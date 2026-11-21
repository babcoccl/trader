"""Accounting tests (spec 2.3): deposits, fills, rebuild parity, cost basis,
oversell. Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger import accounting
from trader.ledger.models import (
    Balance,
    Decision,
    Event,
    Fill,
    Order,
    Plan,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ids = count(1)


def _alembic(connection, *args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    # Reuse the open connection: a fresh engine per call leaks connections
    # and hits Postgres' client limit under hypothesis examples.
    cfg.attributes["connection"] = connection
    getattr(command, args[0])(cfg, *args[1:])


_engine = None


def _shared_engine():
    """One engine for the whole module: an engine per hypothesis example
    exhausts Postgres' client limit."""
    global _engine
    if _engine is None:
        assert TEST_URL is not None
        _engine = create_engine(TEST_URL)
        with _engine.begin() as connection:
            _alembic(connection, "upgrade", "head")
    return _engine


@contextmanager
def _session():
    engine = _shared_engine()
    with Session(engine) as s:
        try:
            _clean(s)
            yield s
        finally:
            # Other suites' fixtures wipe decisions but not plans/fills:
            # leave nothing behind that their cleanup cannot remove.
            s.rollback()
            _clean(s)


def _clean(s: Session) -> None:
    for table in (Fill, Order, Plan, Decision, Balance, Event):
        s.execute(delete(table))
    s.commit()


@pytest.fixture()
def session():
    with _session() as s:
        yield s


def _fill(
    session: Session,
    side: str,
    price: Decimal,
    qty: Decimal,
    fee: Decimal,
    time: datetime,
    symbol: str = "BTC-USD",
    mode: str = "paper",
    shadow: bool = False,
) -> Fill:
    """Build the Decision -> Plan -> Order -> Fill chain a fill needs."""
    n = next(_ids)
    decision = Decision(
        cycle="daily", ran_at=time, action="none", reason="test", details={}
    )
    session.add(decision)
    session.flush()
    plan = Plan(
        sleeve="trend",
        symbol=symbol,
        side=side,
        status="approved",
        reference_price=price,
        target_weight=Decimal("0.5"),
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="test",
        expires_at=time,
        decision_id=decision.id,
    )
    session.add(plan)
    session.flush()
    order = Order(
        plan_id=plan.id,
        venue="paper",
        client_order_id=f"t-{n}",
        symbol=symbol,
        side=side,
        type="market",
        quantity=qty,
        status="filled",
        mode=mode,
        shadow=shadow,
    )
    session.add(order)
    session.flush()
    fill = Fill(
        order_id=order.id,
        time=time,
        price=price,
        quantity=qty,
        fee=fee,
        fee_currency="USD",
        liquidity="taker",
    )
    session.add(fill)
    session.flush()
    return fill


def test_deposit_credits_cash(session: Session) -> None:
    clock = FrozenClock(T0)
    accounting.deposit(session, "paper", Decimal("5000"), clock)
    session.commit()
    assert accounting.cash(session, "paper") == Decimal("5000")
    assert accounting.positions(session, "paper") == {}
    assert accounting.cash(session, "live") == Decimal(0)


def test_buy_fill_moves_cash_and_position(session: Session) -> None:
    clock = FrozenClock(T0)
    accounting.deposit(session, "paper", Decimal("10000"), clock)
    fill = _fill(session, "buy", Decimal("100"), Decimal("2"), Decimal("0.80"), T0)
    accounting.apply_fill(session, fill)
    session.commit()
    assert accounting.positions(session, "paper") == {"BTC": Decimal("2")}
    # 10000 - 2*100 - 0.80 fee
    assert accounting.cash(session, "paper") == Decimal("9799.2")


def test_fee_reduces_cash(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(T0))
    fill = _fill(session, "buy", Decimal("100"), Decimal("1"), Decimal("5"), T0)
    accounting.apply_fill(session, fill)
    session.commit()
    assert accounting.cash(session, "paper") == Decimal("895")


