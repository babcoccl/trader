"""Approval state machine tests (spec 2.5). Needs TEST_DATABASE_URL
(Compose Postgres)."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import FrozenClock
from trader.execution.paper import PaperAdapter
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
from trader.plans import approval

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


NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _candle(
    session: Session,
    symbol: str = "BTC-USD",
    open_time: datetime | None = None,
    o: str = "60000",
    h: str = "60000",
    low: str = "60000",
    c: str = "60000",
) -> Candle:
    candle = Candle(
        symbol=symbol,
        interval="5m",
        open_time=open_time or (NOW - timedelta(minutes=5)),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1"),
        source="bitstamp",
    )
    session.add(candle)
    session.flush()
    return candle


def _plan(
    session: Session,
    symbol: str = "BTC-USD",
    status: str = "pending",
    quantity: str = "0.1",
    expires_at: datetime | None = None,
    reference_price: str = "60000",
    worst_case_loss: str = "1000",
    est_fees: str = "48",
) -> Plan:
    decision = Decision(
        cycle="daily", ran_at=NOW, action="plan_proposed", reason="seed", details={}
    )
    session.add(decision)
    session.flush()
    plan = Plan(
        sleeve="trend",
        symbol=symbol,
        side="buy",
        status=status,
        reference_price=Decimal(reference_price),
        target_weight=Decimal("0.5"),
        entry_orders=[
            {
                "side": "buy",
                "type": "limit",
                "price": "60000",
                "quantity": quantity,
                "maker_or_cancel": True,
                "reprice_seconds": 300,
                "market_after_seconds": 3600,
            }
        ],
        stop_price=Decimal("45000"),
        worst_case_loss=Decimal(worst_case_loss),
        est_fees=Decimal(est_fees),
        rationale="test plan",
        expires_at=expires_at or (NOW + timedelta(hours=6)),
        decision_id=decision.id,
    )
    session.add(plan)
    session.flush()
    return plan


def _orders(session: Session, plan: Plan) -> list[Order]:
    return list(
        session.scalars(
            select(Order).where(Order.plan_id == plan.id).order_by(Order.id)
        )
    )


def _audits(session: Session, action: str) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


def _clock(at: datetime = NOW) -> FrozenClock:
    return FrozenClock(at)


def _funded(session: Session) -> None:
    """$10k cash and a fresh 5m candle, so the portfolio and risk staleness
    checks see a live market."""
    accounting.deposit(session, "paper", Decimal("10000"), _clock())
    _candle(session)


def test_approve_full_size(session: Session) -> None:
    _funded(session)
    plan = _plan(session)
    approval.approve(session, plan.id, 1.0, "owner", _clock())
    assert plan.status == "approved"
    assert plan.decided_at == NOW
    assert plan.size_fraction == 1
    assert plan.entry_orders[0]["quantity"] == "0.1"
    assert plan.worst_case_loss == Decimal("1000")
    audits = _audits(session, "approve plan")
    assert len(audits) == 1
    assert audits[0].actor == "owner"


def test_approve_half_size_scales_orders_and_loss(session: Session) -> None:
    _funded(session)
    plan = _plan(session)
    approval.approve(session, plan.id, 0.5, "owner", _clock())
    assert plan.status == "approved"
    assert plan.size_fraction == Decimal("0.5")
    assert Decimal(plan.entry_orders[0]["quantity"]) == Decimal("0.05")
    assert plan.worst_case_loss == Decimal("500")
    assert plan.est_fees == Decimal("24")
    # The JSONB change must persist, not just sit in the session.
    session.commit()
    session.expire_all()
    persisted = session.get(Plan, plan.id)
    assert persisted is not None
    assert Decimal(persisted.entry_orders[0]["quantity"]) == Decimal("0.05")
    assert persisted.worst_case_loss == Decimal("500")


def test_approve_quarter_size(session: Session) -> None:
    _funded(session)
    plan = _plan(session, quantity="0.2")
    approval.approve(session, plan.id, Decimal("0.25"), "owner", _clock())
    assert Decimal(plan.entry_orders[0]["quantity"]) == Decimal("0.05")


@pytest.mark.parametrize("bad", [0, 0.3, 1.5, 2, -1])
def test_approve_bad_fraction_refused(session: Session, bad: float) -> None:
    _funded(session)
    plan = _plan(session)
    with pytest.raises(approval.ApprovalError):
        approval.approve(session, plan.id, bad, "owner", _clock())
    assert plan.status == "pending"


def test_approve_twice_refused(session: Session) -> None:
    _funded(session)
    plan = _plan(session)
    approval.approve(session, plan.id, 1.0, "owner", _clock())
    with pytest.raises(approval.ApprovalError):
        approval.approve(session, plan.id, 1.0, "owner", _clock())


def test_approve_after_expiry_refused(session: Session) -> None:
    _funded(session)
    plan = _plan(session, expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(approval.ApprovalError):
        approval.approve(session, plan.id, 1.0, "owner", _clock())
    assert plan.status == "pending"


def test_approve_terminal_plan_refused(session: Session) -> None:
    _funded(session)
    plan = _plan(session, status="expired")
    with pytest.raises(approval.ApprovalError):
        approval.approve(session, plan.id, 1.0, "owner", _clock())


def test_approve_failing_risk_stays_pending(session: Session) -> None:
    # No candles: price data is stale, so risk.check_plan vetoes.
    accounting.deposit(session, "paper", Decimal("10000"), _clock())
    plan = _plan(session)
    with pytest.raises(approval.ApprovalError):
        approval.approve(session, plan.id, 1.0, "owner", _clock())
    assert plan.status == "pending"


def test_approve_minutes_after_candle_mark(session: Session) -> None:
    """Spec R1: the newest 5m candle opened at 11:55 and closed at 12:00, so
    approving at 12:05:30 sees fresh price data (age 330s, not 630s)."""
    _funded(session)  # seeds a 5m candle opened at NOW - 5m
    plan = _plan(session)
    approval.approve(
        session, plan.id, 1.0, "owner", _clock(NOW + timedelta(minutes=5, seconds=30))
    )
    assert plan.status == "approved"


def test_approve_missing_plan(session: Session) -> None:
    with pytest.raises(approval.ApprovalError):
        approval.approve(session, 999, 1.0, "owner", _clock())


def test_deny(session: Session) -> None:
    _funded(session)
    plan = _plan(session)
    approval.deny(session, plan.id, "owner", _clock())
    assert plan.status == "denied"
    assert plan.decided_at == NOW
    orders = _orders(session, plan)
    assert len(orders) == 1
    assert orders[0].shadow is True
    assert orders[0].quantity == Decimal("0.1")  # full size
    assert orders[0].status == "open"
    audits = _audits(session, "denied plan")
    assert len(audits) == 1 and audits[0].actor == "owner"


def test_deny_non_pending_refused(session: Session) -> None:
    plan = _plan(session, status="approved")
    with pytest.raises(approval.ApprovalError):
        approval.deny(session, plan.id, "owner", _clock())


def test_sweep_expires_overdue_plan(session: Session) -> None:
    plan = _plan(session, expires_at=NOW - timedelta(seconds=1))
    changed = approval.sweep(session, {}, _clock())
    assert changed == [plan]
    assert plan.status == "expired"
    assert len(_orders(session, plan)) == 1
    assert _orders(session, plan)[0].shadow is True
    assert len(_audits(session, "expired plan")) == 1


def test_sweep_voids_plan_on_big_move(session: Session) -> None:
    plan = _plan(session, reference_price="60000")
    changed = approval.sweep(session, {"BTC-USD": Decimal("62000")}, _clock())
    assert changed == [plan]
    assert plan.status == "voided"
    assert _orders(session, plan)[0].shadow is True
    assert len(_audits(session, "voided plan")) == 1


def test_sweep_keeps_plan_within_band(session: Session) -> None:
    plan = _plan(session, reference_price="60000")
    changed = approval.sweep(
        session,
        {"BTC-USD": Decimal("61100")},
        _clock(),  # +1.83% < 2%
    )
    assert changed == []
    assert plan.status == "pending"
    assert _orders(session, plan) == []


def test_sweep_expiry_wins_over_void(session: Session) -> None:
    plan = _plan(session, expires_at=NOW - timedelta(seconds=1))
    changed = approval.sweep(session, {"BTC-USD": Decimal("70000")}, _clock())
    assert changed == [plan]
    assert plan.status == "expired"


def test_sweep_ignores_approved_plan(session: Session) -> None:
    plan = _plan(session, status="approved", expires_at=NOW - timedelta(seconds=1))
    assert approval.sweep(session, {"BTC-USD": 1}, _clock()) == []
    assert plan.status == "approved"


def test_shadow_fills_never_touch_balances(session: Session) -> None:
    """A denied plan's shadow orders fill at full size but the ledger
    ignores them: cash and positions do not move."""
    _funded(session)
    plan = _plan(session)
    approval.deny(session, plan.id, "owner", _clock())
    cash_before = accounting.cash(session, "paper")

    order = _orders(session, plan)[0]
    # Candle after the order's creation that trades through the limit.
    _candle(
        session,
        open_time=order.created_at + timedelta(minutes=5),
        o="59990",
        h="60010",
        low="59950",
        c="59990",
    )
    adapter = PaperAdapter(session, BITSTAMP)
    since = order.created_at - timedelta(seconds=1)
    fills = adapter.poll_fills(since)
    assert len(fills) == 1
    assert fills[0].quantity == Decimal("0.1")
    assert fills[0].liquidity == "maker"
    for fill in fills:
        session.add(fill)
        accounting.apply_fill(session, fill)
    session.flush()

    assert accounting.cash(session, "paper") == cash_before
    assert accounting.positions(session, "paper") == {}


def test_shadow_limit_has_no_market_deadline(session: Session) -> None:
    """A shadow limit still fills on a candle more than an hour after the
    plan died: shadow orders track the would-have-been result, so they
    must not convert to market (and their plan is never approved, so the
    executor's time rules never touch them)."""
    _funded(session)
    plan = _plan(session)
    approval.deny(session, plan.id, "owner", _clock())
    order = _orders(session, plan)[0]
    # 13 candles later (> 1h, past LIMIT_TTL): the trade-through finally prints.
    _candle(
        session,
        open_time=order.created_at + timedelta(minutes=70),
        o="60100",
        h="60100",
        low="59900",
        c="59950",
    )
    adapter = PaperAdapter(session, BITSTAMP)
    fills = adapter.poll_fills(order.created_at - timedelta(seconds=1))
    assert len(fills) == 1
    assert fills[0].price == Decimal("60000")


def test_halt_keeps_shadow_orders(session: Session) -> None:
    """A plan denied while trading is halted keeps its shadow order: the
    executor's halt cancels real buys only, so the counterfactual fill
    still happens (and still never touches balances)."""
    from contextlib import contextmanager

    from trader.execution.__main__ import Executor
    from trader.risk import engine as risk

    _funded(session)
    plan = _plan(session)
    approval.deny(session, plan.id, "owner", _clock())
    risk.halt(session, "owner", "test halt")
    _candle(session, open_time=NOW, o="60100", h="60100", low="59900", c="59950")
    session.commit()
    cash_before = accounting.cash(session, "paper")

    @contextmanager
    def _cm():
        yield session
        session.commit()

    Executor(clock=_clock(NOW + timedelta(minutes=5)), session_factory=_cm).tick()

    order = _orders(session, plan)[0]
    assert order.shadow is True
    assert order.status == "filled"
    fills = list(session.scalars(select(Fill).where(Fill.order_id == order.id)))
    assert len(fills) == 1 and fills[0].quantity == Decimal("0.1")
    assert accounting.cash(session, "paper") == cash_before
    assert accounting.positions(session, "paper") == {}


@pytest.fixture()
def auto_approve_on(monkeypatch: pytest.MonkeyPatch):
    """Enable Autonomy Level 2 for one test; settings are lru_cached."""
    from trader.core.config import get_settings

    monkeypatch.setenv("TRADER_AUTO_APPROVE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_auto_approve_disabled_leaves_pending(session: Session) -> None:
    _funded(session)
    plan = _plan(session, quantity="0.01", worst_case_loss="100")
    assert approval.maybe_auto_approve(session, plan, _clock()) is False
    assert plan.status == "pending"
    assert _audits(session, "approve plan") == []


def test_auto_approve_within_bounds(session: Session, auto_approve_on: None) -> None:
    """$600 notional <= $750 and $100 loss = 1% of $10k equity <= 1.5%:
    the plan is approved as actor "auto" and the executor places its
    working orders."""
    from contextlib import contextmanager

    from trader.execution.__main__ import Executor

    _funded(session)
    plan = _plan(session, quantity="0.01", worst_case_loss="100")
    assert approval.maybe_auto_approve(session, plan, _clock()) is True
    assert plan.status == "approved"
    assert plan.decided_at == NOW
    audits = _audits(session, "approve plan")
    assert len(audits) == 1
    assert audits[0].actor == "auto"
    session.commit()

    @contextmanager
    def _cm():
        yield session
        session.commit()

    Executor(clock=_clock(NOW + timedelta(minutes=5)), session_factory=_cm).tick()
    orders = _orders(session, plan)
    assert len(orders) == 1
    assert orders[0].shadow is False
    assert orders[0].status == "open"
    assert orders[0].quantity == Decimal("0.01")


def test_auto_approve_over_notional_stays_pending(
    session: Session, auto_approve_on: None
) -> None:
    _funded(session)
    plan = _plan(session, quantity="0.1", worst_case_loss="100")  # $6000 > $750
    assert approval.maybe_auto_approve(session, plan, _clock()) is False
    assert plan.status == "pending"
    assert _audits(session, "approve plan") == []


def test_auto_approve_over_loss_pct_stays_pending(
    session: Session, auto_approve_on: None
) -> None:
    _funded(session)
    # $200 loss = 2% of $10k equity > 1.5%, though notional is inside the cap.
    plan = _plan(session, quantity="0.01", worst_case_loss="200")
    assert approval.maybe_auto_approve(session, plan, _clock()) is False
    assert plan.status == "pending"


def test_auto_approve_halted_stays_pending(
    session: Session, auto_approve_on: None
) -> None:
    from trader.risk import engine as risk

    _funded(session)
    plan = _plan(session, quantity="0.01", worst_case_loss="100")
    risk.halt(session, "owner", "test halt")
    assert approval.maybe_auto_approve(session, plan, _clock()) is False
    assert plan.status == "pending"


def test_auto_approve_risk_veto_stays_pending(
    session: Session, auto_approve_on: None
) -> None:
    # No candles: price data is stale, so risk.check_plan vetoes the approve.
    accounting.deposit(session, "paper", Decimal("10000"), _clock())
    plan = _plan(session, quantity="0.01", worst_case_loss="100")
    assert approval.maybe_auto_approve(session, plan, _clock()) is False
    assert plan.status == "pending"
