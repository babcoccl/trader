"""Paper execution adapter and executor tests (spec 2.4). Needs
TEST_DATABASE_URL (Compose Postgres).

``Order.created_at`` is stamped by the database wall clock, so candle times
are derived from each order's real ``created_at`` (next 5-minute boundary).
"""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import FrozenClock
from trader.execution.__main__ import Executor, _latest_prices
from trader.ledger import accounting
from trader.ledger.models import (
    AuditLog,
    Balance,
    Candle,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    Heartbeat,
    Order,
    Plan,
    Setting,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)


def _alembic(connection, *args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    cfg.attributes["connection"] = connection
    getattr(command, args[0])(cfg, *args[1:])


_engine = None


def _shared_engine():
    global _engine
    if _engine is None:
        assert TEST_URL is not None
        _engine = create_engine(TEST_URL)
        with _engine.begin() as connection:
            _alembic(connection, "upgrade", "head")
    return _engine


def _clean(s: Session) -> None:
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
        Heartbeat,
        AuditLog,
    ):
        s.execute(delete(table))
    s.commit()


@pytest.fixture()
def session():
    engine = _shared_engine()
    with Session(engine) as s:
        try:
            _clean(s)
            yield s
        finally:
            s.rollback()
            _clean(s)


@contextmanager
def _cm(session: Session):
    yield session
    session.commit()


def _executor(session: Session) -> Executor:
    """An executor whose Clock stands where the collector would: just after
    the newest fixture candle has closed (or now, if that is later)."""
    newest = session.scalar(select(func.max(Candle.open_time)))
    at = _now() if newest is None else max(_now(), newest + timedelta(minutes=5))
    return Executor(clock=FrozenClock(at), session_factory=lambda: _cm(session))


def _now() -> datetime:
    return datetime.now(UTC)


