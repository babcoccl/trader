"""Weekly paper report tests (spec 5.1): the $5,000 seed, the Monday
schedule, the backtest tracking gap, and the report's uptime, alarm and
approval sections. Needs TEST_DATABASE_URL."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    Digest,
    EquitySnapshot,
    Event,
    Fill,
    Heartbeat,
    Order,
    Plan,
    Setting,
)
from trader.ops import paper_report

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

# Week: Monday 2026-11-16 00:00 to Monday 2026-11-23 00:00 UTC.
WEEK_START = datetime(2026, 11, 16, tzinfo=UTC)
WEEK_END = datetime(2026, 11, 23, tzinfo=UTC)
MONDAY_30 = datetime(2026, 11, 23, 0, 30, tzinfo=UTC)


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


def _daily_candles(session: Session) -> None:
    """One week of 1d candles for both symbols — too short a history for
    any rule to fire, so the backtest return over the week is 0."""
    for symbol in ("BTC-USD", "ETH-USD"):
        for i in range(7):
            day = WEEK_START + timedelta(days=i)
            session.add(
                Candle(
                    symbol=symbol,
                    interval="1d",
                    open_time=day,
                    open=Decimal("60000"),
                    high=Decimal("60000"),
                    low=Decimal("60000"),
                    close=Decimal("60000"),
                    volume=Decimal("1"),
                    source="fixture",
                )
            )
    session.commit()


def _snapshots(session: Session, first: Decimal, last: Decimal) -> None:
    session.add(
        EquitySnapshot(
            time=WEEK_START + timedelta(hours=1),
            mode="paper",
            equity_usd=first,
            high_water_mark=first,
            drawdown=Decimal("0"),
        )
    )
    session.add(
        EquitySnapshot(
            time=WEEK_END - timedelta(minutes=1),
            mode="paper",
            equity_usd=last,
            high_water_mark=last,
            drawdown=Decimal("0"),
        )
    )
    session.commit()


def test_seed_deposits_once(session: Session) -> None:
    clock = FrozenClock(WEEK_START)
    assert paper_report.ensure_seeded(session, clock) is True
    assert accounting.cash(session, "paper") == Decimal("5000")
    assert paper_report.ensure_seeded(session, clock) is False
    assert accounting.cash(session, "paper") == Decimal("5000")


def test_seed_skips_when_funded(session: Session) -> None:
    accounting.deposit(session, "paper", Decimal("1000"), FrozenClock(WEEK_START))
    session.commit()
    assert paper_report.ensure_seeded(session, FrozenClock(WEEK_START)) is False
    assert accounting.cash(session, "paper") == Decimal("1000")


def test_seed_skips_in_live_mode(session: Session, monkeypatch) -> None:
    monkeypatch.setenv("TRADER_MODE", "live")
    get_settings.cache_clear()
    try:
        assert paper_report.ensure_seeded(session, FrozenClock(WEEK_START)) is False
    finally:
        get_settings.cache_clear()


def test_due_week_waits_for_digest_time(session: Session) -> None:
    _daily_candles(session)
    early = FrozenClock(datetime(2026, 11, 23, 0, 29, tzinfo=UTC))
    assert paper_report.due_week(session, early) is None
    assert paper_report.due_week(session, FrozenClock(MONDAY_30)) == (
        WEEK_START,
        WEEK_END,
    )


def test_due_week_waits_for_sundays_candle(session: Session) -> None:
    session.add(Setting(key="digest_time_utc", value="00:00", updated_at=WEEK_START))
    session.commit()
    midnight = FrozenClock(WEEK_END)
    assert paper_report.due_week(session, midnight) is None
    _daily_candles(session)
    assert paper_report.due_week(session, midnight) == (WEEK_START, WEEK_END)


def test_due_week_catches_up_before_a_later_days_cutoff(session: Session) -> None:
    # Tuesday 00:10 is before that day's 00:30, but Monday's report is due.
    tuesday = FrozenClock(datetime(2026, 11, 24, 0, 10, tzinfo=UTC))
    assert paper_report.due_week(session, tuesday) == (WEEK_START, WEEK_END)


def test_due_week_marks_done_and_midweek_build_dedupes(session: Session) -> None:
    paper_report.build(session, WEEK_START, WEEK_END)
    session.commit()
    clock = FrozenClock(MONDAY_30)
    assert paper_report.due_week(session, clock) is None
    # A week later the next window is due.
    # Next Tuesday: due even though that Sunday's candle never arrived.
    later = FrozenClock(MONDAY_30 + timedelta(days=8))
    assert paper_report.due_week(session, later) == (
        WEEK_END,
        WEEK_END + timedelta(days=7),
    )


def test_partial_report_does_not_settle_week(session: Session) -> None:
    # An on-demand report covering only part of the week must not stop
    # the watchdog writing the full Monday-to-Monday one.
    _daily_candles(session)
    paper_report.build(session, WEEK_START, WEEK_START + timedelta(days=3))
    session.commit()
    assert paper_report.due_week(session, FrozenClock(MONDAY_30)) == (
        WEEK_START,
        WEEK_END,
    )


def test_tracking_gap_points(session: Session) -> None:
    _daily_candles(session)
    _snapshots(session, Decimal("5000"), Decimal("5500"))
    gap = paper_report.tracking_gap(session, "paper", WEEK_START, WEEK_END)
    assert gap["paper_return"] == pytest.approx(0.10)
    # Seven days of candles: no rule has enough history, backtest holds
    # cash, so its return is 0 and the whole paper gain is the gap.
    assert gap["backtest_return"] == pytest.approx(0.0)
    assert gap["gap_points"] == pytest.approx(10.0)


def test_gap_none_when_a_day_lacks_candles(session: Session) -> None:
    _daily_candles(session)
    _snapshots(session, Decimal("5000"), Decimal("5500"))
    session.execute(
        delete(Candle).where(
            Candle.interval == "1d",
            Candle.open_time == WEEK_START + timedelta(days=3),
        )
    )
    session.commit()
    gap = paper_report.tracking_gap(session, "paper", WEEK_START, WEEK_END)
    assert gap["paper_return"] == pytest.approx(0.10)
    assert gap["backtest_return"] is None
    assert gap["gap_points"] is None


def test_gap_none_without_coverage(session: Session) -> None:
    gap = paper_report.tracking_gap(session, "paper", WEEK_START, WEEK_END)
    assert gap["gap_points"] is None


def test_build_writes_report_event(session: Session) -> None:
    _daily_candles(session)
    _snapshots(session, Decimal("5000"), Decimal("5500"))
    for minute in range(0, 200):
        session.add(
            Heartbeat(
                service="collector",
                time=WEEK_START + timedelta(minutes=minute),
                status="ok",
                detail="",
            )
        )
    session.add(
        Event(
            time=WEEK_START + timedelta(hours=3),
            kind="alarm",
            severity="alarm",
            message="No heartbeat from collector in over 5 minutes.",
            details={"alarm": "service_silent:collector"},
        )
    )
    decision = Decision(
        cycle="daily",
        ran_at=WEEK_START + timedelta(minutes=5),
        symbol="BTC-USD",
        action="plan_proposed",
        reason="Checked BTC-USD. Proposing a plan to buy the difference.",
        details={},
    )
    session.add(decision)
    session.flush()
    session.add(
        Plan(
            sleeve="trend",
            symbol="BTC-USD",
            side="buy",
            status="approved",
            reference_price=Decimal("60000"),
            target_weight=Decimal("0.3"),
            worst_case_loss=Decimal("100"),
            est_fees=Decimal("5"),
            rationale="I would like to raise BTC-USD.",
            expires_at=WEEK_START + timedelta(hours=6),
            created_at=WEEK_START + timedelta(minutes=5),
            decided_at=WEEK_START + timedelta(hours=1, minutes=35),
            decision_id=decision.id,
        )
    )
    session.commit()

    row = paper_report.build(session, WEEK_START, WEEK_END)
    session.commit()

    assert row.kind == "weekly_report"
    assert row.details["week_start"] == "2026-11-16"
    body = row.details["body_md"]
    assert "tracking gap" in body.lower()
    assert "+10.00 percentage points" in body
    assert "collector" in body
    assert "No heartbeat from collector" in body
    assert "answered in 90 minutes" in body
    stats = row.details
    assert stats["alarms_raised"] == 1
    assert stats["approvals"][0]["minutes"] == pytest.approx(90.0)
    assert stats["approval_median_minutes"] == pytest.approx(90.0)
    # 200 heartbeat minutes out of 10080.
    assert stats["uptime"]["collector"] == round(200 / 10080, 4)
    assert Decimal(stats["equity_end_usd"]) == Decimal("5500")


def test_build_handles_empty_week(session: Session) -> None:
    row = paper_report.build(session, WEEK_START, WEEK_END)
    session.commit()
    assert row.kind == "weekly_report"
    assert "not computable" in row.details["body_md"]
    assert row.details["alarms_raised"] == 0
    assert row.details["approvals"] == []
