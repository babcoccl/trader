"""Candle storage tests: upsert idempotency, load, find_gaps refill, and the
unfinished-candle rule. Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.data import candles as candles_mod
from trader.data.sources.base import Candle
from trader.ledger.models import Candle as CandleRow

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

DAY0 = datetime(2026, 1, 1, tzinfo=UTC)


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture()
def session():
    assert TEST_URL is not None
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        s.execute(delete(CandleRow))
        s.commit()
        yield s
        s.execute(delete(CandleRow))
        s.commit()
    engine.dispose()


def _candle(day: int, close: str = "100", interval: str = "1d") -> Candle:
    return Candle(
        symbol="BTC-USD",
        interval=interval,
        open_time=DAY0 + timedelta(days=day),
        open=Decimal("99"),
        high=Decimal("101"),
        low=Decimal("98"),
        close=Decimal(close),
        volume=Decimal("10"),
        source="test",
    )


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CandleRow)) or 0


def test_upsert_twice_same_row_count(session: Session) -> None:
    candles = [_candle(d) for d in range(3)]
    assert candles_mod.upsert(session, candles) == 3
    session.commit()
    assert candles_mod.upsert(session, candles) == 3
    session.commit()
    assert _count(session) == 3


def test_upsert_updates_changed_values(session: Session) -> None:
    candles_mod.upsert(session, [_candle(0, close="100")])
    session.commit()
    candles_mod.upsert(session, [_candle(0, close="200")])
    session.commit()
    assert _count(session) == 1
    row = session.execute(select(CandleRow)).scalar_one()
    assert row.close == Decimal("200")


def test_unfinished_candle_rejected(session: Session) -> None:
    now = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)
    open_candle = Candle(
        symbol="BTC-USD",
        interval="1d",
        open_time=datetime(2026, 1, 3, tzinfo=UTC),  # period ends 2026-01-04
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        source="test",
    )
    assert candles_mod.upsert(session, [open_candle], now=now) == 0
    session.commit()
    assert _count(session) == 0


def test_load_indexed_by_open_time(session: Session) -> None:
    candles_mod.upsert(session, [_candle(d) for d in range(3)])
    session.commit()
    df = candles_mod.load(session, "BTC-USD", "1d", DAY0, DAY0 + timedelta(days=10))
    assert list(df.index) == [DAY0 + timedelta(days=d) for d in range(3)]
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "source"]
    assert df.loc[DAY0, "close"] == 100.0


def test_find_gaps_detects_and_refills_deleted_day(session: Session) -> None:
    candles_mod.upsert(session, [_candle(d) for d in range(5)])
    session.commit()
    session.execute(
        delete(CandleRow).where(CandleRow.open_time == DAY0 + timedelta(days=2))
    )
    session.commit()

    start, end = DAY0, DAY0 + timedelta(days=5)
    gaps = candles_mod.find_gaps(session, "BTC-USD", "1d", start, end)
    assert gaps == [(DAY0 + timedelta(days=2), DAY0 + timedelta(days=2))]

    # Refill the gap the way the collector does.
    for _gap in gaps:
        candles_mod.upsert(session, [_candle(2)])
    session.commit()
    assert candles_mod.find_gaps(session, "BTC-USD", "1d", start, end) == []


def test_find_gaps_coalesces_a_run(session: Session) -> None:
    candles_mod.upsert(session, [_candle(0), _candle(4)])
    session.commit()
    gaps = candles_mod.find_gaps(
        session, "BTC-USD", "1d", DAY0, DAY0 + timedelta(days=5)
    )
    assert gaps == [(DAY0 + timedelta(days=1), DAY0 + timedelta(days=3))]


def test_find_gaps_5m(session: Session) -> None:
    t0 = DAY0
    session.add(
        CandleRow(
            symbol="BTC-USD",
            interval="5m",
            open_time=t0,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
            source="test",
        )
    )
    session.commit()
    gaps = candles_mod.find_gaps(
        session, "BTC-USD", "5m", t0, t0 + timedelta(minutes=15)
    )
    assert gaps == [(t0 + timedelta(minutes=5), t0 + timedelta(minutes=10))]
