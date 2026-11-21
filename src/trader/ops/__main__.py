"""Watchdog service (spec 4.1): one ``watchdog.check`` pass per minute plus
a heartbeat of its own.

Run: ``python -m trader.ops`` (Compose service ``watchdog``).
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC

from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped]
    BlockingScheduler,
)
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.core.logging import setup as setup_logging
from trader.ledger.models import Event
from trader.ops import watchdog
from trader.ops.heartbeat import beat

log = logging.getLogger(__name__)

SERVICE = watchdog.SERVICE
INTERVAL_SECONDS = 60


class Watchdog:
    def __init__(
        self,
        clock: Clock | None = None,
        session_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._clock = clock or SystemClock()
        self._session = session_factory

    def tick(self) -> None:
        with self._session() as session:
            # Beat before checking: the watchdog must not alarm on itself.
            beat(session, SERVICE, clock=self._clock)
            alarms = watchdog.check(session, self._clock)
            beat(
                session,
                SERVICE,
                "ok",
                f"{len(alarms)} alarm(s) firing" if alarms else "all checks quiet",
                clock=self._clock,
            )

    def tick_safe(self) -> None:
        try:
            self.tick()
        except Exception as exc:
            log.exception("watchdog pass failed")
            with self._session() as session:
                session.add(
                    Event(
                        time=self._clock.now(),
                        kind="watchdog_error",
                        severity="error",
                        message=f"Watchdog pass failed: {exc}",
                        details={},
                    )
                )
                beat(session, SERVICE, "error", str(exc), clock=self._clock)

    def serve(self) -> None:
        scheduler = BlockingScheduler(timezone=UTC)
        # First run after one interval: the other services get their start-up
        # heartbeats down before silence is judged.
        scheduler.add_job(
            self.tick_safe,
            "interval",
            seconds=INTERVAL_SECONDS,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()


def main() -> None:
    settings = get_settings()
    setup_logging(service=SERVICE, level=settings.log_level)
    Watchdog().serve()


if __name__ == "__main__":
    main()
