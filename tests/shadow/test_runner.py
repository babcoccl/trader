"""Shadow runner tests (spec G3): a failing sleeve never stops the next
one, and one ``shadow_error`` event per sleeve per UTC day.
Needs TEST_DATABASE_URL."""

from datetime import UTC, datetime
from types import ModuleType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger.models import Event
from trader.shadow import runner

from .conftest import pytestmark  # noqa: F401

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def _module(name: str, fn) -> ModuleType:
    m = ModuleType(name)
    m.NAME = name
    m.DESCRIPTION = f"{name} sleeve"
    m.run = fn
    return m


def test_failure_isolated_and_recorded(engine, monkeypatch) -> None:
    ran: list[str] = []

    def boom(session, clock):
        raise RuntimeError("dip exploded")

    def ok(session, clock):
        ran.append("ok")

    monkeypatch.setattr(runner, "DAILY", (_module("bad", boom), _module("ok", ok)))
    runner.run(_factory(engine), FrozenClock(NOW))
    assert ran == ["ok"]

    with Session(engine) as s:
        events = s.scalars(select(Event).where(Event.kind == "shadow_error")).all()
        assert len(events) == 1
        assert events[0].details["sleeve"] == "bad"
        assert events[0].severity == "warning"
        assert "dip exploded" in events[0].message

    # A second run the same day adds no event.
    monkeypatch.setattr(runner, "DAILY", (_module("bad", boom),))
    runner.run(_factory(engine), FrozenClock(NOW))
    with Session(engine) as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "shadow_error")
            )
            == 1
        )


def _factory(engine):
    from contextlib import contextmanager

    @contextmanager
    def scope():
        with Session(engine) as s:
            yield s
            s.commit()

    return scope
