"""Watchdog tests (spec 4.1): each alarm condition on a frozen clock, an
alarm fires once rather than every minute, and it clears. Needs
TEST_DATABASE_URL."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger import accounting
from trader.ledger.models import (
    Balance,
    Candle,
    Decision,
    Event,
    Fill,
    Heartbeat,
    Order,
    Plan,
)
from trader.ops import watchdog
from trader.ops.heartbeat import beat

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


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
        for table in (Fill, Order, Plan, Decision, Balance, Heartbeat, Candle, Event):
            s.execute(delete(table))
        s.commit()
        yield s
    engine.dispose()


def _clock() -> FrozenClock:
    return FrozenClock(NOW)


def _healthy(session: Session, now: datetime = NOW) -> None:
    """Rows that keep every condition quiet."""
    for service in watchdog.EXPECTED_SERVICES:
        session.add(Heartbeat(service=service, time=now, status="ok", detail=""))
    session.add(
        Decision(
            cycle="daily",
            ran_at=now.replace(hour=0, minute=5),
            action="none",
            reason="daily ok",
            details={},
        )
    )
    session.add(
        Decision(
            cycle="intraday",
            ran_at=now,
            action="none",
            reason="intraday ok",
            details={},
        )
    )
    for symbol in ("BTC-USD", "ETH-USD"):
        session.add(
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=now - timedelta(minutes=5),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("0"),
                source="fixture",
            )
        )
    session.commit()


def _events(session: Session, kind: str, alarm_key: str | None = None) -> list[Event]:
    rows = session.scalars(
        select(Event).where(Event.kind == kind).order_by(Event.id)
    ).all()
    if alarm_key is not None:
        rows = [e for e in rows if e.details.get("alarm") == alarm_key]
    return list(rows)


def test_quiet_when_healthy(session: Session) -> None:
    _healthy(session)
    alarms = watchdog.check(session, _clock())
    session.commit()
    assert alarms == []
    assert _events(session, "alarm") == []


def test_silent_service_raises_once_then_clears(session: Session) -> None:
    _healthy(session)
    session.execute(delete(Heartbeat).where(Heartbeat.service == "collector"))
    session.add(
        Heartbeat(
            service="collector",
            time=NOW - timedelta(minutes=10),
            status="ok",
            detail="",
        )
    )
    session.commit()

    alarms = watchdog.check(session, _clock())
    session.commit()
    key = "service_silent:collector"
    assert [a.key for a in alarms] == [key]
    raised = _events(session, "alarm", key)
    assert len(raised) == 1
    assert "collector" in raised[0].message

    # A second pass while still silent raises nothing new.
    alarms = watchdog.check(session, _clock())
    session.commit()
    assert [a.key for a in alarms] == [key]
    assert len(_events(session, "alarm", key)) == 1

    # A fresh heartbeat clears it.
    session.add(Heartbeat(service="collector", time=NOW, status="ok", detail=""))
    session.commit()
    alarms = watchdog.check(session, _clock())
    session.commit()
    assert alarms == []
    cleared = _events(session, "alarm_cleared", key)
    assert len(cleared) == 1


def test_service_with_no_heartbeat_is_silent(session: Session) -> None:
    _healthy(session)
    session.execute(delete(Heartbeat).where(Heartbeat.service == "executor"))
    session.commit()
    alarms = watchdog.check(session, _clock())
    assert [a.key for a in alarms] == ["service_silent:executor"]


def test_daily_decision_missing_after_0015(session: Session) -> None:
    _healthy(session)
    session.execute(delete(Decision).where(Decision.cycle == "daily"))
    session.commit()

    # Before 00:15 no alarm; after it, one.
    early = FrozenClock(NOW.replace(hour=0, minute=14))
    assert watchdog.check(session, early) == []
    session.commit()
    late = FrozenClock(NOW.replace(hour=0, minute=16))
    alarms = watchdog.check(session, late)
    session.commit()
    assert [a.key for a in alarms] == ["daily_decision_missing"]

    # Rows for today end the alarm.
    session.add(
        Decision(
            cycle="daily",
            ran_at=NOW.replace(hour=0, minute=5),
            action="none",
            reason="ok",
            details={},
        )
    )
    session.commit()
    assert watchdog.check(session, late) == []
    assert len(_events(session, "alarm_cleared", "daily_decision_missing")) == 1


def test_intraday_stale(session: Session) -> None:
    _healthy(session)
    session.execute(delete(Decision).where(Decision.cycle == "intraday"))
    session.add(
        Decision(
            cycle="intraday",
            ran_at=NOW - timedelta(minutes=16),
            action="none",
            reason="old",
            details={},
        )
    )
    session.commit()
    alarms = watchdog.check(session, _clock())
    assert [a.key for a in alarms] == ["intraday_stale"]


def test_candle_stale(session: Session) -> None:
    _healthy(session)
    session.execute(delete(Candle).where(Candle.symbol == "BTC-USD"))
    session.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=NOW - timedelta(minutes=30),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("0"),
            source="fixture",
        )
    )
    session.commit()
    alarms = watchdog.check(session, _clock())
    assert [a.key for a in alarms] == ["candle_stale:BTC-USD"]


def test_balances_mismatch(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _healthy(session)

    class FakeAdapter:
        def balances(self) -> dict[str, Decimal]:
            return {"USD": Decimal("999"), "BTC": Decimal("1")}

    monkeypatch.setattr(watchdog, "_adapter_for", lambda s, mode, clock: FakeAdapter())
    alarms = watchdog.check(session, _clock())
    session.commit()
    assert [a.key for a in alarms] == ["balances_mismatch"]
    assert len(_events(session, "alarm", "balances_mismatch")) == 1


def test_heartbeat_summary_every_three_hours(session: Session) -> None:
    _healthy(session)
    clock = _clock()
    watchdog.check(session, clock)
    session.commit()
    summaries = _events(session, "heartbeat_summary")
    assert len(summaries) == 1
    assert summaries[0].message.startswith("All services up.")
    assert "price checks" in summaries[0].message
    assert "Nothing to do." in summaries[0].message

    # Within 3 hours: no new summary. After 3 hours: one more.
    clock.advance(timedelta(hours=1))
    watchdog.check(session, clock)
    session.commit()
    assert len(_events(session, "heartbeat_summary")) == 1
    clock.advance(timedelta(hours=2, minutes=1))
    _healthy(session, clock.now())
    watchdog.check(session, clock)
    session.commit()
    assert len(_events(session, "heartbeat_summary")) == 2


def test_summary_reports_alarms(session: Session) -> None:
    _healthy(session)
    session.execute(delete(Heartbeat).where(Heartbeat.service == "api"))
    session.commit()
    watchdog.check(session, _clock())
    session.commit()
    summary = _events(session, "heartbeat_summary")[0]
    assert "service_silent:api" in summary.message
    assert "All services up" not in summary.message


def test_summary_counts_error_decisions(session: Session) -> None:
    # A failed engine cycle records an error decision, not an event.
    _healthy(session)
    session.add(
        Decision(
            cycle="intraday",
            ran_at=NOW,
            action="error",
            reason="Intraday cycle failed: boom",
            details={},
        )
    )
    session.commit()
    watchdog.check(session, _clock())
    session.commit()
    summary = _events(session, "heartbeat_summary")[0]
    assert "1 errors" in summary.message
    assert summary.details["errors"] == 1


def test_beat_writes_row(session: Session) -> None:
    beat(session, "collector", "ok", "loop done")
    session.commit()
    row = session.scalars(
        select(Heartbeat).where(Heartbeat.service == "collector")
    ).one()
    assert row.status == "ok"
    assert row.detail == "loop done"
    assert row.time is not None


def test_beat_defaults(session: Session) -> None:
    beat(session, "engine")
    session.commit()
    row = session.scalars(select(Heartbeat).where(Heartbeat.service == "engine")).one()
    assert row.status == "ok"
    assert row.detail == ""


def test_alarm_count_grows_for_multiple_conditions(session: Session) -> None:
    # Empty database: several conditions fire together and each gets its
    # own event and its own clear later.
    watchdog.check(session, _clock())
    session.commit()
    keys = {e.details["alarm"] for e in _events(session, "alarm")}
    assert "daily_decision_missing" in keys
    assert "intraday_stale" in keys
    assert "balances_mismatch" not in keys  # paper adapter == ledger
    assert "candle_stale:BTC-USD" in keys


def test_paper_balances_checked_against_fill_replay(session: Session) -> None:
    # Real PaperAdapter: stored balances vs deposits+fills replayed.
    _healthy(session)
    accounting.deposit(session, "paper", Decimal("1000"), _clock())
    session.commit()
    watchdog.check(session, _clock())
    session.commit()
    assert not _events(session, "alarm", "balances_mismatch")

    # A balance row that no deposit or fill explains.
    session.add(Balance(time=NOW, mode="paper", asset="USD", quantity=Decimal("900")))
    session.commit()
    alarms = watchdog.check(session, _clock())
    session.commit()
    assert [a.key for a in alarms] == ["balances_mismatch"]
    assert "USD ledger 1000" in alarms[0].message


def test_beat_uses_the_given_clock(session: Session) -> None:
    beat(session, "engine", clock=_clock())
    session.commit()
    row = session.scalars(select(Heartbeat).where(Heartbeat.service == "engine")).one()
    assert row.time == NOW
