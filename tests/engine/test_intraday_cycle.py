"""Intraday evaluation cycle tests: one row per run, blocked on stale data,
details hold the per-asset evaluation. Needs TEST_DATABASE_URL."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS, Candle
from trader.engine import intraday_cycle
from trader.ledger.models import Candle as CandleRow
from trader.ledger.models import Decision, Plan, Signal
from trader.strategy import dip_bid

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 20, 12, 0, 10, tzinfo=UTC)


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


def _five_min_candles(
    symbol: str, end_close: datetime, n: int, base: float
) -> list[Candle]:
    """``n`` consecutive 5m candles ending with the bar opening at
    ``end_close``, prices a deterministic mild random walk."""
    rng = np.random.default_rng(sum(map(ord, symbol)))
    rets = rng.normal(0.0002, 0.001, n)
    closes = base * np.exp(np.cumsum(rets))
    candles: list[Candle] = []
    for i in range(n):
        open_time = end_close - timedelta(minutes=5 * (n - 1 - i))
        c = Decimal(str(round(closes[i], 2)))
        candles.append(
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=open_time,
                open=c,
                high=c,
                low=c,
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
        for table in (Plan, CandleRow, Decision, Signal):
            s.execute(delete(table))
        # Last closed bar opens at the 5-minute boundary just under NOW.
        last_open = NOW.replace(second=0, microsecond=0) - timedelta(minutes=5)
        for i, symbol in enumerate(SYMBOLS):
            candles_mod.upsert(
                s, _five_min_candles(symbol, last_open, 289, 60000 + i * 1000)
            )
        s.commit()
        yield s
    engine.dispose()


def _decision_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(Decision.cycle == "intraday")
        )
        or 0
    )


def test_one_row_per_run(session: Session) -> None:
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    assert row.cycle == "intraday"
    assert row.action == "none"
    assert row.reason.startswith("Checked prices.")
    assert "Nothing to do." in row.reason
    assert _decision_count(session) == 1


def test_details_per_asset(session: Session) -> None:
    session.add(
        Signal(
            symbol="BTC-USD",
            day=(NOW - timedelta(days=1)).date(),
            rule="ensemble",
            value=Decimal("0.6"),
        )
    )
    session.commit()
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    per_asset = row.details["per_asset"]
    assert set(per_asset) == set(SYMBOLS)
    for symbol in SYMBOLS:
        a = per_asset[symbol]
        assert a["last_price"] is not None
        # Newest closed bar opened 5 minutes ago: age ~= 10s.
        assert a["candle_age_seconds"] == pytest.approx(10, abs=1)
        assert a["hourly_sigma"] is not None and a["hourly_sigma"] > 0
        assert a["dip_quote"]["bid"] < a["last_price"]
        assert a["dip_quote"]["target"] > a["dip_quote"]["bid"]
        assert a["dip_quote"]["stop"] < a["dip_quote"]["bid"]
        assert a["nearest_stop_distance"] is None  # no plans until 2.x
    assert per_asset["BTC-USD"]["trend_gate"] is True
    assert per_asset["ETH-USD"]["trend_gate"] is None  # no ensemble stored


def test_sigma_matches_function(session: Session) -> None:
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    df = candles_mod.load(
        session,
        "BTC-USD",
        "5m",
        NOW - timedelta(days=2),
        NOW,
    )
    expected = dip_bid.hourly_sigma(df["close"])
    assert row.details["per_asset"]["BTC-USD"]["hourly_sigma"] == pytest.approx(
        expected
    )


def test_nearest_stop_distance(session: Session) -> None:
    """Open plans' stops: details hold per-asset distance and the reason
    names the nearest (smallest-distance) stop, not the farthest."""
    anchor = Decision(
        cycle="daily",
        ran_at=NOW,
        action="none",
        reason="seed",
        details={},
    )
    session.add(anchor)
    session.flush()
    for symbol, stop in (("BTC-USD", "59000"), ("ETH-USD", "1000")):
        session.add(
            Plan(
                sleeve="trend",
                symbol=symbol,
                side="buy",
                status="approved",
                reference_price=Decimal("60000"),
                target_weight=Decimal("0.5"),
                entry_orders=[],
                stop_price=Decimal(stop),
                worst_case_loss=Decimal("0"),
                est_fees=Decimal("0"),
                rationale="seed",
                expires_at=NOW + timedelta(hours=6),
                decision_id=anchor.id,
            )
        )
    session.commit()
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    per_asset = row.details["per_asset"]
    # BTC stop ~1.5% under price; ETH stop ~40% under. Nearest = BTC.
    assert per_asset["BTC-USD"]["nearest_stop_distance"] == pytest.approx(
        (per_asset["BTC-USD"]["last_price"] - 59000)
        / per_asset["BTC-USD"]["last_price"],
        rel=1e-9,
    )
    assert "Nearest stop is" in row.reason
    assert "under BTC." in row.reason


def test_stale_data_blocked(session: Session) -> None:
    session.execute(delete(CandleRow).where(CandleRow.symbol == "ETH-USD"))
    session.commit()
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    assert row.action == "blocked"
    assert "stale" in row.reason
    assert _decision_count(session) == 1


def test_old_candle_blocked(session: Session) -> None:
    """A newest candle older than 10 minutes blocks the whole evaluation."""
    clock = FrozenClock(NOW + timedelta(minutes=11))
    row = intraday_cycle.run(session, clock)
    session.commit()
    assert row.action == "blocked"
    assert "stale" in row.reason


def test_short_history_null_quote(session: Session) -> None:
    """Under 288 returns there is no sigma and no quote, but still one row."""
    session.execute(delete(CandleRow))
    session.commit()
    last_open = NOW.replace(second=0, microsecond=0) - timedelta(minutes=5)
    for symbol in SYMBOLS:
        candles_mod.upsert(session, _five_min_candles(symbol, last_open, 100, 100))
    session.commit()
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    assert row.action == "none"
    for symbol in SYMBOLS:
        a = row.details["per_asset"][symbol]
        assert a["hourly_sigma"] is None
        assert a["dip_quote"] is None
    assert "Not enough 5-minute history" in row.reason


def test_empty_table_blocked(session: Session) -> None:
    session.execute(delete(CandleRow))
    session.commit()
    row = intraday_cycle.run(session, FrozenClock(NOW))
    session.commit()
    assert row.action == "blocked"
    for symbol in SYMBOLS:
        assert row.details["per_asset"][symbol]["last_price"] is None
