"""Spec R8: the paper pipeline end to end with production candle timing.

Candles are stored the way the collector stores them (closed periods only,
``source="bitstamp"``, newest 5m open at 23:55), then daily cycle, approval,
executor, fill and stop run in sequence on a frozen clock against the
Postgres test database. This is the test review finding 8 asked for: module
tests seeded "a just-closed candle", so the 00:05 staleness veto (finding 1)
passed CI.

One test function per numbered block of the spec, shared helpers.
"""

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
from trader.execution.__main__ import Executor
from trader.execution.base import LIMIT_TTL
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
from trader.ledger.models import (
    Candle as CandleRow,
)
from trader.ops import paper_report
from trader.plans import approval
from trader.strategy import ensemble, sizing

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

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
BTC = "BTC-USD"
USD_SEED = Decimal("5000")
FRACTION = Decimal("0.5")
VOL_TARGET = 0.40
# Spec section A, written out so a change to the shared cost preset fails here.
MAKER_FEE = Decimal("0.003")
TAKER_FEE = Decimal("0.004")
SLIPPAGE = Decimal("0.0001")
TICK = Decimal("0.01")

_engine = None


def _shared_engine():
    global _engine
    if _engine is None:
        assert TEST_URL is not None
        _engine = create_engine(TEST_URL)
        with _engine.begin() as connection:
            _alembic(connection, "upgrade", "head")
    return _engine


def _alembic(connection, *args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    cfg.attributes["connection"] = connection
    getattr(command, args[0])(cfg, *args[1:])


def _clean(s: Session) -> None:
    for table in (
        Fill,
        Order,
        Plan,
        Decision,
        Signal,
        Balance,
        Event,
        CandleRow,
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


def _flat(
    symbol: str,
    open_time: datetime,
    price: Decimal,
    *,
    low: Decimal | None = None,
    close: Decimal | None = None,
) -> Candle:
    """5m candle opening at ``price``; high and low cover open and close."""
    close = close if close is not None else price
    low = low if low is not None else min(price, close)
    return Candle(
        symbol=symbol,
        interval="5m",
        open_time=open_time,
        open=price,
        high=max(price, close, low),
        low=min(low, price, close),
        close=close,
        volume=Decimal("1"),
        source="bitstamp",
    )


def _tick_at(session: Session, clock: FrozenClock, at: datetime) -> None:
    """Advance the clock and run one executor pass."""
    clock.advance(at - clock.now())
    Executor(clock=clock, session_factory=lambda: _cm(session)).tick()


def _store_5m(
    session: Session,
    clock: FrozenClock,
    open_time: datetime,
    price: Decimal,
    *,
    low: Decimal | None = None,
    close: Decimal | None = None,
) -> None:
    """Store one 5m candle per symbol, the collector's way: only once the
    clock is past its close. The caller must have advanced the clock."""
    candles_mod.upsert(
        session,
        [_flat(s, open_time, price, low=low, close=close) for s in SYMBOLS],
        now=clock.now(),
    )
    session.commit()


def _candle_then_tick(
    session: Session,
    clock: FrozenClock,
    tick_at: datetime,
    open_time: datetime,
    price: Decimal,
    *,
    low: Decimal | None = None,
    close: Decimal | None = None,
) -> None:
    """Move the clock to ``tick_at``, store the candle for ``open_time``
    (closed by then, as the collector would), and run one executor pass."""
    clock.advance(tick_at - clock.now())
    _store_5m(session, clock, open_time, price, low=low, close=close)
    _tick_at(session, clock, tick_at)


def _pick_day(df: pd.DataFrame) -> datetime:
    """Latest fixture decision day where BTC's target is above zero and
    ETH's is zero, so the day proposes exactly one plan. Weights come from
    the strategy functions on the fixture closes; nothing hard-coded."""
    closes = {s: df.set_index("date")[f"{s}_close"] for s in SYMBOLS}
    signals = {s: ensemble.signal(closes[s]) for s in SYMBOLS}
    vols = {s: sizing.annual_vol(closes[s]) for s in SYMBOLS}
    days = signals[BTC].index
    for i in range(len(days) - 1, 0, -1):
        weights = sizing.target_weights(
            {s: float(signals[s].iloc[i]) for s in SYMBOLS},
            {s: float(vols[s].iloc[i]) for s in SYMBOLS},
            vol_target=VOL_TARGET,
        )
        if weights[BTC] > 0 and weights["ETH-USD"] == 0:
            day = days[i] + timedelta(days=1)
            return day.to_pydatetime().replace(tzinfo=UTC)
    raise AssertionError("no fixture day gives BTC a positive target weight")


@pytest.fixture()
def market(session: Session) -> datetime:
    """Spec block 1: daily candles from the fixture through ``day`` - 1,
    and 5m candles for the last two hours before ``day`` 00:00, newest open
    23:55, flat at the last daily close, all ``source="bitstamp"``.

    Returns the chosen ``day`` (00:00 UTC)."""
    df = pd.read_csv(FIXTURE, parse_dates=["date"])
    day = _pick_day(df)
    history = df[df["date"] < pd.Timestamp(day).tz_localize(None)]
    candles: list[Candle] = []
    for _, row in history.iterrows():
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
                    source="bitstamp",
                )
            )
    last = history.iloc[-1]
    assert last["date"] == pd.Timestamp(day - timedelta(days=1)).tz_localize(None)
    # The collector stores only closed candles: at 00:05 the newest 5m
    # candle opened at 23:55 (finding 1).
    candles += [
        _flat(
            symbol,
            day - timedelta(minutes=5 * (i + 1)),
            Decimal(str(last[f"{symbol}_close"])),
        )
        for symbol in SYMBOLS
        for i in range(24)
    ]
    candles_mod.upsert(session, candles, now=day)
    session.commit()
    return day