def _next_candle(after: datetime, n: int = 1) -> datetime:
    """First 5-minute boundary strictly after ``after``, plus n-1 periods."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    step = 300
    k = int(after.timestamp()) // step + n
    return epoch + timedelta(seconds=k * step)


def _decision(session: Session, ran_at: datetime) -> Decision:
    d = Decision(cycle="daily", ran_at=ran_at, action="none", reason="seed", details={})
    session.add(d)
    session.flush()
    return d


def _plan(
    session: Session,
    symbol: str = "BTC-USD",
    side: str = "buy",
    status: str = "approved",
    entry_orders: list | None = None,
    stop_price: Decimal | None = None,
    decided_at: datetime | None = None,
) -> Plan:
    now = _now()
    plan = Plan(
        sleeve="trend",
        symbol=symbol,
        side=side,
        status=status,
        reference_price=Decimal("60000"),
        target_weight=Decimal("0.5"),
        entry_orders=entry_orders or [],
        stop_price=stop_price,
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="test",
        expires_at=now + timedelta(hours=6),
        decided_at=decided_at or now,
        decision_id=_decision(session, now).id,
    )
    session.add(plan)
    session.flush()
    return plan


def _candle(
    session: Session,
    symbol: str,
    open_time: datetime,
    o: str,
    h: str,
    low: str,
    c: str,
    source: str = "bitstamp",
) -> Candle:
    candle = Candle(
        symbol=symbol,
        interval="5m",
        open_time=open_time,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1"),
        source=source,
    )
    session.add(candle)
    session.flush()
    return candle


def _balances(session: Session, **assets: str) -> None:
    for asset, qty in assets.items():
        session.add(
            Balance(time=_now(), mode="paper", asset=asset, quantity=Decimal(qty))
        )
    session.flush()


def _orders(session: Session, plan: Plan) -> list[Order]:
    return list(
        session.scalars(
            select(Order).where(Order.plan_id == plan.id).order_by(Order.id)
        )
    )


def _fills(session: Session) -> list[Fill]:
    return list(session.scalars(select(Fill).order_by(Fill.id)))


def _filled_buy(session: Session, plan: Plan, symbol: str = "BTC-USD") -> Order:
    """A filled buy order under ``plan`` — marks the plan as the one that
    established the position, so its stop governs the emergency check."""
    order = Order(
        plan_id=plan.id,
        venue="paper",
        client_order_id=f"{plan.id}-0",
        venue_order_id=f"paper-{plan.id}-0",
        symbol=symbol,
        side="buy",
        type="market",
        quantity=Decimal("1"),
        status="filled",
        mode="paper",
    )
    session.add(order)
    session.flush()
    return order


# --- fill simulation --------------------------------------------------------


def test_limit_buy_fills_on_trade_through(session: Session) -> None:
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]
    assert order.status == "open"
    assert order.client_order_id == f"{plan.id}-1"
    assert order.venue_order_id == f"paper-{plan.id}-1"

    _candle(
        session,
        "BTC-USD",
        _next_candle(order.created_at),
        "60000",
        "60100",
        "59980",
        "60000",
    )
    _executor(session).tick()

    session.refresh(order)
    assert order.status == "filled"
    fill = _fills(session)[0]
    assert fill.price == Decimal("60000")
    assert fill.liquidity == "maker"
    assert fill.fee == (
        Decimal("60000") * Decimal("0.1") * Decimal(str(BITSTAMP.maker_fee))
    )
    assert accounting.positions(session, "paper")["BTC"] == Decimal("0.1")
    session.refresh(plan)
    assert plan.status == "completed"


def test_limit_touched_not_traded_through_does_not_fill(session: Session) -> None:
    """A candle that touches the limit but never trades through leaves the
    order open (spec 2.4)."""
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]

    # low = 60000 * (1 - 0.00005): touched, not through the 0.0001 band.
    _candle(
        session,
        "BTC-USD",
        _next_candle(order.created_at),
        "60010",
        "60010",
        "59997",
        "60000",
    )
    _executor(session).tick()

    session.refresh(order)
    assert order.status == "open"
    assert _fills(session) == []


def test_market_order_fills_at_open_with_slippage(session: Session) -> None:
    _balances(session, USD="10000")
    plan = _plan(session, entry_orders=[{"type": "market", "quantity": "0.1"}])
    _executor(session).tick()
    order = _orders(session, plan)[0]

    _candle(
        session,
        "BTC-USD",
        _next_candle(order.created_at),
        "61000",
        "61000",
        "61000",
        "61000",
    )
    _executor(session).tick()

    fill = _fills(session)[0]
    expected = Decimal("61000") * (1 + Decimal(str(BITSTAMP.slippage)))
    assert fill.price == expected
    assert fill.liquidity == "taker"
    assert fill.fee == expected * Decimal("0.1") * Decimal(str(BITSTAMP.taker_fee))


def test_stop_sell_fills_at_min_open_stop(session: Session) -> None:
    _balances(session, BTC="0.5", USD="1000")
    plan = _plan(
        session,
        side="sell",
        entry_orders=[{"type": "stop", "stop_price": "55000", "quantity": "0.5"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]

    _candle(
        session,
        "BTC-USD",
        _next_candle(order.created_at),
        "56000",
        "56000",
        "54000",
        "54500",
    )
    _executor(session).tick()

    fill = _fills(session)[0]
    assert fill.price == Decimal("55000") * (1 - Decimal(str(BITSTAMP.slippage)))
    assert fill.liquidity == "taker"
    assert accounting.positions(session, "paper") == {}


# --- emergency stop ---------------------------------------------------------


def test_emergency_stop_sells_position_first_loop(session: Session) -> None:
    """Price at or under a plan's stop sells the whole position on the first
    executor loop after start-up, with no approval."""
    _balances(session, BTC="1.0", USD="1000")
    plan = _plan(session, status="completed", stop_price=Decimal("50000"))
    _filled_buy(session, plan)
    # A resting order on the same asset must be cancelled too.
    resting_plan = _plan(session)
    resting = Order(
        plan_id=resting_plan.id,
        venue="paper",
        client_order_id=f"{resting_plan.id}-1",
        venue_order_id=f"paper-{resting_plan.id}-1",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("60000"),
        quantity=Decimal("0.1"),
        status="open",
        mode="paper",
    )
    session.add(resting)
    _candle(
        session,
        "BTC-USD",
        _now() - timedelta(minutes=5),
        "49500",
        "49500",
        "48000",
        "49000",
    )

    _executor(session).tick()

    fills = _fills(session)
    assert len(fills) == 1
    assert fills[0].liquidity == "taker"
    order = session.get(Order, fills[0].order_id)
    assert order is not None and order.type == "market" and order.side == "sell"
    assert fills[0].price == Decimal("49000") * (1 - Decimal(str(BITSTAMP.slippage)))
    assert accounting.positions(session, "paper") == {}
    session.refresh(resting)
    assert resting.status == "cancelled"
    event = session.scalars(select(Event).where(Event.kind == "emergency_stop")).one()
    assert event.details["plan_id"] == plan.id


def test_emergency_stop_after_restart(session: Session) -> None:
    """A fresh Executor instance (the restart case) fires the stop on its
    first pass — nothing depends on in-memory state."""
    _balances(session, BTC="0.25", USD="0")
    _filled_buy(
        session, _plan(session, status="completed", stop_price=Decimal("50000"))
    )
    _candle(
        session,
        "BTC-USD",
        _now() - timedelta(minutes=5),
        "49500",
        "49500",
        "49000",
        "49500",
    )

    _executor(session).tick()

    assert accounting.positions(session, "paper") == {}
    assert len(_fills(session)) == 1


# --- plan time rules ----------------------------------------------------------


def test_limit_converts_to_market_after_one_hour(session: Session) -> None:
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
        decided_at=_now() - timedelta(hours=2),
    )
    _executor(session).tick()
    first = _orders(session, plan)[0]
    assert first.type == "limit"

    _executor(session).tick()
    session.refresh(first)
    assert first.status == "cancelled"
    orders = _orders(session, plan)
    assert len(orders) == 2 and orders[1].type == "market"
    assert orders[1].client_order_id == f"{plan.id}-2"

    _candle(
        session,
        "BTC-USD",
        _next_candle(orders[1].created_at),
        "60000",
        "60000",
        "60000",
        "60000",
    )
    _executor(session).tick()
    assert _fills(session)[0].liquidity == "taker"


def test_buy_limit_repriced_to_new_close(session: Session) -> None:
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    first = _orders(session, plan)[0]

    # Does not trade through 60000 (low 59995 >= 59994), closes lower.
    _candle(
        session,
        "BTC-USD",
        _next_candle(first.created_at),
        "60000",
        "60000",
        "59995",
        "59995",
    )
    _executor(session).tick()

    orders = _orders(session, plan)
    assert orders[0].status == "cancelled"
    assert len(orders) == 2
    assert orders[1].type == "limit" and orders[1].price == Decimal("59995")


# --- restart safety ---------------------------------------------------------


def test_restart_neither_loses_nor_duplicates(session: Session) -> None:
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()

    # Restart: a new executor must not place the plan's orders again.
    _executor(session).tick()
    orders = _orders(session, plan)
    assert len(orders) == 1
    assert orders[0].client_order_id == f"{plan.id}-1"

    _candle(
        session,
        "BTC-USD",
        _next_candle(orders[0].created_at),
        "60000",
        "60000",
        "59980",
        "60000",
    )
    _executor(session).tick()
    _executor(session).tick()

    assert len(_fills(session)) == 1
    session.refresh(plan)
    assert plan.status == "completed"


def test_earned_fill_survives_repricing(session: Session) -> None:
    """A candle that both trades through the limit and closes elsewhere must
    fill the order — re-pricing runs after fills, not before."""
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]

    _candle(
        session,
        "BTC-USD",
        _next_candle(order.created_at),
        "60000",
        "60000",
        "59980",
        "59950",
    )
    _executor(session).tick()

    orders = _orders(session, plan)
    assert len(orders) == 1 and orders[0].status == "filled"
    assert _fills(session)[0].liquidity == "maker"


def test_unaffordable_fill_rejects_order_not_pass(session: Session) -> None:
    """A market buy above available cash rejects that order; other fills in
    the same pass still settle."""
    _balances(session, USD="1000", ETH="0.5")
    buy_plan = _plan(session, entry_orders=[{"type": "market", "quantity": "1"}])
    sell_plan = _plan(
        session,
        symbol="ETH-USD",
        side="sell",
        entry_orders=[{"type": "market", "quantity": "0.5"}],
    )
    _executor(session).tick()
    buy = _orders(session, buy_plan)[0]
    sell = _orders(session, sell_plan)[0]
    at = max(buy.created_at, sell.created_at)
    _candle(session, "BTC-USD", _next_candle(at), "60000", "60000", "60000", "60000")
    _candle(session, "ETH-USD", _next_candle(at), "3000", "3000", "3000", "3000")

    _executor(session).tick()

    session.refresh(buy)
    session.refresh(sell)
    assert buy.status == "rejected"
    assert sell.status == "filled"
    assert len(_fills(session)) == 1
    assert accounting.cash(session, "paper") > Decimal("1000")
    session.scalars(select(Event).where(Event.kind == "fill_rejected")).one()


def test_catchup_fills_apply_in_candle_order(session: Session) -> None:
    """A sale on an earlier candle settles before a buy it funds on a later
    candle, even though the buy's order row came first."""
    _balances(session, USD="1000", ETH="0.5")
    buy_plan = _plan(session, entry_orders=[{"type": "market", "quantity": "0.04"}])
    sell_plan = _plan(
        session,
        symbol="ETH-USD",
        side="sell",
        entry_orders=[{"type": "market", "quantity": "0.5"}],
    )
    _executor(session).tick()
    buy = _orders(session, buy_plan)[0]
    sell = _orders(session, sell_plan)[0]
    at = max(buy.created_at, sell.created_at)
    _candle(session, "ETH-USD", _next_candle(at), "3000", "3000", "3000", "3000")
    _candle(
        session,
        "BTC-USD",
        _next_candle(at, 2),
        "60000",
        "60000",
        "60000",
        "60000",
    )

    _executor(session).tick()

    session.refresh(buy)
    session.refresh(sell)
    assert sell.status == "filled" and buy.status == "filled"
    assert len(_fills(session)) == 2
    fills = _fills(session)
    assert fills[0].order_id == sell.id and fills[1].order_id == buy.id


