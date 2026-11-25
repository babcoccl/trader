"""Ping-pong subsidy sleeve tests (spec W1.2b): tier math, throttling and
maker-only plan creation. Needs TEST_DATABASE_URL."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.core.config import get_settings
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


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


def _daily_candles(symbol: str, n: int, base: float, drift: float) -> list[Candle]:
    """``n`` daily candles ending yesterday; drift 0 is a mild sideways
    walk (ADX < 20), a strong positive drift trends (ADX > 20)."""
    rng = np.random.default_rng(3 if drift == 0 else 7)
    rets = rng.normal(drift, 0.004, n)
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


def _five_min_close(symbol: str, price: str) -> Candle:
    return Candle(
        symbol=symbol,
        interval="5m",
        open_time=NOW - timedelta(minutes=5),
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal("0"),
        source="fixture",
    )


def _fill(
    s: Session,
    *,
    sleeve: str,
    notional: Decimal,
    side: str = "buy",
    at: datetime,
    mode: str = "paper",
    shadow: bool = False,
    apply: bool = False,
) -> None:
    decision = Decision(
        cycle="intraday",
        ran_at=at,
        action="plan_proposed",
        reason="seed",
        details={},
    )
    s.add(decision)
    s.flush()
    plan = Plan(
        sleeve=sleeve,
        symbol="BTC-USD",
        side=side,
        status="completed",
        reference_price=Decimal("50000"),
        target_weight=Decimal("0"),
        entry_orders=[],
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="seed",
        expires_at=at,
        decision_id=decision.id,
    )
    s.add(plan)
    s.flush()
    order = Order(
        plan_id=plan.id,
        venue="paper",
        client_order_id=f"{plan.id}-1",
        symbol="BTC-USD",
        side=side,
        type="limit",
        price=Decimal("50000"),
        quantity=notional / Decimal("50000"),
        status="filled",
        mode=mode,
        shadow=shadow,
    )
    s.add(order)
    s.flush()
    fill = Fill(
        order_id=order.id,
        time=at,
        price=Decimal("50000"),
        quantity=notional / Decimal("50000"),
        fee=notional * Decimal("0.004"),
        fee_currency="USD",
        liquidity="maker",
    )
    s.add(fill)
    s.flush()
    if apply:
        accounting.apply_fill(s, fill)


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


def _fund(s: Session) -> None:
    accounting.deposit(s, "paper", Decimal("10000"), FrozenClock(NOW))
    risk.update_equity(s, Decimal("10000"), FrozenClock(NOW))


def _enable(monkeypatch, **overrides: str) -> None:
    env = {
        "TRADER_SUBSIDY_ENABLED": "true",
        "TRADER_VENUE": "coinbase",
        "TRADER_SUBSIDY_TARGET_VOLUME_USD": "10000",
        "TRADER_SUBSIDY_DAILY_CAP_USD": "2000",
        "TRADER_SUBSIDY_ADX_MAX": "20",
        "TRADER_SUBSIDY_ORDER_NOTIONAL_USD": "250",
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_disabled_by_default(session) -> None:
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "disabled"
    assert snap.tier == "Tier 1"
    assert snap.trailing_30d_volume_usd == 0


def test_tier_progress(session, monkeypatch) -> None:
    _enable(monkeypatch)
    _fill(session, sleeve="trend", notional=Decimal("5000"), at=NOW - timedelta(days=2))
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.trailing_30d_volume_usd == Decimal("5000")
    assert snap.tier == "Tier 1"
    assert (snap.maker_bps, snap.taker_bps) == (40.0, 60.0)
    assert snap.remaining_to_target_usd == Decimal("5000")
    assert snap.progress_pct == pytest.approx(0.5)


def test_throttled_when_volume_reaches_target(session, monkeypatch) -> None:
    _enable(monkeypatch)
    _fill(
        session, sleeve="trend", notional=Decimal("12000"), at=NOW - timedelta(days=2)
    )
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert "tier" in snap.reason.lower()
    assert snap.tier == "Tier 2"
    assert (snap.maker_bps, snap.taker_bps) == (25.0, 40.0)
    assert snap.remaining_to_target_usd == 0
    assert snap.progress_pct == 1.0


def test_throttled_when_adx_missing_or_high(session, monkeypatch) -> None:
    _enable(monkeypatch)
    # No daily candles at all: ADX is undefined -> throttled.
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert "ADX" in snap.reason
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.02):
        session.add(candle)
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert "ADX" in snap.reason
    assert snap.adx is not None and snap.adx >= 20


def test_throttled_when_daily_cap_hit(session, monkeypatch) -> None:
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("2500"),
        at=NOW - timedelta(hours=2),
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert "Daily booster cap" in snap.reason
    assert snap.today_booster_volume_usd == Decimal("2500")
    assert snap.booster_trades_30d == 1
    assert snap.booster_volume_30d_usd == Decimal("2500")
    assert snap.booster_fees_30d_usd == Decimal("10")


def test_active_and_maker_only_plan(session, monkeypatch) -> None:
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.add(_five_min_close("BTC-USD", "60000"))
    _fund(session)
    session.flush()

    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "active"
    assert snap.adx is not None and snap.adx < 20

    plan = subsidy.propose_ping_pong_plan(session, FrozenClock(NOW), "paper")
    assert plan is not None
    assert plan.sleeve == "ping_pong"
    assert plan.status == "pending"
    assert plan.side == "buy"
    order = plan.entry_orders[0]
    assert order["type"] == "limit"
    assert order["maker_or_cancel"] is True
    assert Decimal(order["price"]) < Decimal("60000")
    assert Decimal(order["price"]) * Decimal(order["quantity"]) <= Decimal("250")

    # A second pass proposes nothing while one is open.
    assert subsidy.propose_ping_pong_plan(session, FrozenClock(NOW), "paper") is None


def test_plan_alternates_to_sell_when_holding(session, monkeypatch) -> None:
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.add(_five_min_close("BTC-USD", "60000"))
    _fund(session)
    # The sleeve already holds BTC from an earlier ping-pong buy.
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("240"),
        side="buy",
        at=NOW - timedelta(hours=30),
        apply=True,
    )
    session.flush()

    plan = subsidy.propose_ping_pong_plan(session, FrozenClock(NOW), "paper")
    assert plan is not None
    assert plan.side == "sell"
    order = plan.entry_orders[0]
    assert order["maker_or_cancel"] is True
    assert Decimal(order["price"]) > Decimal("60000")
    assert Decimal(order["quantity"]) <= Decimal("240") / Decimal("50000")


def test_buys_again_after_position_sold_elsewhere(session, monkeypatch) -> None:
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.add(_five_min_close("BTC-USD", "60000"))
    _fund(session)
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("240"),
        side="buy",
        at=NOW - timedelta(hours=30),
        apply=True,
    )
    # A flatten/trend sell outside the sleeve liquidated the BTC.
    _fill(
        session,
        sleeve="trend",
        notional=Decimal("240"),
        side="sell",
        at=NOW - timedelta(hours=1),
        apply=True,
    )
    session.flush()

    plan = subsidy.propose_ping_pong_plan(session, FrozenClock(NOW), "paper")
    assert plan is not None
    assert plan.side == "buy"


def test_no_plan_on_non_coinbase_venue(session, monkeypatch) -> None:
    _enable(monkeypatch, TRADER_VENUE="bitstamp")
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.add(_five_min_close("BTC-USD", "60000"))
    _fund(session)
    session.flush()

    assert subsidy.propose_ping_pong_plan(session, FrozenClock(NOW), "paper") is None
