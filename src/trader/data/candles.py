"""Candle persistence: upsert, load and gap detection against the `candles` table."""

from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from trader.data.sources.base import Candle, is_closed, period_seconds
from trader.ledger.models import Candle as CandleRow


def upsert(
    session: Session, candles: list[Candle], *, now: datetime | None = None
) -> int:
    """Insert candles, replacing any row on the unique key. Returns rows written.

    Candles whose period has not ended by ``now`` (when given) are skipped —
    the collector always passes its clock so a still-forming candle is never
    stored.
    """
    closed = [c for c in candles if now is None or is_closed(c, now)]
    if not closed:
        return 0
    rows = [
        {
            "symbol": c.symbol,
            "interval": c.interval,
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "source": c.source,
        }
        for c in closed
    ]
    # Postgres allows 65535 bind parameters; 10 columns -> stay under 6000 rows.
    for i in range(0, len(rows), 5000):
        batch = rows[i : i + 5000]
        stmt = insert(CandleRow).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="candles_symbol_interval_open_time_source_key",
            set_={
                col: stmt.excluded[col]
                for col in ("open", "high", "low", "close", "volume")
            },
        )
        session.execute(stmt)
    return len(closed)


def load(
    session: Session,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    source: str | None = None,
) -> pd.DataFrame:
    """Candles with ``open_time`` in ``[start, end)``, indexed by ``open_time``."""
    stmt = (
        select(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.interval == interval,
            CandleRow.open_time >= start,
            CandleRow.open_time < end,
        )
        .order_by(CandleRow.open_time)
    )
    if source is not None:
        stmt = stmt.where(CandleRow.source == source)
    rows = session.execute(stmt).scalars().all()
    df = pd.DataFrame(
        [
            {
                "open_time": r.open_time,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume),
                "source": r.source,
            }
            for r in rows
        ]
    )
    if df.empty:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume", "source"]
        ).rename_axis("open_time")
    return df.set_index("open_time")


def find_gaps(
    session: Session,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Missing periods in ``[start, end)`` as ``(first_open, last_open)`` runs.

    A period counts when its open_time is >= start and it closes at or before
    ``end``. Contiguous missing periods merge into one run.
    """
    step = timedelta(seconds=period_seconds(interval))
    first = _floor(start, step)
    have = {
        _floor(t, step)
        for (t,) in session.execute(
            select(CandleRow.open_time).where(
                CandleRow.symbol == symbol,
                CandleRow.interval == interval,
                CandleRow.open_time >= first,
                CandleRow.open_time <= end - step,
            )
        )
    }
    gaps: list[tuple[datetime, datetime]] = []
    run_start: datetime | None = None
    t = first
    while t + step <= end:
        if t not in have:
            run_start = t if run_start is None else run_start
        elif run_start is not None:
            gaps.append((run_start, t - step))
            run_start = None
        t += step
    if run_start is not None:
        gaps.append((run_start, t - step))
    return gaps


def _floor(t: datetime, step: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    s = int(step.total_seconds())
    return epoch + timedelta(seconds=(int(t.timestamp()) // s) * s)
