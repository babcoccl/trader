"""Daily digest tests (spec 4.2): a quiet day still yields a digest with
the closing line, a day with a fill lists it, and ``due_day`` only
schedules the build after ``digest_time_utc``. Needs TEST_DATABASE_URL."""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger import accounting
from trader.ledger.models import (
    Balance,
    Candle,
    Decision,
    Digest,
    EquitySnapshot,
    Event,
    Fill,
    Heartbeat,
    Order,
    Plan,
    Setting,
)
from trader.ops import digest

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

DAY = date(2026, 11, 20)
START = datetime(2026, 11, 20, tzinfo=UTC)
END = START + timedelta(days=1)


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture()
def session():
    assert TEST_URL is not None
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Balance,
            Heartbeat,
            Candle,
            Event,
            EquitySnapshot,
            Digest,
            Setting,
        ):
            s.execute(delete(table))
        s.commit()
        yield s
    engine.dispose()


def _quiet_day(session: Session) -> None:
    """A healthy day: equity snapshots, checks and daily decisions, no
    plans or fills."""
    session.add(
        EquitySnapshot(
            time=START + timedelta(hours=1),
            mode="paper",
            equity_usd=Decimal("5000"),
            high_water_mark=Decimal("5000"),
            drawdown=Decimal("0"),
        )
    )
    session.add(
        EquitySnapshot(
            time=END - timedelta(minutes=1),
            mode="paper",
            equity_usd=Decimal("5012.34"),
            high_water_mark=Decimal("5012.34"),
            drawdown=Decimal("0"),
        )
    )
    for symbol in ("BTC-USD", "ETH-USD"):
        session.add(
            Decision(
                cycle="daily",
                ran_at=START + timedelta(minutes=5),
                symbol=symbol,
                action="none",
                reason=(
                    f"Checked {symbol}. 6 of 9 rules positive. Target 30%, "
                    "holding 28%. Gap is under the 20% trading band, so no trade."
                ),
                details={},
            )
        )
    session.add(
        Decision(
            cycle="intraday",
            ran_at=START + timedelta(hours=12),
            action="none",
            reason="Checked prices. Nothing to do.",
            details={},
        )
    )
    session.commit()


def _plan(session: Session, status: str, clock: FrozenClock) -> Plan:
    decision = Decision(
        cycle="daily",
        ran_at=START + timedelta(minutes=5),
        symbol="BTC-USD",
        action="plan_proposed",
        reason="Checked BTC-USD. Proposing a plan to buy the difference.",
        details={},
    )
    session.add(decision)
    session.flush()
    plan = Plan(
        sleeve="trend",
        symbol="BTC-USD",
        side="buy",
        status="pending",
        reference_price=Decimal("60000"),
        target_weight=Decimal("0.3"),
        worst_case_loss=Decimal("100"),
        est_fees=Decimal("5"),
        rationale="I would like to raise BTC-USD.",
        expires_at=START + timedelta(hours=6),
        decided_at=clock.now() if status != "pending" else None,
        decision_id=decision.id,
    )
    session.add(plan)
    session.flush()
    plan.status = status  # type: ignore[assignment]
    return plan


def _fill(session: Session, plan: Plan) -> Fill:
    order = Order(
        plan_id=plan.id,
        venue="paper",
        client_order_id=f"{plan.id}-1",
        symbol="BTC-USD",
        side="buy",
        type="market",
        quantity=Decimal("0.01"),
        status="filled",
        mode="paper",
        shadow=False,
    )
    session.add(order)
    session.flush()
    fill = Fill(
        order_id=order.id,
        time=START + timedelta(hours=9),
        price=Decimal("60000"),
        quantity=Decimal("0.01"),
        fee=Decimal("2.40"),
        fee_currency="USD",
        liquidity="taker",
    )
    session.add(fill)
    return fill


def test_quiet_day_has_closing_line(session: Session) -> None:
    _quiet_day(session)
    row = digest.build(session, DAY)
    session.commit()

    assert row.day == DAY
    assert "No trades today because" in row.body_md
    assert "Checked BTC-USD" in row.body_md  # daily decision reasons listed
    assert "No fills." in row.body_md
    assert row.stats["checks_run"] == 1
    assert row.stats["checks_failed"] == 0
    assert row.stats["fills"] == 0
    assert Decimal(row.stats["equity_usd"]) == Decimal("5012.34")
    assert Decimal(row.stats["equity_change_usd"]) == Decimal("12.34")


def test_day_with_fill_lists_it(session: Session) -> None:
    _quiet_day(session)
    clock = FrozenClock(START + timedelta(hours=9))
    plan = _plan(session, "approved", clock)
    _fill(session, plan)
    session.commit()

    row = digest.build(session, DAY)
    session.commit()

    assert "0.01" in row.body_md and "BTC-USD" in row.body_md
    assert "2.4 USD" in row.body_md
    assert "No trades today" not in row.body_md
    assert row.stats["fills"] == 1
    assert row.stats["plans"]["approved"] == 1


def test_no_daily_decision_still_digests(session: Session) -> None:
    row = digest.build(session, DAY)
    session.commit()
    assert "No trades today because" in row.body_md


def test_rebuild_updates_same_row(session: Session) -> None:
    _quiet_day(session)
    first = digest.build(session, DAY)
    session.commit()
    session.add(
        Event(
            time=START + timedelta(hours=3),
            kind="alarm",
            severity="alarm",
            message="No heartbeat from collector in over 5 minutes.",
            details={"alarm": "service_silent:collector"},
        )
    )
    session.commit()
    second = digest.build(session, DAY)
    session.commit()
    assert second.id == first.id
    assert "collector" in second.body_md
    assert second.stats["alarms_raised"] == 1


def test_due_day_respects_digest_time(session: Session) -> None:
    before = FrozenClock(datetime(2026, 11, 21, 0, 29, tzinfo=UTC))
    assert digest.due_day(session, before) is None

    after = FrozenClock(datetime(2026, 11, 21, 0, 30, tzinfo=UTC))
    assert digest.due_day(session, after) == DAY

    digest.build(session, DAY)
    session.commit()
    assert digest.due_day(session, after) is None  # already written


def test_due_day_uses_setting(session: Session) -> None:
    session.add(Setting(key="digest_time_utc", value="06:15", updated_at=START))
    session.commit()
    clock = FrozenClock(datetime(2026, 11, 21, 0, 30, tzinfo=UTC))
    assert digest.due_day(session, clock) is None
    later = FrozenClock(datetime(2026, 11, 21, 6, 15, tzinfo=UTC))
    assert digest.due_day(session, later) == DAY


def test_equity_falls_back_to_ledger(session: Session) -> None:
    # No equity_snapshots: account value comes from the ledger at the
    # day's last 5m closes.
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(START))
    session.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=END - timedelta(minutes=5),
            open=Decimal("60000"),
            high=Decimal("60000"),
            low=Decimal("60000"),
            close=Decimal("60000"),
            volume=Decimal("0"),
            source="fixture",
        )
    )
    session.commit()
    row = digest.build(session, DAY)
    session.commit()
    assert Decimal(row.stats["equity_usd"]) == Decimal("1000")
    assert "1,000.00" in row.body_md
