"""Market-data collector service.

Every minute: store the last two closed 5m candles per symbol and, after
00:01 UTC, yesterday's 1d candle. On start it refills every gap in the
backfill windows (daily candles from each asset's ``daily_start``, 5m
candles for the last 30 days). A heartbeat row is written each loop. On a
source failure the backup source takes over and an `events` row is written.

Run: ``python -m trader.data.collector`` (Compose service ``collector``).
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped]
    BlockingScheduler,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.core.logging import setup as setup_logging
from trader.data import candles as candles_mod
from trader.data.sources.base import (
    ASSETS,
    SYMBOLS,
    Candle,
    CandleSource,
    period_seconds,
)
from trader.data.sources.bitstamp import BitstampSource
from trader.data.sources.coinbase import CoinbaseSource
from trader.ledger.models import Event, Order, Plan
from trader.ops.heartbeat import beat

log = logging.getLogger(__name__)

FIVEM_BACKFILL_DAYS = 30

_OPEN_ORDER_STATUSES = ("new", "open", "partially_filled")


def _floor(t: datetime, seconds: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + timedelta(seconds=(int(t.timestamp()) // seconds) * seconds)


class Collector:
    def __init__(
        self,
        sources: list[CandleSource] | None = None,
        clock: Clock | None = None,
        session_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._sources: list[CandleSource] = (
            list(sources) if sources else [BitstampSource(), CoinbaseSource()]
        )
        self._source_idx = 0
        self._clock = clock or SystemClock()
        self._session = session_factory
        self._outage = False

    def _event(self, session: Session, severity: str, message: str) -> None:
        session.add(
            Event(
                time=self._clock.now(),
                kind="collector_source_failover",
                severity=severity,
                message=message,
                details={},
            )
        )

    def _fetch(
        self,
        session: Session,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Fetch with failover to the next source on error."""
        for attempt in range(len(self._sources)):
            src = self._sources[self._source_idx]
            try:
                return src.fetch(symbol, interval, start, end)
            except Exception as exc:
                log.warning(
                    "source %s failed for %s %s: %s", src.name, symbol, interval, exc
                )
                last = attempt == len(self._sources) - 1
                self._source_idx = (self._source_idx + 1) % len(self._sources)
                self._event(
                    session,
                    "error" if last else "warning",
                    f"{src.name} failed for {symbol} {interval}: {exc}; "
                    f"switched to {self._sources[self._source_idx].name}",
                )
                if last:
                    self._outage = True
                    return []
        return []

    def _store(
        self,
        session: Session,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> int:
        got = self._fetch(session, symbol, interval, start, end)
        return candles_mod.upsert(session, got, now=self._clock.now())

    def _heartbeat(self, session: Session, status: str, detail: str) -> None:
        beat(session, "collector", status, detail, clock=self._clock)

    def _feed_symbols(self, session: Session) -> list[str]:
        """Symbols whose candles are still collected: ``SYMBOLS`` plus any
        symbol removed from the list that still has an open order or an
        approved plan — its fills need new candles until it settles."""
        wanted = set(SYMBOLS)
        wanted |= set(
            session.scalars(
                select(Order.symbol)
                .where(Order.status.in_(_OPEN_ORDER_STATUSES))
                .distinct()
            )
        )
        wanted |= set(
            session.scalars(
                select(Plan.symbol).where(Plan.status == "approved").distinct()
            )
        )
        return [s for s in ASSETS if s in wanted]

    def run_once(self, session: Session) -> int:
        """One loop: recent 5m candles, yesterday's 1d, heartbeat. Rows written."""
        now = self._clock.now()
        self._outage = False
        written = 0
        symbols = self._feed_symbols(session)
        # Last two closed 5m candles.
        last_open = _floor(now, 300) - timedelta(seconds=300)
        for symbol in symbols:
            written += self._store(
                session,
                symbol,
                "5m",
                last_open - timedelta(seconds=300),
                last_open + timedelta(seconds=300),
            )
        # Yesterday's daily candle, once 00:01 UTC has passed.
        today = _floor(now, 86400)
        if now >= today + timedelta(minutes=1):
            for symbol in symbols:
                written += self._store(
                    session, symbol, "1d", today - timedelta(days=1), today
                )
        status = "error" if self._outage else "ok"
        detail = f"stored {written} candles"
        if self._outage:
            detail += "; all sources failed for some requests"
        self._heartbeat(session, status, detail)
        return written

    def refill_gaps(self, session: Session) -> None:
        """Backfill windows then repair any gaps still present."""
        now = self._clock.now()
        day_end = _floor(now, 86400)
        fivem_start = _floor(now - timedelta(days=FIVEM_BACKFILL_DAYS), 300)
        symbols = self._feed_symbols(session)
        for symbol in symbols:
            daily_start = ASSETS[symbol].daily_start
            self._store(session, symbol, "1d", daily_start, day_end)
            self._store(session, symbol, "5m", fivem_start, now)
            self._tick_heartbeat(f"backfilled {symbol}")
        for symbol in symbols:
            for interval, start in (
                ("1d", ASSETS[symbol].daily_start),
                ("5m", fivem_start),
            ):
                end = day_end if interval == "1d" else now
                for gap_start, gap_end in candles_mod.find_gaps(
                    session, symbol, interval, start, end
                ):
                    self._store(
                        session,
                        symbol,
                        interval,
                        gap_start,
                        gap_end + timedelta(seconds=period_seconds(interval)),
                    )
            self._tick_heartbeat(f"refilled gaps for {symbol}")

    def _tick_heartbeat(self, detail: str) -> None:
        """Heartbeat in its own committed session, so a long refill still
        lands a row every ~60 s even before its own transaction commits."""
        with self._session() as s:
            self._heartbeat(s, "ok", detail)

    def serve(self) -> None:
        """Blocking service loop: initial gap refill, then one run per minute."""
        self._tick_heartbeat("starting gap refill")
        with self._session() as session:
            self.refill_gaps(session)
        scheduler = BlockingScheduler(timezone=UTC)
        scheduler.add_job(
            self._tick, "interval", minutes=1, coalesce=True, max_instances=1
        )
        scheduler.start()

    def _tick(self) -> None:
        with self._session() as session:
            try:
                self.run_once(session)
            except Exception as exc:
                log.exception("collector loop failed")
                session.rollback()
                self._event(session, "error", f"collector loop failed: {exc}")
                self._heartbeat(session, "error", str(exc))


def main() -> None:
    settings = get_settings()
    setup_logging(service="collector", level=settings.log_level)
    Collector().serve()


if __name__ == "__main__":
    main()