def test_halt_cancels_buys_and_blocks_new(session: Session) -> None:
    """While halted, resting buys are cancelled and no new buy orders are
    placed (spec 2.1 veto applies to execution too)."""
    _balances(session, USD="10000")
    session.add(Setting(key="risk.halted", value=True, updated_at=_now()))
    buy_plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    resting_plan = _plan(session)
    resting = Order(
        plan_id=resting_plan.id,
        venue="paper",
        client_order_id=f"{resting_plan.id}-1",
        venue_order_id=f"paper-{resting_plan.id}-1",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("60000"),
        quantity=Decimal("0.1"),
        status="open",
        mode="paper",
    )
    session.add(resting)

    _executor(session).tick()

    session.refresh(resting)
    assert resting.status == "cancelled"
    assert _orders(session, buy_plan) == []


def test_old_stop_does_not_liquidate_new_position(session: Session) -> None:
    """After a full exit and re-entry, the newest stop-bearing plan governs;
    an older plan's higher stop must not fire."""
    _balances(session, BTC="0.5", USD="0")
    # Both plans filled a buy, so only the newest-first ordering protects
    # the re-entered position from the older, higher stop.
    _filled_buy(
        session, _plan(session, status="completed", stop_price=Decimal("55000"))
    )
    _filled_buy(
        session, _plan(session, status="completed", stop_price=Decimal("45000"))
    )
    _candle(
        session,
        "BTC-USD",
        _now() - timedelta(minutes=5),
        "50000",
        "50000",
        "49000",
        "50000",
    )

    _executor(session).tick()

    assert accounting.positions(session, "paper") == {"BTC": Decimal("0.5")}
    assert _fills(session) == []


