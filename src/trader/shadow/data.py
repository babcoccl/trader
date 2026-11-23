"""Read-only market and regime data for shadow sleeves (spec G3).

The only place shadow code reads prices and regimes. Candle
de-duplication copies the rule ``daily_cycle.run`` uses (one row per
``open_time``, from the source covering the most bars); this module does
not import from ``trader.engine``.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS, period_seconds
from trader.ledger.models import Candle, Decision
from trader.strategy import regime as regime_mod
from trader.strategy.regime import Regime

MAX_PRICE_AGE = timedelta(minutes=15)


def symbols(wanted: tuple[str, ...]) -> tuple[str, ...]:
    """The entries of ``wanted`` that are configured, in ``wanted`` order."""
    return tuple(s for s in wanted if s in SYMBOLS)


def _regime_from_row(row: Decision | None) -> Regime | None:
    if row is None:
        return None
    return regime_mod.from_details((row.details or {}).get("regime") or {})


def _newest_daily_with_regime(
    session: Session, symbol: str, today: datetime | None
) -> Regime | None:
    conditions = [
        Decision.cycle == "daily",
        Decision.symbol == symbol,
        Decision.details["regime"].isnot(None),
    ]
    if today is not None:
        conditions.append(Decision.ran_at >= today)
    row = session.scalars(
        select(Decision)
        .where(*conditions)
        .order_by(Decision.ran_at.desc(), Decision.id.desc())
        .limit(1)
    ).first()
    return _regime_from_row(row)


def regime_today(session: Session, symbol: str, today: datetime) -> Regime | None:
    """Regime from today's newest daily decision for ``symbol``, or None."""
    return _newest_daily_with_regime(session, symbol, today)


def latest_regime(session: Session, symbol: str) -> Regime | None:
    """Regime from the newest daily decision for ``symbol``, or None."""
    return _newest_daily_with_regime(session, symbol, None)


def _dedup(df: pd.DataFrame) -> pd.DataFrame:
    """One row per ``open_time``, from the source covering the most bars.

    Same rule as ``daily_cycle.run``: when several venues stored the same
    bar, the source with the most rows wins.
    """
    if df.empty or df["source"].nunique() <= 1:
        return df
    preferred = df["source"].value_counts().idxmax()
    df = df.assign(_primary=(df["source"] != preferred))
    df = df.sort_values("_primary", kind="stable")
    df = df[~df.index.duplicated(keep="first")].drop(columns="_primary")
    return df.sort_index()


def price(session: Session, symbol: str, now: datetime) -> Decimal | None:
    """Close of the newest stored 5-minute candle, or None when stale.

    Stale means the bar closed more than ``MAX_PRICE_AGE`` before ``now``.
    """
    latest = session.scalar(
        select(Candle.open_time)
        .where(Candle.symbol == symbol, Candle.interval == "5m")
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if latest is None:
        return None
    if latest + timedelta(seconds=period_seconds("5m")) < now - MAX_PRICE_AGE:
        return None
    # Several sources may hold the same bar: the one covering the most
    # bars wins, the same rule as ``daily_closes``/``bars_after``.
    rows = session.scalars(
        select(Candle).where(
            Candle.symbol == symbol,
            Candle.interval == "5m",
            Candle.open_time == latest,
        )
    ).all()
    if len(rows) == 1:
        return rows[0].close
    counts = session.execute(
        select(Candle.source, func.count())
        .where(Candle.symbol == symbol, Candle.interval == "5m")
        .group_by(Candle.source)
    ).all()
    preferred = max(counts, key=lambda row: row[1])[0]
    for r in rows:
        if r.source == preferred:
            return r.close
    return rows[0].close


def daily_closes(session: Session, symbol: str, today: datetime) -> pd.Series:
    """Daily closes with ``open_time < today``, one row per day."""
    df = candles_mod.load(
        session, symbol, "1d", datetime(1970, 1, 1, tzinfo=today.tzinfo), today
    )
    return _dedup(df)["close"]


def bars_after(
    session: Session, symbol: str, after: datetime, now: datetime
) -> pd.DataFrame:
    """Closed 5-minute candles with ``open_time > after``, oldest first."""
    # A bar counts when open_time > after and it closes at or before now,
    # i.e. open_time <= now - 300s.
    df = candles_mod.load(
        session,
        symbol,
        "5m",
        after + timedelta(microseconds=1),
        now - timedelta(seconds=period_seconds("5m")) + timedelta(microseconds=1),
    )
    return _dedup(df)
