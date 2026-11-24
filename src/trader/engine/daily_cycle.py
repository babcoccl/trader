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

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP, COINBASE_TIER_1
from trader.core.clock import Clock
from trader.core.config import get_settings
from trader.data import candles as candles_mod
from trader.data.sources.base import ASSETS, SYMBOLS
from trader.ledger.models import Candle, Decision, Signal
from trader.plans import builder
from trader.risk import engine as risk_engine
from trader.risk.limits import Limits
from trader.strategy import ensemble, momentum, regime, rules, sizing, sleeves

log = logging.getLogger(__name__)

BAND = 0.20
VOL_TARGET = 0.40
# How many daily runs one UTC day may make before waiting for the next
# day's 00:05 job (spec R4): a blocked day retries every 5 minutes.
MAX_ATTEMPTS = 12


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

    True only when ``clock.now()`` is at or after 00:05 UTC and either
    today has no daily decision rows at all — the scheduled run was
    missed entirely — or every one of today's rows is ``blocked`` or
    ``error`` and the rows span fewer than ``MAX_ATTEMPTS`` distinct
    runs, and trading is not halted.
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
    if risk_engine.state(session, clock).halted:
        return False
    if not rows:
        return True  # the 00:05 run never happened: recover it
    if any(action not in ("blocked", "error") for action, _ in rows):
        return False
    if len({ran_at for _, ran_at in rows}) >= MAX_ATTEMPTS:
        return False
    return True


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
            "close": close,
            "levels": rules.levels(close),
            # Recorded and shown only (spec G1): never enters sizing,
            # should_trade, the plan builder or the risk check.
            "regime": regime.label(
                sum(1 for v in rule_values.values() if v == 1.0),
                df["high"],
                df["low"],
                close,
            ),
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

    settings = get_settings()
    strategy = settings.strategy
    if strategy == "cross_sectional_momentum":
        # Spec W1.2a (Run 5): 60d lookback, top 3, 0.55 vol target, 0.30 band,
        # BTC SMA-50 gate.
        band = 0.30
        closes = pd.DataFrame({s: a["close"] for s, a in per_asset.items()})
        weights = momentum.target_weights(
            closes, vol_target=0.55, lookback_days=60, top_k=3, btc_gate=True
        )
        combined = sleeves.Combined(
            weights=weights,
            parts={
                s: [
                    sleeves.SleevePart(
                        name="cross_sectional_momentum",
                        allowed=True,
                        proposed=w,
                        weight=w,
                    )
                ]
                for s, w in weights.items()
            },
        )
    else:
        band = BAND
        weights = sizing.target_weights(
            {s: a["signal"] for s, a in per_asset.items()},
            {s: a["vol"] for s, a in per_asset.items()},
            vol_target=VOL_TARGET,
        )
        # Spec G2: the target is the sum of the sleeves allowed in each
        # asset's regime. The trend sleeve trades in every regime, so these
        # weights are the same numbers sizing produced.
        combined = sleeves.combine(
            {"trend": weights},
            {s: a["regime"] for s, a in per_asset.items()},
        )
    # Venue-aware fee estimate for proposed plans (spec W1.2a).
    cost = COINBASE_TIER_1 if settings.venue == "coinbase" else BITSTAMP
    limits = Limits.load(session)
    risk_state = risk_engine.state(session, clock)

    decisions: list[Decision] = []
    for symbol in SYMBOLS:
        a = per_asset[symbol]
        target = combined.weights[symbol]
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
            f"{a['regime'].text()}. "
            f"Target {target:.0%}, holding {current:.0%}."
        )
        if sizing.should_trade(target, current, band=band):
            side = "buy" if target > current else "sell"
            action = "plan_proposed"
            reason = f"{prefix} Proposing a plan to {side} the difference."
        elif target == current:
            action = "none"
            reason = f"{prefix} No change."
        else:
            action = "none"
            reason = f"{prefix} Gap is under the {band:.0%} trading band, so no trade."
        decision = Decision(
            cycle="daily",
            ran_at=now,
            symbol=symbol,
            target_weight=Decimal(str(target)),
            current_weight=Decimal(str(current)),
            action=action,
            reason=reason,
            details={
                "strategy": strategy,
                "rules": {n: _finite(v) for n, v in a["rules"].items()},
                "last_close": _finite(float(a["last_close"])),
                "levels": {n: _finite(v) for n, v in a["levels"].items()},
                "annual_vol": _finite(a["vol"]),
                "raw_weight": _finite(a["signal"] / len(SYMBOLS)),
                "scaled_weight": target,
                "regime": a["regime"].as_details(),
                "sleeves": [
                    {
                        "name": p.name,
                        "mode": "active",
                        "allowed": p.allowed,
                        "proposed": _finite(p.proposed),
                        "weight": _finite(p.weight),
                    }
                    for p in combined.parts[symbol]
                ],
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
                cost,
                clock,
                rules_positive=a["k"],
                tick=ASSETS[symbol].tick,
                lot=ASSETS[symbol].lot,
                band=band,
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