def test_unfilled_plan_stop_does_not_govern(session: Session) -> None:
    """A newer plan that has not filled yet cannot replace the governing
    stop: the position's own stop still fires at its price."""
    _balances(session, BTC="0.5", USD="0")
    holding = _plan(session, status="completed", stop_price=Decimal("55000"))
    _filled_buy(session, holding)
    # Approved but not filled: its lower stop must not shield the position.
    _plan(
        session,
        stop_price=Decimal("45000"),
        entry_orders=[{"type": "limit", "price": "48000", "quantity": "0.1"}],
    )
    _candle(
        session,
        "BTC-USD",
        _now() - timedelta(minutes=5),
        "51000",
        "51000",
        "50000",
        "50000",
    )

    _executor(session).tick()

    assert accounting.positions(session, "paper") == {}
    event = session.scalars(select(Event).where(Event.kind == "emergency_stop")).one()
    assert event.details["plan_id"] == holding.id


def test_same_candle_sell_funds_buy(session: Session) -> None:
    """Within one candle a sell settles before a buy it funds, whatever the
    order ids are."""
    _balances(session, USD="1000", ETH="0.5")
    buy_plan = _plan(
        session, entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.04"}]
    )
    sell_plan = _plan(
        session,
        symbol="ETH-USD",
        side="sell",
        entry_orders=[{"type": "market", "quantity": "0.5"}],
    )
    _executor(session).tick()
    buy = _orders(session, buy_plan)[0]
    sell = _orders(session, sell_plan)[0]
    assert buy.id < sell.id
    at = max(buy.created_at, sell.created_at)
    t = _next_candle(at)
    _candle(session, "ETH-USD", t, "3000", "3000", "3000", "3000")
    _candle(session, "BTC-USD", t, "60000", "60000", "59900", "60000")

    _executor(session).tick()

    session.refresh(buy)
    session.refresh(sell)
    assert sell.status == "filled" and buy.status == "filled"
    fills = _fills(session)
    assert fills[0].order_id == sell.id and fills[1].order_id == buy.id
    assert fills[1].liquidity == "maker"


