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


def _five_min_candles(symbol: str, n: int, base: float, drift: float) -> list[Candle]:
    """``n`` 5m candles ending at NOW. ``drift`` 0 is a daily sine-wave
    sideways tape (4h ADX < 20); a positive drift is a steady climb
    (4h ADX > 20)."""
    i = np.arange(n)
    if drift == 0:
        rng = np.random.default_rng(3)
        closes = base * (
            1 + 0.005 * np.sin(2 * np.pi * i / 288) + rng.normal(0, 0.0005, n)
        )
    else:
        rng = np.random.default_rng(7)
        closes = base * np.exp(np.cumsum(rng.normal(drift, 0.001, n)))
    candles: list[Candle] = []
    for k in range(n):
        open_time = NOW - timedelta(minutes=5 * (n - k))
        c = Decimal(str(round(float(closes[k]), 2)))
        candles.append(
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=open_time,
                open=c,
                high=c * Decimal("1.0005"),
                low=c * Decimal("0.9995"),
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


def _order(
    s: Session,
    *,
    sleeve: str,
    side: str,
    price: Decimal,
    quantity: Decimal,
    status: str,
    at: datetime,
    mode: str = "paper",
    shadow: bool = False,
) -> Order:
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
        price=price,
        quantity=quantity,
        status=status,
        mode=mode,
        shadow=shadow,
    )
    s.add(order)
    s.flush()
    return order


def _fill(
    s: Session,
    *,
    sleeve: str,
    notional: Decimal,
    side: str = "buy",
    price: Decimal = Decimal("50000"),
    at: datetime,
    mode: str = "paper",
    shadow: bool = False,
    apply: bool = False,
) -> None:
    order = _order(
        s,
        sleeve=sleeve,
        side=side,
        price=price,
        quantity=notional / price,
        status="filled",
        at=at,
        mode=mode,
        shadow=shadow,
    )
    fill = Fill(
        order_id=order.id,
        time=at,
        price=price,
        quantity=notional / price,
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


def test_adx_prefers_4h_bars_from_5m_candles(session, monkeypatch) -> None:
    """Spec W1.4c: with enough 5m history the gate reads a 4h ADX, which
    wins over a contradictory 1d reading in both directions."""
    _enable(monkeypatch)
    # Trending 1d candles (ADX > 20) but a sideways 4h tape -> active.
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.02):
        session.add(candle)
    for candle in _five_min_candles("BTC-USD", 2400, 60000, drift=0.0):
        session.add(candle)
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "active"
    assert snap.adx is not None and snap.adx < 20


def test_adx_4h_throttles_when_5m_tape_trends(session, monkeypatch) -> None:
    """Sideways 1d candles but a trending 4h tape -> throttled."""
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    for candle in _five_min_candles("BTC-USD", 2400, 60000, drift=0.0005):
        session.add(candle)
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert "ADX" in snap.reason
    assert snap.adx is not None and snap.adx >= 20


def test_adx_falls_back_to_1d_when_5m_history_short(session, monkeypatch) -> None:
    """One 5m candle is too few for a 4h ADX(14); the daily reading
    keeps the gate defined instead of degrading to None."""
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.add(_five_min_close("BTC-USD", "60000"))
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "active"
    assert snap.adx is not None and snap.adx < 20


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


def test_throttled_when_daily_cap_within_min_order(session, monkeypatch) -> None:
    """Spec W1.3h: $4.85 of cap room left cannot fund the $10-minimum
    paper order, so the sleeve reports throttled, not active."""
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("1995.15"),
        at=NOW - timedelta(hours=1),
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert snap.reason == "Daily booster cap reached."
    assert snap.today_booster_volume_usd == Decimal("1995.15")