def test_sell_fill_and_oversell_raises(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(T0))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("1"), Decimal("0"), T0)
    )
    accounting.apply_fill(
        session,
        _fill(
            session,
            "sell",
            Decimal("120"),
            Decimal("0.5"),
            Decimal("0"),
            T0 + timedelta(minutes=1),
        ),
    )
    session.commit()
    assert accounting.positions(session, "paper") == {"BTC": Decimal("0.5")}
    # 1000 - 100 + 0.5*120
    assert accounting.cash(session, "paper") == Decimal("960")
    bad = _fill(
        session,
        "sell",
        Decimal("120"),
        Decimal("1"),
        Decimal("0"),
        T0 + timedelta(minutes=2),
    )
    with pytest.raises(ValueError, match="cannot sell"):
        accounting.apply_fill(session, bad)


def test_overspend_raises(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("50"), FrozenClock(T0))
    fill = _fill(session, "buy", Decimal("100"), Decimal("1"), Decimal("0"), T0)
    with pytest.raises(ValueError, match="exceeds"):
        accounting.apply_fill(session, fill)


def test_shadow_fill_touches_nothing(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(T0))
    fill = _fill(
        session,
        "buy",
        Decimal("100"),
        Decimal("1"),
        Decimal("1"),
        T0,
        shadow=True,
    )
    accounting.apply_fill(session, fill)
    session.commit()
    assert accounting.positions(session, "paper") == {}
    assert accounting.cash(session, "paper") == Decimal("1000")


def test_cost_basis_average(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("100000"), FrozenClock(T0))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("1"), Decimal("0"), T0)
    )
    accounting.apply_fill(
        session,
        _fill(
            session,
            "buy",
            Decimal("200"),
            Decimal("1"),
            Decimal("0"),
            T0 + timedelta(minutes=1),
        ),
    )
    session.commit()
    assert accounting.cost_basis(session, "paper", "BTC-USD") == Decimal("150")
    # Sell half: basis stays.
    accounting.apply_fill(
        session,
        _fill(
            session,
            "sell",
            Decimal("300"),
            Decimal("1"),
            Decimal("0"),
            T0 + timedelta(minutes=2),
        ),
    )
    session.commit()
    assert accounting.cost_basis(session, "paper", "BTC-USD") == Decimal("150")


def test_cost_basis_sell_fee_keeps_basis(session: Session) -> None:
    """A USD fee on a sale adjusts proceeds, not the remaining basis."""
    accounting.deposit(session, "paper", Decimal("100000"), FrozenClock(T0))
    for i, (price, fee) in enumerate(((100, 0), (100, 0))):
        accounting.apply_fill(
            session,
            _fill(
                session,
                "buy",
                Decimal(price),
                Decimal("1"),
                Decimal(fee),
                T0 + timedelta(minutes=i),
            ),
        )
    accounting.apply_fill(
        session,
        _fill(
            session,
            "sell",
            Decimal("150"),
            Decimal("1"),
            Decimal("1"),
            T0 + timedelta(minutes=2),
        ),
    )
    session.commit()
    assert accounting.cost_basis(session, "paper", "BTC-USD") == Decimal("100")


def test_cost_basis_base_currency_fee(session: Session) -> None:
    """A fee paid in BTC shrinks the tracked holding, mirroring _apply."""
    accounting.deposit(session, "paper", Decimal("100000"), FrozenClock(T0))
    fill = _fill(session, "buy", Decimal("100"), Decimal("1"), Decimal("0"), T0)
    # Rewrite the fee to base currency.
    fill.fee = Decimal("0.01")
    fill.fee_currency = "BTC"
    accounting.apply_fill(session, fill)
    session.commit()
    assert accounting.positions(session, "paper") == {"BTC": Decimal("0.99")}
    basis = accounting.cost_basis(session, "paper", "BTC-USD")
    assert basis == pytest.approx(Decimal("101.01010101010101"))
    # Full exit leaves no residual holding or basis.
    accounting.apply_fill(
        session,
        _fill(
            session,
            "sell",
            Decimal("150"),
            Decimal("0.99"),
            Decimal("0"),
            T0 + timedelta(minutes=1),
        ),
    )
    session.commit()
    assert accounting.positions(session, "paper") == {}
    assert accounting.cost_basis(session, "paper", "BTC-USD") == Decimal(0)