def test_expired_limit_fills_as_market_not_maker(session: Session) -> None:
    """A limit past its deadline converts to market before fills are
    collected: a post-deadline candle that trades through it earns a taker
    fill on the market order, not a maker fill on the limit."""
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
        decided_at=_now() - timedelta(hours=2),
    )
    # A candle that trades through the limit price must not fill it: the
    # expired limit converts to market before fills are collected.
    _candle(
        session,
        "BTC-USD",
        _next_candle(_now()),
        "60000",
        "60000",
        "59800",
        "59900",
    )

    _executor(session).tick()

    limit_order, market = _orders(session, plan)
    assert limit_order.status == "cancelled" and market.type == "market"
    assert _fills(session) == []
    # The replacement market order fills at that candle's open, taker.
    _executor(session).tick()
    (fill,) = _fills(session)
    assert fill.order_id == market.id and fill.liquidity == "taker"
    assert fill.price == Decimal("60000") * (1 + Decimal(str(BITSTAMP.slippage)))


def test_pre_deadline_candle_earns_maker_fill_after_restart(
    session: Session,
) -> None:
    """A candle that opened before the deadline and traded through the
    limit still fills maker when the executor sees it late — only
    post-deadline candles are off-limits to the limit order."""
    _balances(session, USD="10000")
    decided = _now() - timedelta(minutes=50)
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
        decided_at=decided,
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]
    # The order really rested during the downtime the candle covers.
    order.created_at = decided + timedelta(minutes=5)
    session.flush()
    _candle(
        session,
        "BTC-USD",
        decided + timedelta(minutes=10),
        "60000",
        "60000",
        "59900",
        "60000",
    )

    _executor(session).tick()

    session.refresh(order)
    assert order.status == "filled"
    assert len(_orders(session, plan)) == 1
    fill = _fills(session)[0]
    assert fill.price == Decimal("60000") and fill.liquidity == "maker"


def test_restart_after_expiry_honours_pre_deadline_candle(
    session: Session,
) -> None:
    """The executor was down across the deadline: a candle that closed
    before it and traded through the limit still earns the maker fill,
    and no market order replaces it."""
    _balances(session, USD="10000")
    decided = _now() - timedelta(hours=2)
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
        decided_at=decided,
    )
    # The book as it stood before the downtime: one resting limit.
    order = Order(
        plan_id=plan.id,
        venue="paper",
        client_order_id=f"{plan.id}-1",
        venue_order_id=f"paper-{plan.id}-1",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("60000"),
        quantity=Decimal("0.1"),
        status="open",
        mode="paper",
    )
    session.add(order)
    session.flush()
    order.created_at = decided + timedelta(minutes=5)
    session.flush()
    _candle(
        session,
        "BTC-USD",
        _next_candle(decided + timedelta(minutes=10)),
        "60000",
        "60000",
        "59900",
        "60000",
    )

    _executor(session).tick()

    session.refresh(order)
    assert order.status == "filled"
    assert len(_orders(session, plan)) == 1
    (fill,) = _fills(session)
    assert fill.price == Decimal("60000") and fill.liquidity == "maker"