def test_throttled_when_target_within_min_order(session, monkeypatch) -> None:
    """Spec W1.3h: under $10 of remaining target volume throttles too;
    the remaining-to-target label still reports the exact $4.85."""
    _enable(monkeypatch)
    _fill(
        session,
        sleeve="trend",
        notional=Decimal("9995.15"),
        at=NOW - timedelta(days=1),
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert snap.reason == "Target fee tier volume reached."
    assert snap.remaining_to_target_usd == Decimal("4.85")


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


def test_disabled_status_on_non_coinbase_venue(session, monkeypatch) -> None:
    _enable(monkeypatch, TRADER_VENUE="bitstamp")
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.flush()

    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "disabled"
    assert "Coinbase" in snap.reason


def test_buy_notional_reserves_fee_cash(session, monkeypatch) -> None:
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.add(_five_min_close("BTC-USD", "60000"))
    _fund(session)
    # Drain most of the cash: a $250 order would exceed the ~$161 left.
    _fill(
        session,
        sleeve="trend",
        notional=Decimal("9800"),
        side="buy",
        at=NOW - timedelta(hours=2),
        apply=True,
    )
    session.flush()
    cash = accounting.cash(session, "paper")

    plan = subsidy.propose_ping_pong_plan(session, FrozenClock(NOW), "paper")
    assert plan is not None
    assert plan.side == "buy"
    order = plan.entry_orders[0]
    notional = Decimal(order["price"]) * Decimal(order["quantity"])
    assert notional <= cash
    assert notional <= cash / (Decimal("1") + Decimal("0.003"))
    assert notional < Decimal("250")


def test_db_override_enables_subsidy_without_env(session, monkeypatch) -> None:
    """Spec W1.3b: a DB row for subsidy_enabled activates the sleeve even
    though the .env default is False — no container restart needed."""
    _enable(monkeypatch, TRADER_SUBSIDY_ENABLED="false")
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "disabled"

    session.add(Setting(key="subsidy_enabled", value=True, updated_at=NOW))
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "active"


def test_db_override_disables_subsidy_over_env(session, monkeypatch) -> None:
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.flush()
    session.add(Setting(key="subsidy_enabled", value=False, updated_at=NOW))
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "disabled"


def test_open_orders_usd(session, monkeypatch) -> None:
    """Spec W1.3e: committed capital sums open ping_pong order notionals;
    a partially filled order counts only its unfilled remainder."""
    _enable(monkeypatch)
    at = NOW - timedelta(minutes=10)
    _order(
        session,
        sleeve="ping_pong",
        side="buy",
        price=Decimal("50000"),
        quantity=Decimal("0.01"),
        status="new",
        at=at,
    )
    partial = _order(
        session,
        sleeve="ping_pong",
        side="buy",
        price=Decimal("50000"),
        quantity=Decimal("0.02"),
        status="partially_filled",
        at=at,
    )
    session.add(
        Fill(
            order_id=partial.id,
            time=at,
            price=Decimal("50000"),
            quantity=Decimal("0.005"),
            fee=Decimal("1"),
            fee_currency="USD",
            liquidity="maker",
        )
    )
    _order(  # excluded: shadow
        session,
        sleeve="ping_pong",
        side="buy",
        price=Decimal("50000"),
        quantity=Decimal("1"),
        status="open",
        at=at,
        shadow=True,
    )
    _order(  # excluded: different sleeve
        session,
        sleeve="trend",
        side="buy",
        price=Decimal("50000"),
        quantity=Decimal("1"),
        status="open",
        at=at,
    )
    _order(  # excluded: no longer open
        session,
        sleeve="ping_pong",
        side="buy",
        price=Decimal("50000"),
        quantity=Decimal("1"),
        status="cancelled",
        at=at,
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.open_orders_usd == Decimal("1250")


def test_booster_net_pnl_30d_fifo(session, monkeypatch) -> None:
    """Spec W1.3e: FIFO spread gain minus every ping_pong fee in 30d.
    The in-window sell matches the oldest open lot — the 40-day-old
    0.01 @ 48,000 buy, not the newer 0.01 @ 49,000 — so spread is
    0.01 * (50,000 - 48,000) = $20; minus in-window fees (1.96 + 2.00)
    and the pre-window buy's fee is outside the window, giving $16.04."""
    _enable(monkeypatch)
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("480"),
        side="buy",
        price=Decimal("48000"),
        at=NOW - timedelta(days=40),
    )
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("490"),
        side="buy",
        price=Decimal("49000"),
        at=NOW - timedelta(days=2),
    )
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("500"),
        side="sell",
        price=Decimal("50000"),
        at=NOW - timedelta(days=1),
    )
    _fill(  # other sleeves never enter the FIFO book
        session,
        sleeve="trend",
        notional=Decimal("500"),
        side="sell",
        price=Decimal("50000"),
        at=NOW - timedelta(days=1),
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.booster_net_pnl_30d_usd == Decimal("16.04")


def test_fill_pnl_usd_fifo(session, monkeypatch) -> None:
    """Spec W1.3i: buys carry -fee, sells carry FIFO spread - fee, and
    summing pnl_usd over the 30d window reproduces the net exactly."""
    _enable(monkeypatch)
    _fill(  # oldest lot, outside the window: pays only its fee
        session,
        sleeve="ping_pong",
        notional=Decimal("480"),
        side="buy",
        price=Decimal("48000"),
        at=NOW - timedelta(days=40),
    )
    _fill(  # newer lot, inside the window
        session,
        sleeve="ping_pong",
        notional=Decimal("490"),
        side="buy",
        price=Decimal("49000"),
        at=NOW - timedelta(days=2),
    )
    _fill(  # sells against the 40-day-old 48,000 lot: spread $20
        session,
        sleeve="ping_pong",
        notional=Decimal("500"),
        side="sell",
        price=Decimal("50000"),
        at=NOW - timedelta(days=1),
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    fills = {(f.side, f.time): f for f in snap.recent_fills}
    old_buy = fills[("buy", NOW - timedelta(days=40))]
    new_buy = fills[("buy", NOW - timedelta(days=2))]
    sell = fills[("sell", NOW - timedelta(days=1))]
    assert old_buy.pnl_usd == -old_buy.fee_usd == Decimal("-1.92")
    assert new_buy.pnl_usd == -new_buy.fee_usd == Decimal("-1.96")
    assert sell.pnl_usd == Decimal("18.00")  # 0.01 * (50000 - 48000) - 2.00
    cutoff = NOW - timedelta(days=30)
    in_window = [f.pnl_usd for f in snap.recent_fills if f.time >= cutoff]
    assert sum(in_window, Decimal("0")) == snap.booster_net_pnl_30d_usd


def test_booster_net_pnl_counts_unclosed_fees(session, monkeypatch) -> None:
    """A buy whose round-trip has not closed still spends its fee."""
    _enable(monkeypatch)
    _fill(
        session,
        sleeve="ping_pong",
        notional=Decimal("500"),
        side="buy",
        at=NOW - timedelta(hours=3),
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.booster_net_pnl_30d_usd == Decimal("-2")


def test_recent_fills_newest_first_and_capped(session, monkeypatch) -> None:
    """Spec W1.3e: up to 25 newest non-shadow ping_pong fills."""
    _enable(monkeypatch)
    for i in range(27):
        _fill(
            session,
            sleeve="ping_pong",
            notional=Decimal("100"),
            side="buy" if i % 2 == 0 else "sell",
            at=NOW - timedelta(minutes=27 - i),
        )
    _fill(  # excluded: different sleeve
        session, sleeve="trend", notional=Decimal("100"), at=NOW
    )
    _fill(  # excluded: shadow
        session,
        sleeve="ping_pong",
        notional=Decimal("100"),
        at=NOW,
        shadow=True,
    )
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert len(snap.recent_fills) == 25
    times = [f.time for f in snap.recent_fills]
    assert times == sorted(times, reverse=True)
    newest = snap.recent_fills[0]
    assert newest.side == "buy"  # i = 26 is the newest seed
    assert newest.price == Decimal("50000")
    assert newest.quantity == Decimal("0.002")
    assert newest.notional_usd == Decimal("100")
    assert newest.fee_usd == Decimal("0.4")


def test_db_override_adx_max(session, monkeypatch) -> None:
    """Lowering subsidy_adx_max in the DB throttles a sideways market the
    env default (20) allows — without touching env vars."""
    _enable(monkeypatch)
    for candle in _daily_candles("BTC-USD", 60, 60000, drift=0.0):
        session.add(candle)
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "active"
    assert snap.adx is not None and snap.adx < 20

    session.add(Setting(key="subsidy_adx_max", value=5.0, updated_at=NOW))
    session.flush()
    snap = subsidy.compute_status(session, FrozenClock(NOW), "paper")
    assert snap.status == "throttled"
    assert "ADX" in snap.reason
    assert snap.adx_max == 5.0
