"""Engine service: runs the daily decision cycle at 00:05 UTC and the
five-minute intraday evaluation cycle at second 10 of every 5-minute mark.

On start it runs the daily cycle once immediately if today's daily decision
rows are missing (catch-up after downtime). A heartbeat row is written every
30 seconds and on each run.

Run: ``python -m trader.engine`` (Compose service ``engine``).
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
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
from trader.engine import daily_cycle, intraday_cycle
from trader.ledger import accounting
from trader.ledger.models import Candle, Decision
from trader.ops import assets as asset_list
from trader.ops.heartbeat import beat
from trader.shadow import runner as shadow_runner

log = logging.getLogger(__name__)

SERVICE = "engine"


def _past_daily_time(now: datetime) -> bool:
    """True once today's scheduled 00:05 UTC daily run has passed, so the
    startup catch-up only fills a missed run — a restart before 00:05
    waits for the cron instead of deciding early (spec D2)."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now >= today + timedelta(minutes=5)


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
            beat(session, SERVICE, status, detail, clock=self._clock)

    def run_daily(self) -> None:
        with self._session() as session:
            try:
                portfolio = accounting.portfolio_view(
                    session, get_settings().mode, _latest_prices(session)
                )
                rows = daily_cycle.run(session, self._clock, portfolio)
                summary = "; ".join(r.reason for r in rows)
                beat(session, SERVICE, "ok", summary, clock=self._clock)
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
                beat(session, SERVICE, "error", str(exc), clock=self._clock)

    def retry_daily(self) -> None:
        """Re-run the daily cycle while the day has only blocked rows.

        Called every five minutes: a day blocked at 00:05 (missing candle,
        short data outage) gets its decision minutes later instead of
        waiting for the next day's run (spec R4).
        """
        with self._session() as session:
            retry = daily_cycle.needs_retry(session, self._clock)
        if retry:
            self.run_daily()

    def run_intraday(self) -> None:
        with self._session() as session:
            try:
                row = intraday_cycle.run(session, self._clock)
                beat(session, SERVICE, "ok", row.reason, clock=self._clock)
            except Exception as exc:
                log.exception("intraday cycle failed")
                session.rollback()
                session.add(
                    Decision(
                        cycle="intraday",
                        ran_at=self._clock.now(),
                        action="error",
                        reason=f"Intraday cycle failed: {exc}",
                        details={},
                    )
                )
                beat(session, SERVICE, "error", str(exc), clock=self._clock)

    def run_shadow(self) -> None:
        """Run the shadow sleeves (spec G3). Each runs in its own session;
        a failure lands as a ``shadow_error`` event inside ``runner.run``,
        so an escaping exception is a bug and is only logged. No
        ``decisions`` row, no heartbeat change."""
        try:
            shadow_runner.run(self._session, self._clock)
        except Exception:
            log.exception("shadow runner failed")

    def serve(self) -> None:
        with self._session() as session:
            asset_list.record_change(session, self._clock)
        if _past_daily_time(self._clock.now()):
            # catch-up for a missed 00:05 run; no-op if today's rows exist
            self.run_daily()
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
            self.retry_daily,
            "cron",
            minute="*/5",
            second=40,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            self.run_intraday,
            "cron",
            minute="*/5",
            second=10,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            self.run_shadow,
            "cron",
            minute="*/5",
            second=50,
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