def test_limit_sell_does_not_fund_same_candle_buy(session: Session) -> None:
    """A limit sell fills at an unknown intra-candle time, so it cannot
    fund a same-candle buy: the buy settles first and is rejected when
    cash is short."""
    _balances(session, USD="1000", ETH="0.5")
    buy_plan = _plan(session, entry_orders=[{"type": "market", "quantity": "0.04"}])
    sell_plan = _plan(
        session,
        symbol="ETH-USD",
        side="sell",
        entry_orders=[{"type": "limit", "price": "3000", "quantity": "0.5"}],
    )
    _executor(session).tick()
    buy = _orders(session, buy_plan)[0]
    sell = _orders(session, sell_plan)[0]
    at = max(buy.created_at, sell.created_at)
    t = _next_candle(at)
    _candle(session, "ETH-USD", t, "2990", "3100", "2990", "3050")
    _candle(session, "BTC-USD", t, "60000", "60000", "60000", "60000")

    _executor(session).tick()

    session.refresh(buy)
    session.refresh(sell)
    assert buy.status == "rejected"
    assert sell.status == "filled"
    (fill,) = _fills(session)
    assert fill.order_id == sell.id


def test_primary_source_candle_wins_period(session: Session) -> None:
    """After a failover both venues may store the same period; only the
    primary's candle drives fills and the latest price."""
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]
    t = _next_candle(order.created_at)
    # Backup venue traded through the limit; the primary did not.
    _candle(session, "BTC-USD", t, "60010", "60010", "59900", "59950", "coinbase")
    _candle(session, "BTC-USD", t, "60010", "60020", "60000", "60015")

    _executor(session).tick()

    assert _fills(session) == []
    assert all(o.status != "filled" for o in _orders(session, plan))
    assert _latest_prices(session)["BTC-USD"] == Decimal("60015")


def test_unclosed_candle_never_fills(session: Session) -> None:
    """A candle whose period has not ended by the Clock is not simulated,
    even if it is already stored."""
    _balances(session, USD="10000")
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]
    t = _next_candle(order.created_at)
    _candle(session, "BTC-USD", t, "60000", "60000", "59900", "60000")

    clock = FrozenClock(t + timedelta(minutes=4))
    Executor(clock=clock, session_factory=lambda: _cm(session)).tick()
    assert _fills(session) == []

    clock.advance(timedelta(minutes=1))
    Executor(clock=clock, session_factory=lambda: _cm(session)).tick()
    (fill,) = _fills(session)
    assert fill.order_id == order.id and fill.liquidity == "maker"


def test_candle_spanning_deadline_does_not_fill_limit(session: Session) -> None:
    """A candle that opened before the deadline but closed after it cannot
    tell whether the trade-through came before expiry: no maker fill, the
    limit converts to market."""
    _balances(session, USD="10000")
    b = _next_candle(_now() - timedelta(hours=2))
    decided = b - timedelta(minutes=58)  # deadline = b + 2 minutes
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
        decided_at=decided,
    )
    _executor(session).tick()
    limit_order = _orders(session, plan)[0]
    limit_order.created_at = decided + timedelta(minutes=1)
    for order in _orders(session, plan)[1:]:
        session.delete(order)  # rebuild the pre-expiry book
    limit_order.status = "open"
    session.flush()
    _candle(session, "BTC-USD", b, "60000", "60000", "59900", "60000")

    _executor(session).tick()

    assert _fills(session) == []
    session.refresh(limit_order)
    assert limit_order.status == "cancelled"
    assert _orders(session, plan)[-1].type == "market"


# --- worst-case taker setting -------------------------------------------------


def test_assume_taker_never_fills_limit(session: Session) -> None:
    _balances(session, USD="10000")
    session.add(Setting(key="paper_assume_taker", value=True, updated_at=_now()))
    plan = _plan(
        session,
        entry_orders=[{"type": "limit", "price": "60000", "quantity": "0.1"}],
    )
    _executor(session).tick()
    order = _orders(session, plan)[0]

    _candle(
        session,
        "BTC-USD",
        _next_candle(order.created_at),
        "60000",
        "60000",
        "59000",
        "60000",
    )
    _executor(session).tick()

    assert _fills(session) == []
