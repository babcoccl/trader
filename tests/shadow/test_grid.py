"""Grid shadow sleeve tests (spec G5): ``step`` bar by bar, ``run`` on a
seeded database, and the fee arithmetic of one round trip.
Needs TEST_DATABASE_URL."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import FrozenClock
from trader.ledger.models import Candle, Decision, ShadowMark, ShadowState, ShadowTrade
from trader.shadow import book
from trader.shadow.grid import (
    LOTS,
    SPACING,
    THROUGH,
    Bar,
    GridState,
    Lot,
    level_price,
    run,
    step,
)

from .conftest import pytestmark  # noqa: F401

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
SYMBOLS = ("BTC-USD", "ETH-USD")


def bar(open_: float, high: float, low: float, close: float = 0.0) -> Bar:
    return Bar(open=open_, high=high, low=low, close=close)


def on_state(p0: float = 100.0) -> GridState:
    return GridState(on=True, p0=p0)


def buys(actions):
    return [a for a in actions if a.kind == "buy"]


def sells(actions):
    return [a for a in actions if a.kind == "sell"]


def add_bar(
    session: Session,
    symbol: str,
    open_time: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> None:
    session.add(
        Candle(
            symbol=symbol,
            interval="5m",
            open_time=open_time,
            open=Decimal(str(open_)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal("0"),
            source="seed",
        )
    )


def add_decision(
    session: Session,
    symbol: str,
    ran_at: datetime,
    trend: str = "mixed",
    sideways: bool = True,
    adx: float = 17.2,
) -> None:
    session.add(
        Decision(
            cycle="daily",
            ran_at=ran_at,
            symbol=symbol,
            action="none",
            reason=f"Checked {symbol}.",
            details={
                "regime": {
                    "trend": trend,
                    "volatility": "calm",
                    "sideways": sideways,
                    "vol_percentile": 0.4,
                    "adx": adx,
                }
            },
        )
    )


# --- step, hand-built bars, p0 = 100: level 0 is 100.0, level -1 is
# 95.2381, level 1 is 105.0 (spec G5 test 1).


def test_dip_under_p0_buys_level_zero() -> None:
    actions = step(on_state(), [], bar(100, 101, 99.9), True)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "buy" and a.level == 0 and a.price == pytest.approx(100.0)
    assert a.fee_rate == BITSTAMP.maker_fee and a.slippage == 0.0


def test_low_short_of_through_buys_nothing() -> None:
    assert step(on_state(), [], bar(100, 101, 99.995), True) == []


def test_sells_one_level_up() -> None:
    lots = [Lot(level=0, entry_price=100.0)]
    actions = step(on_state(), lots, bar(100, 105.02, 100), True)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "sell" and a.level == 0 and a.price == pytest.approx(105.0)
    assert a.fee_rate == BITSTAMP.maker_fee and a.slippage == 0.0
    assert a.reason == "sold one level up"


def test_high_exactly_one_level_up_does_not_sell() -> None:
    lots = [Lot(level=0, entry_price=100.0)]
    assert step(on_state(), lots, bar(100, 105.0, 100), True) == []


def test_deep_bar_buys_levels_in_order() -> None:
    actions = step(on_state(), [], bar(100, 100, 85), True)
    assert [a.level for a in actions] == [0, -1, -2, -3]
    assert [a.price for a in actions] == pytest.approx(
        [100.0, 100.0 / 1.05, 100.0 / 1.05**2, 100.0 / 1.05**3]
    )


def test_eleventh_lot_is_not_bought() -> None:
    lots = [Lot(level=j, entry_price=level_price(100.0, j)) for j in range(LOTS)]
    actions = step(on_state(), lots, bar(80, 80, 1), True)
    assert buys(actions) == []


def test_held_level_is_not_bought_twice() -> None:
    lots = [Lot(level=0, entry_price=100.0)]
    actions = step(on_state(), lots, bar(100, 100, 85), True)
    assert [a.level for a in buys(actions)] == [-1, -2, -3]


def test_lot_bought_in_a_bar_is_not_sold_in_it() -> None:
    actions = step(on_state(), [], bar(100, 200, 85), True)
    assert buys(actions) and sells(actions) == []


# --- the sideways reading (spec G5 test 2).


def test_off_sells_every_lot_at_the_open() -> None:
    state = on_state()
    lots = [Lot(level=0, entry_price=100.0), Lot(level=-1, entry_price=95.2381)]
    actions = step(state, lots, bar(99, 99, 98), False)
    assert [a.level for a in actions] == [0, -1]
    for a in actions:
        assert a.kind == "sell" and a.price == pytest.approx(99.0)
        assert a.fee_rate == BITSTAMP.taker_fee
        assert a.slippage == pytest.approx(BITSTAMP.slippage)
        assert a.reason == "sideways reading ended"
    assert state.on is False


def test_turning_on_sets_p0_to_the_open() -> None:
    state = GridState(on=False, p0=None)
    actions = step(state, [], bar(100, 100, 100), True)
    assert actions == []
    assert state.on is True and state.p0 == 100.0


def test_level_price() -> None:
    assert level_price(100.0, 0) == 100.0
    assert level_price(100.0, 1) == pytest.approx(105.0)
    assert level_price(100.0, -1) == pytest.approx(100.0 / 1.05)


# --- run on a seeded database (spec G5 test 3).


def _seed_decisions(session: Session, sideways: bool = True, adx: float = 17.2) -> None:
    for symbol in SYMBOLS:
        add_decision(
            session, symbol, TODAY + timedelta(hours=1), sideways=sideways, adx=adx
        )
    session.commit()


def test_run_first_call_sets_cursor_and_trades_nothing(engine, session) -> None:
    _seed_decisions(session)
    t0 = NOW - timedelta(minutes=5)
    add_bar(session, "BTC-USD", t0, 100, 101, 99.9, 100)
    session.commit()
    run(session, FrozenClock(NOW))
    row = session.scalars(
        select(ShadowState).where(
            ShadowState.sleeve == "grid", ShadowState.symbol == "BTC-USD"
        )
    ).one()
    assert row.state["cursor"] == t0.isoformat()
    assert session.scalar(select(func.count()).select_from(ShadowTrade)) == 0


def test_run_processes_later_candles_like_step(engine, session) -> None:
    _seed_decisions(session)
    t0 = NOW - timedelta(minutes=5)
    add_bar(session, "BTC-USD", t0, 100, 101, 99.9, 100)
    session.commit()
    run(session, FrozenClock(NOW))  # sets the cursor
    t1 = NOW + timedelta(minutes=5)
    add_bar(session, "BTC-USD", t1, 100, 100, 99.9, 100)
    session.commit()
    run(session, FrozenClock(NOW + timedelta(minutes=10)))
    trades = session.scalars(
        select(ShadowTrade).where(ShadowTrade.sleeve == "grid")
    ).all()
    assert len(trades) == 1
    trade = trades[0]
    assert trade.details["level"] == 0
    assert trade.entry_price == Decimal("100")
    assert trade.opened_at == t1 + timedelta(minutes=5)


def test_run_with_no_new_candle_does_nothing(engine, session) -> None:
    _seed_decisions(session)
    t0 = NOW - timedelta(minutes=5)
    add_bar(session, "BTC-USD", t0, 100, 101, 99.9, 100)
    session.commit()
    run(session, FrozenClock(NOW))
    run(session, FrozenClock(NOW))
    rows = session.scalars(
        select(ShadowState).where(ShadowState.sleeve == "grid")
    ).all()
    assert {r.symbol for r in rows} == set(SYMBOLS)
    assert session.scalar(select(func.count()).select_from(ShadowTrade)) == 0


def test_run_without_daily_decision_stays_off(engine, session) -> None:
    t0 = NOW - timedelta(minutes=5)
    add_bar(session, "BTC-USD", t0, 100, 101, 99.9, 100)
    session.commit()
    run(session, FrozenClock(NOW))
    t1 = NOW + timedelta(minutes=5)
    add_bar(session, "BTC-USD", t1, 100, 100, 50, 60)
    session.commit()
    run(session, FrozenClock(NOW + timedelta(minutes=10)))
    assert session.scalar(select(func.count()).select_from(ShadowTrade)) == 0
    assert session.scalar(select(func.count()).select_from(ShadowMark)) == 0


def test_run_marks_once_a_day(engine, session) -> None:
    _seed_decisions(session)
    t0 = NOW - timedelta(minutes=5)
    add_bar(session, "BTC-USD", t0, 100, 100, 99, 100)
    session.commit()
    run(session, FrozenClock(NOW))
    run(session, FrozenClock(NOW + timedelta(minutes=5)))
    marks = session.scalars(select(ShadowMark).where(ShadowMark.sleeve == "grid")).all()
    assert len(marks) == 1
    assert marks[0].day == TODAY.date()
    assert "BTC:" in marks[0].note


# --- fee arithmetic of one round trip (spec G5 test 4).


def test_round_trip_fees_on_a_500_dollar_lot(session) -> None:
    trade = book.buy(
        session,
        "grid",
        "BTC-USD",
        NOW,
        Decimal("100"),
        Decimal("500"),
        BITSTAMP.maker_fee,
        0.0,
        details={"level": 0},
    )
    book.sell(
        session,
        trade,
        NOW + timedelta(minutes=5),
        Decimal("105"),
        BITSTAMP.maker_fee,
        0.0,
        "sold one level up",
    )
    expected = Decimal("500") * Decimal("0.997") * Decimal("1.05") * Decimal(
        "0.997"
    ) - Decimal("500")
    assert trade.pnl_usd is not None
    assert abs(trade.pnl_usd - expected) < Decimal("1e-9")


def test_through_constant_is_one_bp() -> None:
    assert THROUGH == 0.0001
    assert SPACING == 0.05
