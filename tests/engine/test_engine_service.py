"""Engine service tests: blocked daily cycle retries (spec R4).
Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS, Candle
from trader.engine.__main__ import Engine
from trader.ledger import accounting
from trader.ledger.models import (
    AuditLog,
    Balance,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    Heartbeat,
    Order,
    Plan,
    Setting,
    Signal,
)
from trader.ledger.models import Candle as CandleRow

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "research",
    "fixtures",
    "btc_eth_daily.csv",
)


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


def _load_fixture_candles() -> list[Candle]:
    df = pd.read_csv(FIXTURE, parse_dates=["date"])
    candles: list[Candle] = []
    for _, row in df.iterrows():
        open_time = row["date"].to_pydatetime().replace(tzinfo=UTC)
        for symbol in SYMBOLS:
            o = Decimal(str(row[f"{symbol}_open"]))
            c = Decimal(str(row[f"{symbol}_close"]))
            candles.append(
                Candle(
                    symbol=symbol,
                    interval="1d",
                    open_time=open_time,
                    open=o,
                    high=max(o, c),
                    low=min(o, c),
                    close=c,
                    volume=Decimal("0"),
                    source="fixture",
                )
            )
    return candles


@pytest.fixture()
def engine():
    assert TEST_URL is not None
    eng = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(eng) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Signal,
            CandleRow,
            Setting,
            AuditLog,
            EquitySnapshot,
            Balance,
            Event,
            Heartbeat,
        ):
            s.execute(delete(table))
        s.commit()
    yield eng
    eng.dispose()


def _session_factory(engine):
    """Commit-on-exit sessions, like ``core.db.session_scope``."""

    @contextmanager
    def scope():
        with Session(engine) as s:
            yield s
            s.commit()

    return scope


def _decision_count(engine) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(Decision)) or 0


def test_blocked_day_retries_once_data_arrives(engine) -> None:
    """The 00:05 run blocks on missing candles; a retry minutes later
    writes the day's decisions; a further retry writes nothing."""
    day = datetime(2026, 11, 20, tzinfo=UTC)
    clock = FrozenClock(day + timedelta(minutes=5))
    service = Engine(clock=clock, session_factory=_session_factory(engine))

    # First start: no candles stored yet, the cycle blocks.
    service.run_daily()
    assert _decision_count(engine) == 1
    with Session(engine) as s:
        row = s.scalars(select(Decision)).one()
        assert row.action == "blocked"

    # Collector backfill lands; candles and a fresh 5m candle arrive.
    with Session(engine) as s:
        candles_mod.upsert(s, _load_fixture_candles())
        candles_mod.upsert(
            s,
            [
                Candle(
                    symbol=symbol,
                    interval="5m",
                    open_time=clock.now() - timedelta(minutes=5),
                    open=Decimal("1"),
                    high=Decimal("1"),
                    low=Decimal("1"),
                    close=Decimal("1"),
                    volume=Decimal("0"),
                    source="fixture",
                )
                for symbol in SYMBOLS
            ],
        )
        accounting.deposit(s, "paper", Decimal("10000"), clock)
        s.commit()

    # Five minutes later the retry job writes normal decisions per symbol.
    clock.advance(timedelta(minutes=5))
    service.retry_daily()
    with Session(engine) as s:
        rows = s.scalars(
            select(Decision).where(Decision.cycle == "daily").order_by(Decision.id)
        ).all()
        assert len(rows) == 3
        retried = rows[1:]
        assert {r.symbol for r in retried} == set(SYMBOLS)
        assert all(r.action != "blocked" for r in retried)
        assert all(r.ran_at == clock.now() for r in retried)

    # The day now has real decisions: another retry writes nothing.
    clock.advance(timedelta(minutes=5))
    service.retry_daily()
    assert _decision_count(engine) == 3


def test_past_daily_time_gates_startup_catchup() -> None:
    """Before 00:05 UTC a restart waits for the cron run; after it the
    catch-up fills a missed run (spec D2: a new asset's first decision
    comes at the next 00:05 UTC, not at restart time)."""
    from trader.engine.__main__ import _past_daily_time

    day = datetime(2026, 11, 23, tzinfo=UTC)
    assert not _past_daily_time(day)  # 00:00
    assert not _past_daily_time(day + timedelta(minutes=4, seconds=59))
    assert _past_daily_time(day + timedelta(minutes=5))
    assert _past_daily_time(day + timedelta(hours=10))
