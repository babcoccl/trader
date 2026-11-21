"""Engine service: runs the daily decision cycle at 00:05 UTC.

On start it runs once immediately if today's daily decision rows are missing
(catch-up after downtime). A heartbeat row is written every 30 seconds and on
each run.

Run: ``python -m trader.engine`` (Compose service ``engine``).
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC
from decimal import Decimal

from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped]
    BlockingScheduler,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.core.logging import setup as setup_logging
from trader.data.sources.base import SYMBOLS
from trader.engine import daily_cycle
from trader.ledger import accounting
from trader.ledger.models import Candle, Decision, Heartbeat

log = logging.getLogger(__name__)

SERVICE = "engine"


def _latest_prices(session: Session) -> dict[str, Decimal]:
    """Most recent stored close per symbol, for marking positions."""
    prices: dict[str, Decimal] = {}
    for symbol in SYMBOLS:
        close = session.scalar(
            select(Candle.close)
            .where(Candle.symbol == symbol)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


class Engine:
    def __init__(
        self,
        clock: Clock | None = None,
        session_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._clock = clock or SystemClock()
        self._session = session_factory

    def _heartbeat(self, status: str, detail: str) -> None:
        with self._session() as session:
            session.add(
                Heartbeat(
                    service=SERVICE,
                    time=self._clock.now(),
                    status=status,
                    detail=detail,
                )
            )

    def run_daily(self) -> None:
        with self._session() as session:
            try:
                portfolio = accounting.portfolio_view(
                    session, get_settings().mode, _latest_prices(session)
                )
                rows = daily_cycle.run(session, self._clock, portfolio)
                summary = "; ".join(r.reason for r in rows)
                session.add(
                    Heartbeat(
                        service=SERVICE,
                        time=self._clock.now(),
                        status="ok",
                        detail=summary[:2000],
                    )
                )
            except Exception as exc:
                log.exception("daily cycle failed")
                session.rollback()
                # Convention: every scheduled cycle leaves a decisions row.
                session.add(
                    Decision(
                        cycle="daily",
                        ran_at=self._clock.now(),
                        action="error",
                        reason=f"Daily cycle failed: {exc}",
                        details={},
                    )
                )
                session.add(
                    Heartbeat(
                        service=SERVICE,
                        time=self._clock.now(),
                        status="error",
                        detail=str(exc),
                    )
                )

    def serve(self) -> None:
        self.run_daily()  # catch-up: no-op if today's rows already exist
        scheduler = BlockingScheduler(timezone=UTC)
        scheduler.add_job(
            self.run_daily,
            "cron",
            hour=0,
            minute=5,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            self._heartbeat,
            "interval",
            seconds=30,
            args=["ok", "idle"],
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()


def main() -> None:
    settings = get_settings()
    setup_logging(service=SERVICE, level=settings.log_level)
    Engine().serve()


if __name__ == "__main__":
    main()
