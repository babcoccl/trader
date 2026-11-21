"""Five-minute evaluation cycle (spec 1.5).

Every five minutes the engine inspects the latest 5m candles, computes the
hourly sigma and the sudden-drop quote per asset, measures the distance to the
nearest open plan's stop, and writes exactly one ``decisions`` row with
``cycle="intraday"`` — including when it does nothing. It places nothing; dip
bids are parked until spec 5.5.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS
from trader.ledger.models import Decision, Plan, Signal
from trader.strategy import dip_bid

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)
CANDLE_PERIOD = timedelta(minutes=5)
LOOKBACK = timedelta(days=2)  # covers the 289 closes hourly_sigma needs
_OPEN_PLAN_STATUSES = ("pending", "approved")


def _dedupe_sources(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per open_time, from the source covering the most bars."""
    if df.empty or df["source"].nunique() <= 1:
        return df
    preferred = df["source"].value_counts().idxmax()
    df = df.assign(_primary=(df["source"] != preferred))
    df = df.sort_values("_primary", kind="stable")
    df = df[~df.index.duplicated(keep="first")].drop(columns="_primary")
    return df.sort_index()


def _yesterday_ensemble(session: Session, symbol: str, now: datetime) -> float | None:
    yesterday = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=1)
    value = session.scalar(
        select(Signal.value).where(
            Signal.symbol == symbol,
            Signal.day == yesterday.date(),
            Signal.rule == "ensemble",
        )
    )
    return None if value is None else float(value)


def _nearest_stop_distance(session: Session, symbol: str, price: float) -> float | None:
    """Distance from ``price`` to the highest stop of any open plan, as a
    fraction of price. Null until plans exist (spec 2.x)."""
    stop = session.scalar(
        select(func.max(Plan.stop_price)).where(
            Plan.symbol == symbol,
            Plan.status.in_(_OPEN_PLAN_STATUSES),
            Plan.stop_price.is_not(None),
        )
    )
    if stop is None:
        return None
    return float(price - float(stop)) / price


def _finite(x: float | None) -> float | None:
    """JSON details hold no NaN: it becomes null."""
    if x is None or x != x:  # noqa: PLR0124 (NaN != NaN)
        return None
    return x


def _bid_distance(price: float, bid: float) -> float:
    return (price - bid) / price


def run(session: Session, clock: Clock) -> Decision:
    """One intraday evaluation. Writes and returns one decisions row."""
    now = clock.now()

    per_asset: dict[str, dict[str, Any]] = {}
    stale: list[tuple[str, float | None]] = []
    for symbol in SYMBOLS:
        df = _dedupe_sources(
            candles_mod.load(session, symbol, "5m", now - LOOKBACK, now)
        )
        asset: dict[str, Any] = {
            "last_price": None,
            "candle_age_seconds": None,
            "hourly_sigma": None,
            "trend_gate": None,
            "dip_quote": None,
            "nearest_stop_distance": None,
        }
        if not df.empty:
            last_close = float(df["close"].iloc[-1])
            age = (now - (df.index[-1].to_pydatetime() + CANDLE_PERIOD)).total_seconds()
            asset["candle_age_seconds"] = age
            if age <= STALE_AFTER.total_seconds():
                asset["last_price"] = last_close
                sigma = dip_bid.hourly_sigma(df["close"])
                asset["hourly_sigma"] = _finite(sigma)
                if sigma == sigma:  # noqa: PLR0124 (NaN != NaN)
                    q = dip_bid.quote(last_close, sigma)
                    asset["dip_quote"] = {
                        "bid": q.bid,
                        "target": q.target,
                        "stop": q.stop,
                    }
                ensemble = _yesterday_ensemble(session, symbol, now)
                if ensemble is not None:
                    asset["trend_gate"] = dip_bid.trend_gate(ensemble)
                asset["nearest_stop_distance"] = _nearest_stop_distance(
                    session, symbol, last_close
                )
                per_asset[symbol] = asset
                continue
            stale.append((symbol, age))
        else:
            stale.append((symbol, None))
        per_asset[symbol] = asset

    if stale:
        parts = ", ".join(
            f"{s} last candle {age:.0f}s old"
            if age is not None
            else f"{s} has no candles"
            for s, age in stale
        )
        row = Decision(
            cycle="intraday",
            ran_at=now,
            action="blocked",
            reason=f"5-minute data is stale ({parts}); no evaluation made.",
            details={"per_asset": per_asset},
        )
        session.add(row)
        session.flush()
        return row

    prices = ", ".join(
        f"{s.split('-')[0]} {per_asset[s]['last_price']:,.0f}" for s in SYMBOLS
    )
    stops = {
        s: d
        for s in SYMBOLS
        if (d := per_asset[s]["nearest_stop_distance"]) is not None
    }
    if stops:
        nearest = max(stops, key=lambda s: stops[s])
        stop_part = (
            f"Nearest stop is {stops[nearest]:.1%} under {nearest.split('-')[0]}."
        )
    else:
        stop_part = "No stop is near."
    bids = {s: q for s in SYMBOLS if (q := per_asset[s]["dip_quote"]) is not None}
    if len(bids) == len(SYMBOLS):
        bid_part = (
            "Sudden-drop bid would sit "
            + " and ".join(
                f"{_bid_distance(per_asset[s]['last_price'], bids[s]['bid']):.1%} "
                f"under {s.split('-')[0]}"
                for s in SYMBOLS
            )
            + "."
        )
    else:
        bid_part = "Not enough 5-minute history for a drop-bid quote."
    reason = f"Checked prices. {prices}. {stop_part} {bid_part} Nothing to do."

    row = Decision(
        cycle="intraday",
        ran_at=now,
        action="none",
        reason=reason,
        details={"per_asset": per_asset},
    )
    session.add(row)
    session.flush()
    return row