def test_equity_and_portfolio_view(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("10000"), FrozenClock(T0))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("10"), Decimal("0"), T0)
    )
    session.commit()
    prices = {"BTC-USD": Decimal("110"), "ETH-USD": Decimal("5")}
    # 9000 cash + 10 * 110
    assert accounting.equity(session, "paper", prices) == Decimal("10100")
    view = accounting.portfolio_view(session, "paper", prices)
    assert view.equity_usd == Decimal("10100")
    assert view.weights["BTC-USD"] == pytest.approx(1100 / 10100)
    assert "ETH-USD" not in view.weights


def test_missing_price_raises(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("10000"), FrozenClock(T0))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("1"), Decimal("0"), T0)
    )
    session.commit()
    with pytest.raises(KeyError):
        accounting.equity(session, "paper", {"ETH-USD": Decimal("5")})


_money = st.decimals(
    min_value=Decimal("0.00000001"), max_value=Decimal("100000"), places=8
)
_op = st.tuples(st.sampled_from(["buy", "sell"]), _money, _money, _money)


@given(data=st.data())
@settings(max_examples=30, deadline=None, derandomize=True)
def test_apply_then_rebuild_matches(data: st.DataObject) -> None:
    """Random fill sequences: balances via apply_fill equal balances via
    rebuild, including the deposit path."""
    with _session() as session:
        clock = FrozenClock(T0)
        accounting.deposit(session, "paper", Decimal("1000000000"), clock)
        ops = data.draw(st.lists(_op, min_size=1, max_size=25))
        held = Decimal(0)
        for i, (side, price, qty, fee) in enumerate(ops):
            if side == "sell" and qty > held:
                side = "buy"
            # Keep every op affordable: notional <= 1e6 against 1e9 cash,
            # fee <= 1% of notional.
            qty = min(qty, Decimal("1000000") / price)
            fee = min(fee, price * qty / 100)
            fill = _fill(
                session,
                side,
                price,
                qty,
                fee,
                T0 + timedelta(minutes=i + 1),
            )
            accounting.apply_fill(session, fill)
            held = accounting.positions(session, "paper").get("BTC", Decimal(0))
        session.commit()
        expected_positions = accounting.positions(session, "paper")
        expected_cash = accounting.cash(session, "paper")

        accounting.rebuild(session, "paper")
        session.commit()

        assert accounting.positions(session, "paper") == expected_positions
        assert accounting.cash(session, "paper") == expected_cash


def test_rebuild_after_manual_corruption(session: Session) -> None:
    """rebuild rewrites balances from fills even after the rows drift."""
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(T0))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("2"), Decimal("0"), T0)
    )
    session.commit()
    session.execute(delete(Balance))
    session.commit()
    accounting.rebuild(session, "paper")
    session.commit()
    assert accounting.positions(session, "paper") == {"BTC": Decimal("2")}
    assert accounting.cash(session, "paper") == Decimal("800")


def test_rebuild_replays_deposits_in_time_order(session: Session) -> None:
    """A deposit dated after a fill cannot fund that fill on rebuild."""
    later = T0 + timedelta(days=1)
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(later))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("2"), Decimal("0"), T0)
    )
    session.commit()
    with pytest.raises(ValueError):
        accounting.rebuild(session, "paper")


def test_rebuild_deposit_between_fills(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("200"), FrozenClock(T0))
    t1 = T0 + timedelta(hours=1)
    t2 = T0 + timedelta(hours=2)
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("100"), Decimal("2"), Decimal("0"), t1)
    )
    accounting.deposit(session, "paper", Decimal("300"), FrozenClock(t2))
    accounting.apply_fill(
        session, _fill(session, "buy", Decimal("150"), Decimal("2"), Decimal("0"), t2)
    )
    session.commit()
    accounting.rebuild(session, "paper")
    session.commit()
    assert accounting.positions(session, "paper") == {"BTC": Decimal("4")}
    assert accounting.cash(session, "paper") == Decimal("0")
