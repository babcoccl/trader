"""Ping-pong worker tests (spec W1.3c): multi-level state machine, re-quote
timer, paper fills and the halt path. Needs TEST_DATABASE_URL; the live
ticker is injected, so no network is touched."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import FrozenClock
from trader.core.config import get_settings
from trader.execution.paper import PaperAdapter
from trader.execution.ping_pong import PingPongWorker
from trader.ledger import accounting
from trader.ledger.models import (
    Balance,
    Candle,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    Order,
    Plan,
    Setting,
)
from trader.risk import engine as risk
from trader.strategy import subsidy

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 20, 12, 0, 10, tzinfo=UTC)
_OPEN = ("new", "open", "partially_filled")


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture()
def session(monkeypatch):
    assert TEST_URL is not None
    monkeypatch.setenv("TRADER_DATABASE_URL", TEST_URL)
    get_settings.cache_clear()
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Balance,
            Event,
            Candle,
            Setting,
            EquitySnapshot,
        ):
            s.execute(delete(table))
        s.commit()
        yield s
    engine.dispose()
    get_settings.cache_clear()


def _daily_candles(symbol: str, n: int, base: float) -> list[Candle]:
    """``n`` mild sideways daily candles ending yesterday (ADX < 20)."""
    rng = np.random.default_rng(3)
    rets = rng.normal(0, 0.004, n)
    closes = base * np.exp(np.cumsum(rets))
    day0 = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    candles: list[Candle] = []
    for i in range(n):
        open_time = day0 - timedelta(days=n - i)
        c = Decimal(str(round(closes[i], 2)))
        candles.append(
            Candle(
                symbol=symbol,
                interval="1d",
                open_time=open_time,
                open=c,
                high=c * Decimal("1.001"),
                low=c * Decimal("0.999"),
                close=c,
                volume=Decimal("0"),
                source="fixture",
            )
        )
    return candles


def _enable(monkeypatch, **overrides: str) -> None:
    env = {
        "TRADER_SUBSIDY_ENABLED": "true",
        "TRADER_VENUE": "coinbase",
        "TRADER_SUBSIDY_TARGET_VOLUME_USD": "10000",
        "TRADER_SUBSIDY_DAILY_CAP_USD": "2000",
        "TRADER_SUBSIDY_ADX_MAX": "20",
        "TRADER_SUBSIDY_ORDER_NOTIONAL_USD": "250",
        "TRADER_SUBSIDY_ORDER_LEVELS": "2",
        "TRADER_SUBSIDY_SPREAD_BPS": "5",
        "TRADER_SUBSIDY_REFRESH_SECONDS": "20",
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _active(session: Session, monkeypatch, **overrides: str) -> FrozenClock:
    """Active subsidy sleeve: env on, sideways ADX, funded account."""
    _enable(monkeypatch, **overrides)
    for candle in _daily_candles("BTC-USD", 60, 60000):
        session.add(candle)
    # A fresh 5m candle: risk.check_plan vetoes plans priced on stale data.
    session.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=NOW - timedelta(minutes=5),
            open=Decimal("60000"),
            high=Decimal("60000"),
            low=Decimal("60000"),
            close=Decimal("60000"),
            volume=Decimal("0"),
            source="fixture",
        )
    )
    clock = FrozenClock(NOW)
    accounting.deposit(session, "paper", Decimal("10000"), clock)
    # Some BTC so the sell side of the grid can quote.
    session.add(Balance(time=NOW, mode="paper", asset="BTC", quantity=Decimal("0.05")))
    risk.update_equity(session, Decimal("10000"), clock)
    session.flush()
    snap = subsidy.compute_status(session, clock, "paper")
    assert snap.status == "active"
    return clock


def _worker(
    bbo: dict[str, tuple[Decimal, Decimal]], clock: FrozenClock
) -> PingPongWorker:
    return PingPongWorker(ticker=lambda _symbol: bbo["quote"], clock=clock)


def _adapter(session: Session, clock: FrozenClock) -> PaperAdapter:
    return PaperAdapter(session, BITSTAMP, clock, "paper")


def _open(session: Session) -> list[Order]:
    return list(
        session.scalars(
            select(Order)
            .join(Plan, Order.plan_id == Plan.id)
            .where(Plan.sleeve == "ping_pong", Order.status.in_(_OPEN))
            .order_by(Order.id)
        )
    )


def test_multi_level_progression(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (2, 2)
    orders = _open(session)
    assert len(orders) == 4
    buys = sorted(o.price for o in orders if o.side == "buy")
    sells = sorted(o.price for o in orders if o.side == "sell")
    assert buys == [Decimal("59930.01"), Decimal("59960.00")]
    assert sells == [Decimal("60040.00"), Decimal("60070.01")]
    plan = session.get(Plan, orders[0].plan_id)
    assert plan is not None and plan.status == "approved"
    assert all(e["maker_or_cancel"] for e in plan.entry_orders)

    # Ask dips under the top buy level only: one buy fills -> (1, 2).
    bbo["quote"] = (Decimal("59950"), Decimal("59958"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 2)
    assert len(_open(session)) == 3

    # Bid lifts through the top sell level only: one sell fills -> (1, 1).
    bbo["quote"] = (Decimal("59990"), Decimal("59958"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 1)
    assert len(_open(session)) == 2

    # Bid stays through the sell level; the ask stays above the buy -> (1, 0).
    bbo["quote"] = (Decimal("59990"), Decimal("59965"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 0)
    assert [o.side for o in _open(session)] == ["buy"]

    # Ask drops through the buy: (0, 0) resets the grid to (2, 2).
    bbo["quote"] = (Decimal("59950"), Decimal("59955"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (2, 2)
    assert len(_open(session)) == 4


def test_requotes_after_refresh_seconds(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    first = _open(session)
    assert len(first) == 4

    # Same tick again, timer not elapsed: orders untouched.
    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == first

    clock.advance(timedelta(seconds=21))
    bbo["quote"] = (Decimal("59980"), Decimal("59990"))
    worker.tick(session, adapter, clock, "paper")

    for order in first:
        session.refresh(order)
        assert order.status == "cancelled"
    new = _open(session)
    assert len(new) == 4
    new_buys = sorted(o.price for o in new if o.side == "buy")
    assert new_buys == [Decimal("59920.02"), Decimal("59950.01")]


def test_paper_fill_books_to_ledger(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    order = _open(session)[0]

    bbo["quote"] = (Decimal("59950"), Decimal("59955"))
    worker.tick(session, adapter, clock, "paper")

    fills = list(session.scalars(select(Fill)))
    assert len(fills) == 1
    assert fills[0].order_id == order.id
    assert fills[0].price == order.price
    assert accounting.positions(session, "paper")["BTC"] == (
        Decimal("0.05") + order.quantity
    )

    snap = subsidy.compute_status(session, clock, "paper")
    assert snap.today_booster_volume_usd == fills[0].price * fills[0].quantity
    assert snap.booster_fees_30d_usd == fills[0].fee


def test_halt_cancels_and_disabled_places_nothing(
    session: Session, monkeypatch
) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert len(_open(session)) == 4

    session.add(Setting(key="risk.halted", value=True, updated_at=NOW))
    session.flush()
    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == []
    for order in session.scalars(select(Order)).all():
        assert order.status == "cancelled"


def test_disabled_places_nothing(session: Session, monkeypatch) -> None:
    _enable(monkeypatch, TRADER_SUBSIDY_ENABLED="false")
    clock = FrozenClock(NOW)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == []
