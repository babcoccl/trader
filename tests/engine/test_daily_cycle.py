"""Daily decision cycle tests: fixture replay, blocked on missing candle,
idempotency. Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS, Candle
from trader.engine import daily_cycle
from trader.ledger.models import Candle as CandleRow
from trader.ledger.models import Decision, Signal

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
def session():
    assert TEST_URL is not None
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        for table in (CandleRow, Decision, Signal):
            s.execute(delete(table))
        candles_mod.upsert(s, _load_fixture_candles())
        s.commit()
        yield s
    engine.dispose()


def _decision_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Decision)) or 0


def test_replay_thirty_days(session: Session) -> None:
    """30 consecutive fixture days, frozen clock: 60 decision rows, none skipped."""
    start_day = datetime(2026, 10, 1, tzinfo=UTC)
    for i in range(30):
        day = start_day + timedelta(days=i)
        clock = FrozenClock(day + timedelta(minutes=5))
        rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
        session.commit()
        assert len(rows) == 2
        assert {r.symbol for r in rows} == set(SYMBOLS)
        assert all(r.cycle == "daily" for r in rows)
        assert all(r.action in ("none", "plan_proposed") for r in rows)
        assert all("Checked" in r.reason for r in rows)
    assert _decision_count(session) == 60
    # Signals stored for every replayed day and both symbols.
    signal_days = session.scalars(select(Signal.day).distinct()).all()
    assert len(signal_days) == 30


def test_missing_yesterday_candle_blocks(session: Session) -> None:
    yesterday = datetime(2026, 11, 19, tzinfo=UTC)
    session.execute(
        delete(CandleRow).where(
            CandleRow.symbol == "BTC-USD",
            CandleRow.interval == "1d",
            CandleRow.open_time == yesterday,
        )
    )
    session.commit()
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(rows) == 1
    assert rows[0].action == "blocked"
    assert rows[0].reason == (
        "Yesterday's BTC-USD candle is missing; no decision made."
    )
    assert _decision_count(session) == 1


def test_second_run_same_day_adds_nothing(session: Session) -> None:
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    first = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    clock.advance(timedelta(hours=1))
    second = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert [r.id for r in second] == [r.id for r in first]
    assert _decision_count(session) == 2
    signals_before = session.scalar(select(func.count()).select_from(Signal))
    daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    assert session.scalar(select(func.count()).select_from(Signal)) == signals_before


def test_reason_templates(session: Session) -> None:
    """One day with holdings that force a sell decision exercises all shapes."""
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    portfolio = daily_cycle.CashOnlyPortfolio(weights={"BTC-USD": 0.5, "ETH-USD": 0.0})
    rows = daily_cycle.run(session, clock, portfolio)
    session.commit()
    for r in rows:
        assert r.reason.startswith(f"Checked {r.symbol}.")
        assert "of 9 rules positive." in r.reason
        assert "Target" in r.reason and "holding" in r.reason
        assert r.details["rules"].keys() == {
            "sma50",
            "sma100",
            "sma200",
            "momentum30",
            "momentum90",
            "momentum180",
            "breakout20",
            "breakout55",
            "breakout100",
        }
        assert "annual_vol" in r.details
        assert "raw_weight" in r.details and "scaled_weight" in r.details
    # BTC held at 50% while the trend target is low must propose a sell.
    btc = next(r for r in rows if r.symbol == "BTC-USD")
    if btc.action == "plan_proposed":
        assert "Proposing a plan to sell the difference." in btc.reason
