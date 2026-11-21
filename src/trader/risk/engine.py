"""Risk engine (spec 2.1): the single place that can veto anything.

State (the halted flag, halt reason, flatten request) lives in the
``settings`` table under the ``risk.`` prefix and is written only here.
Equity history lives in ``equity_snapshots``.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.data.sources.base import SYMBOLS
from trader.ledger.models import (
    AuditLog,
    Candle,
    EquitySnapshot,
    Plan,
    Setting,
)
from trader.risk.limits import Limits

EPS = 1e-9


class PortfolioView(Protocol):
    """Same shape as ``engine.daily_cycle.PortfolioView`` and the future
    ``ledger.accounting.portfolio_view`` (spec 2.3)."""

    equity_usd: Decimal
    weights: dict[str, float]


class PlanView(Protocol):
    """What ``check_plan`` needs from a plan (ORM ``Plan`` satisfies it)."""

    symbol: str
    side: str
    target_weight: Decimal
    worst_case_loss: Decimal


@dataclass(frozen=True)
class RiskVerdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RiskState:
    halted: bool
    halt_reason: str | None
    high_water_mark: Decimal | None
    drawdown: float
    # Seconds since the newest closed 5m candle opened, per symbol.
    price_age_seconds: dict[str, float]


def _get(session: Session, key: str) -> object:
    row = session.get(Setting, key)
    return row.value if row is not None else None


def _set(session: Session, key: str, value: object, actor: str, before: object) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_at=func.now()))
    else:
        row.value = value
        row.updated_at = func.now()
    session.add(
        AuditLog(
            time=func.now(),
            actor=actor,
            action=f"set {key}",
            before={key: before},
            after={key: value},
        )
    )


def _set_halted(session: Session, halted: bool, reason: str | None, actor: str) -> None:
    _set(session, "risk.halted", halted, actor, _get(session, "risk.halted"))
    _set(
        session,
        "risk.halt_reason",
        reason,
        actor,
        _get(session, "risk.halt_reason"),
    )


def _latest_snapshot(session: Session, mode: str) -> EquitySnapshot | None:
    return session.scalars(
        select(EquitySnapshot)
        .where(EquitySnapshot.mode == mode)
        .order_by(EquitySnapshot.time.desc(), EquitySnapshot.id.desc())
        .limit(1)
    ).first()


def state(session: Session, clock: Clock | None = None) -> RiskState:
    """Current risk state: halt flag, drawdown, and price-data age."""
    clock = clock or SystemClock()
    now = clock.now()
    halted = bool(_get(session, "risk.halted") or False)
    halt_reason = _get(session, "risk.halt_reason")
    snapshot = _latest_snapshot(session, get_settings().mode)
    price_age: dict[str, float] = {}
    for symbol in SYMBOLS:
        newest = session.scalars(
            select(func.max(Candle.open_time)).where(
                Candle.symbol == symbol, Candle.interval == "5m"
            )
        ).one()
        if newest is not None:
            price_age[symbol] = (now - newest).total_seconds()
    return RiskState(
        halted=halted,
        halt_reason=str(halt_reason) if halt_reason is not None else None,
        high_water_mark=snapshot.high_water_mark if snapshot else None,
        drawdown=float(snapshot.drawdown) if snapshot else 0.0,
        price_age_seconds=price_age,
    )


def check_plan(
    plan: PlanView | Plan,
    portfolio: PortfolioView,
    limits: Limits,
    risk_state: RiskState,
) -> RiskVerdict:
    """Veto check for one plan.

    A sell that only reduces exposure is always allowed, including while
    halted — exits must never be blocked. Everything else goes through every
    check and collects all reasons it fails.
    """
    target = float(plan.target_weight)
    current = portfolio.weights.get(plan.symbol, 0.0)

    if plan.side == "sell" and 0.0 <= target <= current:
        return RiskVerdict(ok=True)

    reasons: list[str] = []

    if target < 0.0:
        reasons.append("Plan would open a short position.")
    elif plan.side == "sell":
        reasons.append("Sell plan does not reduce exposure.")

    if risk_state.halted:
        reasons.append(f"Trading is halted: {risk_state.halt_reason or 'no reason'}.")

    weights_after = dict(portfolio.weights)
    weights_after[plan.symbol] = target
    gross_after = sum(max(w, 0.0) for w in weights_after.values())
    if gross_after > limits.max_gross + EPS:
        reasons.append(
            f"Gross exposure would be {gross_after:.2%}, above the "
            f"{limits.max_gross:.2%} limit."
        )
    if target > limits.max_asset_weight + EPS:
        reasons.append(
            f"{plan.symbol} weight would be {target:.2%}, above the "
            f"{limits.max_asset_weight:.2%} per-asset limit."
        )

    equity = float(portfolio.equity_usd)
    loss_fraction = float(plan.worst_case_loss) / equity if equity > 0 else float("inf")
    if loss_fraction > limits.max_plan_loss_fraction + EPS:
        reasons.append(
            f"Worst-case loss {loss_fraction:.2%} of equity exceeds the "
            f"{limits.max_plan_loss_fraction:.2%} limit."
        )

    age = risk_state.price_age_seconds.get(plan.symbol)
    if age is None or age > limits.stale_candle_seconds:
        reasons.append(f"Price data for {plan.symbol} is stale; no recent candles.")

    return RiskVerdict(ok=not reasons, reasons=reasons)


def update_equity(
    session: Session, equity_usd: Decimal, clock: Clock
) -> EquitySnapshot:
    """Record an equity snapshot; halt when drawdown reaches the limit."""
    mode = get_settings().mode
    prev = _latest_snapshot(session, mode)
    hwm = max(prev.high_water_mark, equity_usd) if prev else equity_usd
    drawdown = max(Decimal(1) - equity_usd / hwm, Decimal(0)) if hwm > 0 else Decimal(0)
    snapshot = EquitySnapshot(
        time=clock.now(),
        mode=mode,
        equity_usd=equity_usd,
        high_water_mark=hwm,
        drawdown=drawdown,
    )
    session.add(snapshot)
    limits = Limits.load(session)
    if drawdown >= Decimal(str(limits.drawdown_halt)):
        _set_halted(
            session,
            True,
            f"Drawdown {drawdown:.1%} reached the {limits.drawdown_halt:.0%} "
            "halt level.",
            actor="risk",
        )
    session.flush()
    return snapshot


def halt(session: Session, actor: str, reason: str) -> None:
    """Manual halt: blocks everything except exposure-reducing sells."""
    _set_halted(session, True, reason, actor)


def resume(session: Session, actor: str) -> None:
    """Clear the halt and reset the high-water mark to current equity."""
    _set_halted(session, False, None, actor)
    snapshot = _latest_snapshot(session, get_settings().mode)
    if snapshot is not None:
        session.add(
            EquitySnapshot(
                # Same `time` as the latest snapshot so this reset row sorts
                # first by id even if that snapshot was future-dated.
                time=snapshot.time,
                mode=snapshot.mode,
                equity_usd=snapshot.equity_usd,
                high_water_mark=snapshot.equity_usd,
                drawdown=Decimal("0"),
            )
        )
    session.flush()


def flatten_requested(session: Session, actor: str) -> None:
    """Raise the flatten flag; the executor (spec 2.4) sells everything."""
    _set(
        session,
        "risk.flatten_requested",
        True,
        actor,
        _get(session, "risk.flatten_requested"),
    )
    session.flush()
