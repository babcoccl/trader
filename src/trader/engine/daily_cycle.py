"""Daily decision cycle (spec 1.4 + 2.2 wiring).

Once a day (scheduled for 00:05 UTC) compute the ensemble target weights from
daily candles up to the day that just closed and write one ``decisions`` row
per asset with a plain-English reason. When the trading band says trade,
spec 2.2 turns the weight change into a plan: ``plans.builder.build`` makes
the row, ``risk.check_plan`` can veto it, and the decision is recorded as
``plan_proposed`` (plan saved) or ``blocked`` (vetoed, reasons in ``reason``).

``run`` is idempotent per UTC day: if daily rows for today already exist it
returns them and writes nothing.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS
from trader.ledger.models import Candle, Decision, Signal
from trader.plans import builder
from trader.risk import engine as risk_engine
from trader.risk.limits import Limits
from trader.strategy import ensemble, rules, sizing

log = logging.getLogger(__name__)

BAND = 0.20
VOL_TARGET = 0.40
# How many daily runs one UTC day may make before waiting for the next
# day's 00:05 job (spec R4): a blocked day retries every 5 minutes.
MAX_ATTEMPTS = 12
# Bitstamp market rules until the executor reads them from ``/markets/``
# (spec 2.4): counter_decimals=2, base_decimals=8 for btcusd and ethusd.
VENUE_TICK = Decimal("0.01")
VENUE_LOT = Decimal("0.00000001")


class PortfolioView(Protocol):
    equity_usd: Decimal
    weights: dict[str, float]


@dataclass
class CashOnlyPortfolio:
    """Stub until spec 2.3 (ledger accounting) exists: 100% cash."""

    equity_usd: Decimal = Decimal("0")
    weights: dict[str, float] = field(
        default_factory=lambda: {symbol: 0.0 for symbol in SYMBOLS}
    )


def _day_start(t: datetime) -> datetime:
    return datetime(t.year, t.month, t.day, tzinfo=UTC)


def _existing_today(session: Session, today: datetime) -> list[Decision]:
    """Today's daily rows once the day produced a real decision.

    A day whose rows are all ``blocked``/``error`` did not complete: missing
    candles (e.g. before the collector's first backfill commits) or a risk
    veto that may lift later, so the cycle retries. Once any row is
    ``none``/``plan_proposed`` the day is done and every row is returned,
    including a ``blocked`` row from a vetoed sibling asset.
    """
    rows = list(
        session.execute(
            select(Decision)
            .where(
                Decision.cycle == "daily",
                Decision.ran_at >= today,
                Decision.ran_at < today + timedelta(days=1),
            )
            .order_by(Decision.id)
        ).scalars()
    )
    if any(r.action not in ("blocked", "error") for r in rows):
        return rows
    return []


def needs_retry(session: Session, clock: Clock) -> bool:
    """Today's daily cycle still owes a decision, so it should run again.

    True only when ``clock.now()`` is at or after 00:05 UTC, today has at
    least one daily decision row, every one of today's daily rows is
    ``blocked`` or ``error``, the rows span fewer than ``MAX_ATTEMPTS``
    distinct runs, and trading is not halted.
    """
    now = clock.now()
    today = _day_start(now)
    if now < today + timedelta(minutes=5):
        return False
    rows = session.execute(
        select(Decision.action, Decision.ran_at).where(
            Decision.cycle == "daily",
            Decision.ran_at >= today,
            Decision.ran_at < today + timedelta(days=1),
        )
    ).all()
    if not rows:
        return False
    if any(action not in ("blocked", "error") for action, _ in rows):
        return False
    if len({ran_at for _, ran_at in rows}) >= MAX_ATTEMPTS:
        return False
    return not risk_engine.state(session, clock).halted


def _finite(x: float) -> float | None:
    """JSON details hold no NaN: it becomes null."""
    return x if x == x else None  # noqa: PLR0124 (NaN != NaN)


def run(
    session: Session,
    clock: Clock,
    portfolio: PortfolioView,
) -> list[Decision]:
    """One daily decision cycle. Returns the day's decision rows."""
    now = clock.now()
    today = _day_start(now)
    yesterday = today - timedelta(days=1)

    existing = _existing_today(session, today)
    if existing:
        return existing

    per_asset: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        df = candles_mod.load(
            session, symbol, "1d", datetime(1970, 1, 1, tzinfo=UTC), today
        )
        # One day may exist from several venues (unique key includes source):
        # keep one row per open_time, from the source covering the most days.
        if df["source"].nunique() > 1:
            preferred = df["source"].value_counts().idxmax()
            df = df.assign(_primary=(df["source"] != preferred))
            df = df.sort_values("_primary", kind="stable")
            df = df[~df.index.duplicated(keep="first")].drop(columns="_primary")
            df = df.sort_index()
        if yesterday not in df.index:
            reason = f"Yesterday's {symbol} candle is missing; no decision made."
            row = Decision(
                cycle="daily",
                ran_at=now,
                symbol=symbol,
                action="blocked",
                reason=reason,
                details={"missing_day": yesterday.date().isoformat()},
            )
            session.add(row)
            session.flush()
            return [row]
        close = df["close"]
        rule_values = {
            name: float(s.iloc[-1]) for name, s in rules.all_rules(close).items()
        }
        sig = float(ensemble.signal(close).iloc[-1])
        vol = float(sizing.annual_vol(close).iloc[-1])
        per_asset[symbol] = {
            "rules": rule_values,
            "signal": sig,
            "vol": vol,
            "k": sum(1 for v in rule_values.values() if v == 1.0),
            "last_close": close.iloc[-1],
            "levels": rules.levels(close),
        }

    day = yesterday.date()
    # A retried day (all rows blocked) already stored these; re-inserting
    # would violate signals' unique (symbol, day, rule).
    signals_done = session.scalar(
        select(func.count()).select_from(Signal).where(Signal.day == day)
    )
    if not signals_done:
        for symbol, a in per_asset.items():
            for name, value in a["rules"].items():
                session.add(
                    Signal(symbol=symbol, day=day, rule=name, value=Decimal(str(value)))
                )
            session.add(
                Signal(
                    symbol=symbol,
                    day=day,
                    rule="ensemble",
                    value=Decimal(str(a["signal"])),
                )
            )

    weights = sizing.target_weights(
        {s: a["signal"] for s, a in per_asset.items()},
        {s: a["vol"] for s, a in per_asset.items()},
        vol_target=VOL_TARGET,
    )
    limits = Limits.load(session)
    risk_state = risk_engine.state(session, clock)

    decisions: list[Decision] = []
    for symbol in SYMBOLS:
        a = per_asset[symbol]
        target = weights[symbol]
        current = portfolio.weights.get(symbol, 0.0)
        # Size the plan at the freshest stored close (what the portfolio is
        # marked with), not yesterday's daily close: a higher latest price
        # would otherwise make an exit request more coins than held.
        plan_price = session.scalar(
            select(Candle.close)
            .where(Candle.symbol == symbol)
            .order_by(Candle.open_time.desc())
            .limit(1)
        ) or Decimal(str(a["last_close"]))
        prefix = (
            f"Checked {symbol}. {a['k']} of 9 rules positive. "
            f"Target {target:.0%}, holding {current:.0%}."
        )
        if sizing.should_trade(target, current, band=BAND):
            side = "buy" if target > current else "sell"
            action = "plan_proposed"
            reason = f"{prefix} Proposing a plan to {side} the difference."
        elif target == current:
            action = "none"
            reason = f"{prefix} No change."
        else:
            action = "none"
            reason = f"{prefix} Gap is under the 20% trading band, so no trade."
        decision = Decision(
            cycle="daily",
            ran_at=now,
            symbol=symbol,
            target_weight=Decimal(str(target)),
            current_weight=Decimal(str(current)),
            action=action,
            reason=reason,
            details={
                "rules": {n: _finite(v) for n, v in a["rules"].items()},
                "last_close": _finite(float(a["last_close"])),
                "levels": {n: _finite(v) for n, v in a["levels"].items()},
                "annual_vol": _finite(a["vol"]),
                "raw_weight": _finite(a["signal"] / len(SYMBOLS)),
                "scaled_weight": target,
            },
        )
        session.add(decision)
        session.flush()  # plan.decision_id needs the row id
        if action == "plan_proposed":
            plan = builder.build(
                symbol,
                target,
                current,
                portfolio.equity_usd,
                plan_price,
                a["vol"],
                BITSTAMP,
                clock,
                rules_positive=a["k"],
                tick=VENUE_TICK,
                lot=VENUE_LOT,
            )
            if plan is not None:
                verdict = risk_engine.check_plan(plan, portfolio, limits, risk_state)
                if verdict.ok:
                    plan.decision_id = decision.id
                    session.add(plan)
                    session.flush()
                else:
                    decision.action = "blocked"
                    decision.reason = (
                        f"{prefix} Trade blocked by risk: {' '.join(verdict.reasons)}"
                    )
                    session.flush()
        decisions.append(decision)
    return decisions
