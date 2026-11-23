"""Shadow sleeve runner (spec G3): runs each shadow module after the
cycles, each in its own session so a failure never touches trading or
another sleeve. An exception becomes one ``shadow_error`` event per
sleeve and UTC day — not a failed cycle, not a watchdog alarm.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import ModuleType

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.ledger.models import Event
from trader.shadow import dip

log = logging.getLogger(__name__)

# Every module listed here exposes NAME, DESCRIPTION and
# run(session, clock).
DAILY: tuple[ModuleType, ...] = (dip,)
INTRADAY: tuple[ModuleType, ...] = ()


def _record_error(
    session_factory: Callable[[], AbstractContextManager[Session]],
    module: ModuleType,
    exc: Exception,
    today: datetime,
) -> None:
    with session_factory() as session:
        already = session.scalars(
            select(Event.id)
            .where(
                Event.kind == "shadow_error",
                Event.time >= today,
                Event.details["sleeve"].as_string() == module.NAME,
            )
            .limit(1)
        ).first()
        if already is not None:
            return
        session.add(
            Event(
                time=datetime.now(UTC),
                kind="shadow_error",
                severity="warning",
                message=str(exc),
                details={"sleeve": module.NAME},
            )
        )


def run(
    session_factory: Callable[[], AbstractContextManager[Session]],
    clock: Clock,
) -> None:
    """Run each shadow module; isolate and record failures."""
    now = clock.now()
    today = datetime(now.year, now.month, now.day, tzinfo=UTC)
    for module in (*DAILY, *INTRADAY):
        try:
            with session_factory() as session:
                module.run(session, clock)
        except Exception as exc:
            log.exception("shadow sleeve %s failed", module.NAME)
            try:
                _record_error(session_factory, module, exc, today)
            except Exception:
                log.exception("recording shadow_error for %s failed", module.NAME)
