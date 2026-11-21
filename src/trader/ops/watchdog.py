"""Watchdog checks (spec 4.1).

``check`` runs once a minute and turns silence into ``events`` rows. Each
condition has a stable key stored in ``details["alarm"]``; a condition that
starts writes one ``kind="alarm"`` row (never a repeat while it stays
broken), and one ``kind="alarm_cleared"`` row when it ends. State is derived
from the event log itself, so a restart loses nothing.

Conditions:

- a service's newest heartbeat is older than ``silence_alarm_minutes``
  (``settings`` table, default 5) or it never wrote one;
- no ``cycle="daily"`` decision rows for today once 00:15 UTC has passed;
- no ``cycle="intraday"`` decision in the last 15 minutes;
- a symbol's newest 5m candle is stale (same convention as the intraday
  cycle: more than 10 minutes since the bar closed);
- ledger balances differ from the execution adapter's ``balances()``
  (checked only where an adapter exists: paper mode today; spec 5.2 adds
  the live one).

Every 3 hours ``check`` also writes one ``kind="heartbeat_summary"`` event
counting the price checks (intraday decisions), errors and alarm activity
of the window.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock
from trader.core.config import get_settings
from trader.data.sources.base import SYMBOLS
from trader.execution.base import ExecutionAdapter
from trader.execution.paper import PaperAdapter
from trader.ledger import accounting
from trader.ledger.models import Candle, Decision, Event, Heartbeat, Setting

log = logging.getLogger(__name__)

SERVICE = "watchdog"

# Services expected to heartbeat at least every 60 seconds (spec section A).
EXPECTED_SERVICES = ("api", "collector", "engine", "executor", SERVICE)

DEFAULT_SILENCE_MINUTES = 5
DAILY_CUTOFF = timedelta(hours=0, minutes=15)  # past 00:15 UTC
INTRADAY_STALE = timedelta(minutes=15)
CANDLE_STALE = timedelta(minutes=10)
CANDLE_PERIOD = timedelta(minutes=5)
SUMMARY_EVERY = timedelta(hours=3)

_ALARM_KINDS = ("alarm", "alarm_cleared")


@dataclass(frozen=True)
class Alarm:
    """One condition that is currently firing."""

    key: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _silence_minutes(session: Session) -> int:
    row = session.get(Setting, "silence_alarm_minutes")
    return int(row.value) if row is not None else DEFAULT_SILENCE_MINUTES


def _adapter_for(session: Session, mode: str, clock: Clock) -> ExecutionAdapter | None:
    """The venue adapter for ``mode``, or None where none exists yet."""
    if mode == "paper":
        return PaperAdapter(session, BITSTAMP, clock, mode)
    return None  # live adapter arrives with spec 5.2


def _conditions(session: Session, clock: Clock) -> list[Alarm]:
    """Every alarm condition that holds right now."""
    now = clock.now()
    alarms: list[Alarm] = []

    # 1. Silent services.
    silence_minutes = _silence_minutes(session)
    cutoff = now - timedelta(minutes=silence_minutes)
    services = set(EXPECTED_SERVICES) | set(
        session.scalars(select(Heartbeat.service).distinct()).all()
    )
    for service in sorted(services):
        latest = session.scalar(
            select(func.max(Heartbeat.time)).where(Heartbeat.service == service)
        )
        if latest is None or latest < cutoff:
            age = "never" if latest is None else f"{now - latest} ago"
            alarms.append(
                Alarm(
                    key=f"service_silent:{service}",
                    message=(
                        f"No heartbeat from {service} in over "
                        f"{silence_minutes} minutes (last: {age})."
                    ),
                    details={"service": service},
                )
            )

    # 2. Today's daily decision rows missing after 00:15 UTC.
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now >= today + DAILY_CUTOFF:
        n_daily = session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(Decision.cycle == "daily", Decision.ran_at >= today)
        )
        if not n_daily:
            alarms.append(
                Alarm(
                    key="daily_decision_missing",
                    message=(
                        f"No daily decision rows for {today.date()} after 00:15 UTC."
                    ),
                )
            )

    # 3. No intraday decision for 15 minutes.
    last_intraday = session.scalar(
        select(func.max(Decision.ran_at)).where(Decision.cycle == "intraday")
    )
    if last_intraday is None or last_intraday < now - INTRADAY_STALE:
        alarms.append(
            Alarm(
                key="intraday_stale",
                message="No intraday decision in the last 15 minutes.",
            )
        )

    # 4. Newest 5m candle stale (age of the bar's close, as in spec 1.5).
    for symbol in SYMBOLS:
        open_time = session.scalar(
            select(func.max(Candle.open_time)).where(
                Candle.symbol == symbol, Candle.interval == "5m"
            )
        )
        if open_time is None or open_time + CANDLE_PERIOD < now - CANDLE_STALE:
            alarms.append(
                Alarm(
                    key=f"candle_stale:{symbol}",
                    message=(f"Newest {symbol} 5m candle is over 10 minutes old."),
                    details={"symbol": symbol},
                )
            )

    # 5. Ledger balances differ from the adapter's balances.
    adapter = _adapter_for(session, get_settings().mode, clock)
    if adapter is not None:
        ledger = accounting.positions(session, get_settings().mode)
        ledger["USD"] = accounting.cash(session, get_settings().mode)
        venue = adapter.balances()
        diffs = {
            asset: (ledger.get(asset, Decimal(0)), venue.get(asset, Decimal(0)))
            for asset in ledger.keys() | venue.keys()
            if ledger.get(asset, Decimal(0)) != venue.get(asset, Decimal(0))
        }
        if diffs:
            alarms.append(
                Alarm(
                    key="balances_mismatch",
                    message=(
                        "Ledger balances differ from the adapter: "
                        + ", ".join(
                            f"{a} ledger {pair[0]} vs venue {pair[1]}"
                            for a, pair in sorted(diffs.items())
                        )
                    ),
                    details={
                        "diffs": {
                            a: [str(pair[0]), str(pair[1])] for a, pair in diffs.items()
                        }
                    },
                )
            )

    return alarms


def _active_alarms(session: Session) -> dict[str, Event]:
    """Currently open alarms: an ``alarm`` event with no later
    ``alarm_cleared`` for the same ``details["alarm"]`` key."""
    active: dict[str, Event] = {}
    for e in session.scalars(
        select(Event).where(Event.kind.in_(_ALARM_KINDS)).order_by(Event.id)
    ).all():
        key = e.details.get("alarm")
        if not isinstance(key, str):
            continue
        if e.kind == "alarm":
            active[key] = e
        else:
            active.pop(key, None)
    return active


def _raise(session: Session, now: datetime, alarm: Alarm) -> Event:
    row = Event(
        time=now,
        kind="alarm",
        severity="alarm",
        message=alarm.message,
        details={"alarm": alarm.key, **alarm.details},
    )
    session.add(row)
    return row


def _clear(session: Session, now: datetime, key: str, opened: Event) -> Event:
    row = Event(
        time=now,
        kind="alarm_cleared",
        severity="info",
        message=f"Cleared: {opened.message}",
        details={"alarm": key, "alarm_event_id": opened.id},
    )
    session.add(row)
    return row


def _write_summary(session: Session, now: datetime) -> Event | None:
    """The 3-hourly ``heartbeat_summary`` event, or None when not due."""
    last = session.scalar(
        select(func.max(Event.time)).where(Event.kind == "heartbeat_summary")
    )
    if last is not None and now - last < SUMMARY_EVERY:
        return None
    window = now - SUMMARY_EVERY
    checks = session.scalar(
        select(func.count())
        .select_from(Decision)
        .where(Decision.cycle == "intraday", Decision.ran_at >= window)
    )
    # A failed cycle records an error decision, not an event — count both.
    errors = (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.severity == "error", Event.time >= window)
        )
        or 0
    ) + (
        session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(Decision.action == "error", Decision.ran_at >= window)
        )
        or 0
    )
    raised = session.scalars(
        select(Event).where(Event.kind == "alarm", Event.time >= window)
    ).all()
    cleared = session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.kind == "alarm_cleared", Event.time >= window)
    )
    tail = f"{checks or 0} price checks, {errors or 0} errors in the last 3 hours."
    if not raised and not _active_alarms(session):
        message = f"All services up. {tail} Nothing to do."
    else:
        parts = []
        if raised:
            parts.append(
                f"raised {len(raised)} alarm(s) "
                f"({', '.join(e.details.get('alarm', '?') for e in raised)})"
            )
        if cleared:
            parts.append(f"cleared {cleared}")
        open_keys = sorted(_active_alarms(session))
        still = f" Still open: {', '.join(open_keys)}." if open_keys else ""
        message = (
            f"Watchdog {', '.join(parts) or 'saw no alarm changes'} in the "
            f"last 3 hours. {tail}{still}"
        )
    row = Event(
        time=now,
        kind="heartbeat_summary",
        severity="info",
        message=message,
        details={
            "price_checks": checks or 0,
            "errors": errors or 0,
            "alarms_raised": len(raised),
            "alarms_cleared": cleared or 0,
        },
    )
    session.add(row)
    return row


def check(session: Session, clock: Clock) -> list[Alarm]:
    """One watchdog pass. Writes alarm transitions and returns the alarms
    still firing after it."""
    now = clock.now()
    firing = _conditions(session, clock)
    active = _active_alarms(session)
    for alarm in firing:
        if alarm.key not in active:
            _raise(session, now, alarm)
            log.warning("alarm raised: %s", alarm.message)
    for key, opened in active.items():
        if all(a.key != key for a in firing):
            _clear(session, now, key, opened)
            log.info("alarm cleared: %s", key)
    _write_summary(session, now)
    session.flush()
    return firing
