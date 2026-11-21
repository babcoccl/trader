"""Daily decision cycle (spec 1.4).

Once a day (scheduled for 00:05 UTC) compute the ensemble target weights from
daily candles up to the day that just closed and write one ``decisions`` row
per asset with a plain-English reason. Plans and orders are out of scope until
spec 2.2, so a warranted trade is recorded as ``plan_proposed`` only.

``run`` is idempotent per UTC day: if daily rows for today already exist it
returns them and writes nothing.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS
from trader.ledger.models import Decision, Signal
from trader.strategy import ensemble, rules, sizing

log = logging.getLogger(__name__)

BAND = 0.20
VOL_TARGET = 0.40


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
    """Today's successful daily rows. ``blocked``/``error`` rows do not count:
    a day blocked by missing candles (e.g. before the collector's first
    backfill commits) must still get its real decisions once data arrives."""
    return list(
        session.execute(
            select(Decision)
            .where(
                Decision.cycle == "daily",
                Decision.ran_at >= today,
                Decision.ran_at < today + timedelta(days=1),
                Decision.action.notin_(("blocked", "error")),
            )
            .order_by(Decision.id)
        ).scalars()
    )


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
        }

    day = yesterday.date()
    for symbol, a in per_asset.items():
        for name, value in a["rules"].items():
            session.add(
                Signal(symbol=symbol, day=day, rule=name, value=Decimal(str(value)))
            )
        session.add(
            Signal(
                symbol=symbol, day=day, rule="ensemble", value=Decimal(str(a["signal"]))
            )
        )

    weights = sizing.target_weights(
        {s: a["signal"] for s, a in per_asset.items()},
        {s: a["vol"] for s, a in per_asset.items()},
        vol_target=VOL_TARGET,
    )

    decisions: list[Decision] = []
    for symbol in SYMBOLS:
        a = per_asset[symbol]
        target = weights[symbol]
        current = portfolio.weights.get(symbol, 0.0)
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
        decisions.append(
            Decision(
                cycle="daily",
                ran_at=now,
                symbol=symbol,
                target_weight=Decimal(str(target)),
                current_weight=Decimal(str(current)),
                action=action,
                reason=reason,
                details={
                    "rules": {n: _finite(v) for n, v in a["rules"].items()},
                    "annual_vol": _finite(a["vol"]),
                    "raw_weight": _finite(a["signal"] / len(SYMBOLS)),
                    "scaled_weight": target,
                },
            )
        )
    session.add_all(decisions)
    session.flush()
    return decisions