def _latest_prices(session: Session) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for symbol in SYMBOLS:
        close = session.scalar(
            select(CandleRow.close)
            .where(CandleRow.symbol == symbol, CandleRow.interval == "5m")
            .order_by(CandleRow.open_time.desc())
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


def _propose(session: Session, day: datetime) -> tuple[FrozenClock, Plan]:
    """Spec block 2: the 00:05 run proposes one pending BTC plan.

    Clock at 00:05:00.050, the age the review measured on a live run.
    Returns the clock (still at proposal time) and the plan."""
    clock = FrozenClock(day + timedelta(minutes=5, milliseconds=50))
    paper_report.ensure_seeded(session, clock)
    Engine(clock=clock, session_factory=lambda: _cm(session)).run_daily()

    decision = session.scalars(select(Decision).where(Decision.symbol == BTC)).one()
    assert decision.action == "plan_proposed", decision.reason
    plans = list(session.scalars(select(Plan).where(Plan.side == "buy")))
    assert len(plans) == 1
    plan = plans[0]
    assert plan.symbol == BTC
    assert plan.status == "pending"
    assert plan.expires_at == day + timedelta(hours=6, minutes=5, milliseconds=50)
    return clock, plan


def _approve_and_place(session: Session, clock: FrozenClock, plan: Plan) -> Order:
    """Spec block 3: approve at 0.5 at 00:06, executor tick at 00:06:30
    places one non-shadow limit buy at half the original quantity."""
    original_qty = Decimal(str(plan.entry_orders[0]["quantity"]))
    limit_price = Decimal(str(plan.entry_orders[0]["price"]))

    clock.advance(timedelta(minutes=1) - timedelta(milliseconds=50))  # 00:06:00
    approval.approve(session, plan.id, FRACTION, "owner", clock)
    session.commit()

    clock.advance(timedelta(seconds=30))  # 00:06:30
    Executor(clock=clock, session_factory=lambda: _cm(session)).tick()

    orders = list(
        session.scalars(
            select(Order).where(Order.plan_id == plan.id, Order.shadow.is_(False))
        )
    )
    assert len(orders) == 1
    order = orders[0]
    assert order.status == "open"
    assert order.side == "buy"
    assert order.type == "limit"
    assert order.price == limit_price
    assert order.quantity == original_qty * FRACTION
    return order


def _open_buy(session: Session, plan_id: int) -> Order:
    """The plan's one working non-shadow buy, as the ``orders`` table has it."""
    return session.scalars(
        select(Order).where(
            Order.plan_id == plan_id,
            Order.side == "buy",
            Order.shadow.is_(False),
            Order.status == "open",
        )
    ).one()


def _fill_maker(
    session: Session, clock: FrozenClock, day: datetime, order: Order
) -> Fill:
    """Spec block 4: a candle that trades through fills the limit as maker."""
    assert order.price is not None
    # 00:05 candle wholly above the limit: no fill. It opened before the
    # order was placed (00:06:30), so the paper adapter also skips it.
    _candle_then_tick(
        session,
        clock,
        day + timedelta(minutes=10, seconds=30),
        day + timedelta(minutes=5),
        order.price + TICK,
    )
    assert session.scalar(select(func.count()).select_from(Fill)) == 0

    # 00:10 candle, low 1% under the order's current limit (read from the
    # orders row: the executor re-prices every 5 minutes): fills at limit.
    order = _open_buy(session, order.plan_id)
    assert order.price is not None
    snapshots_before = session.scalar(select(func.count()).select_from(EquitySnapshot))
    _candle_then_tick(
        session,
        clock,
        day + timedelta(minutes=15, seconds=30),
        day + timedelta(minutes=10),
        order.price,
        low=order.price * Decimal("0.99"),
    )

    fill = session.scalars(select(Fill).where(Fill.order_id == order.id)).one()
    assert fill.liquidity == "maker"
    assert fill.price == order.price
    notional = fill.price * fill.quantity
    assert fill.fee == notional * MAKER_FEE
    assert accounting.positions(session, "paper")["BTC"] == order.quantity
    assert accounting.cash(session, "paper") == USD_SEED - notional - fill.fee
    # The fill tick wrote a snapshot that includes the fill: BTC marked at
    # the fill price, so equity is the seed less the fee.
    assert (
        session.scalar(select(func.count()).select_from(EquitySnapshot))
        == (snapshots_before or 0) + 1
    )
    snapshot = session.scalars(
        select(EquitySnapshot).order_by(EquitySnapshot.id.desc()).limit(1)
    ).one()
    assert snapshot.time == clock.now()
    assert abs(snapshot.equity_usd - (USD_SEED - fill.fee)) < Decimal("1e-12")
    plan = session.get(Plan, order.plan_id)
    assert plan is not None and plan.status == "completed"
    return fill


def test_proposal(session: Session, market: datetime) -> None:
    """Blocks 1-2: production candle timing, the daily run proposes a plan."""
    clock, plan = _propose(session, market)
    assert clock.now() == market + timedelta(minutes=5, milliseconds=50)
    assert plan.reference_price > 0


def test_approval_and_placement(session: Session, market: datetime) -> None:
    """Block 3: approve at half size, executor places the limit buy."""
    clock, plan = _propose(session, market)
    _approve_and_place(session, clock, plan)


def test_maker_fill(session: Session, market: datetime) -> None:
    """Block 4: trade-through candle fills the limit at the maker fee."""
    clock, plan = _propose(session, market)
    order = _approve_and_place(session, clock, plan)
    _fill_maker(session, clock, market, order)


def test_market_conversion(session: Session, market: datetime) -> None:
    """Block 5: nothing trades through for 65 minutes; the limit converts
    to a market order at the one-hour limit and fills at a candle open plus
    slippage."""
    clock, plan = _propose(session, market)
    order = _approve_and_place(session, clock, plan)
    price = order.price
    assert price is not None
    assert plan.decided_at is not None
    deadline = plan.decided_at + LIMIT_TTL

    def market_buys() -> list[Order]:
        return list(
            session.scalars(
                select(Order).where(
                    Order.plan_id == plan.id,
                    Order.type == "market",
                    Order.shadow.is_(False),
                )
            )
        )

    # Candles open above the limit and close at it (no re-price), low at
    # the limit (no trade-through). Open differs from close so the market
    # fill price shows which one it used.
    open_price = price + 10 * TICK
    t = market + timedelta(minutes=10)
    while not market_buys():
        assert t <= market + timedelta(minutes=80), "limit never converted"
        _candle_then_tick(
            session,
            clock,
            t + timedelta(seconds=30),
            t - timedelta(minutes=5),
            open_price,
            close=price,
        )
        limit = session.get(Order, order.id)
        assert limit is not None
        if clock.now() <= deadline:
            assert limit.status == "open"
            assert not market_buys()
        t += timedelta(minutes=5)
    # Converted on the first tick past the deadline, not earlier or later.
    assert clock.now() > deadline
    assert clock.now() - timedelta(minutes=5) <= deadline
    assert session.get(Order, order.id).status == "cancelled"
    (market_buy,) = market_buys()
    assert market_buy.quantity == order.quantity
    assert session.scalar(select(func.count()).select_from(Fill)) == 0

    # A fill needs a candle opened after the market order was placed: the
    # next stored candle opened before it, the one after that fills.
    for _ in range(2):
        _candle_then_tick(
            session,
            clock,
            t + timedelta(seconds=30),
            t - timedelta(minutes=5),
            open_price,
            close=price,
        )
        t += timedelta(minutes=5)
        if t - timedelta(minutes=10) < market_buy.created_at:
            assert session.scalar(select(func.count()).select_from(Fill)) == 0
    fill = session.scalars(select(Fill).where(Fill.order_id == market_buy.id)).one()
    assert session.scalar(select(func.count()).select_from(Fill)) == 1
    assert fill.liquidity == "taker"
    # Filled on the candle opened at t - 10, stamped at its close.
    assert fill.time == t - timedelta(minutes=5)
    assert fill.time >= market_buy.created_at
    assert fill.price == open_price * (1 + SLIPPAGE)
    assert fill.fee == fill.price * fill.quantity * TAKER_FEE


def test_emergency_stop(session: Session, market: datetime) -> None:
    """Block 6: a stored close under the stop sells the whole position;
    a low under the stop with the close above it does not."""
    clock, plan = _propose(session, market)
    order = _approve_and_place(session, clock, plan)
    _fill_maker(session, clock, market, order)
    session.refresh(plan)
    stop = plan.stop_price
    assert stop is not None
    held = accounting.positions(session, "paper")["BTC"]

    # Wick through the stop, close above it: the stop reads the close.
    _candle_then_tick(
        session,
        clock,
        market + timedelta(minutes=20, seconds=30),
        market + timedelta(minutes=15),
        stop + TICK,
        low=stop * Decimal("0.9"),
        close=stop + TICK,
    )
    assert accounting.positions(session, "paper")["BTC"] == held
    assert (
        session.scalars(select(Event).where(Event.kind == "emergency_stop")).first()
        is None
    )

    # Opens above the stop, closes under it: sell everything at the close.
    crashed = stop * Decimal("0.9")
    _candle_then_tick(
        session,
        clock,
        market + timedelta(minutes=25, seconds=30),
        market + timedelta(minutes=20),
        stop + TICK,
        close=crashed,
    )

    assert accounting.positions(session, "paper").get("BTC", Decimal(0)) == 0
    sell = session.scalars(
        select(Fill)
        .join(Order, Fill.order_id == Order.id)
        .where(Order.side == "sell", Order.type == "market", Order.shadow.is_(False))
    ).one()
    assert sell.quantity == held
    assert sell.liquidity == "taker"
    assert sell.price == crashed * (1 - SLIPPAGE)
    assert sell.fee == sell.price * sell.quantity * TAKER_FEE
    assert (
        session.scalars(select(Event).where(Event.kind == "emergency_stop")).first()
        is not None
    )


def test_expiry(session: Session, market: datetime) -> None:
    """Block 7: unapproved six hours and a minute later, the plan expires
    and shadow orders are written."""
    clock, plan = _propose(session, market)
    clock.advance(timedelta(hours=6, minutes=1))
    changed = approval.sweep(session, _latest_prices(session), clock)
    session.commit()

    assert [p.id for p in changed] == [plan.id]
    assert plan.status == "expired"
    shadows = list(
        session.scalars(
            select(Order).where(Order.plan_id == plan.id, Order.shadow.is_(True))
        )
    )
    assert len(shadows) == len(plan.entry_orders)
    assert all(o.status == "open" for o in shadows)
