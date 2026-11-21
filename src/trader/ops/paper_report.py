"""Paper trading start and weekly report (spec 5.1).

``ensure_seeded`` credits the paper account its $5,000 starting balance
once; the watchdog calls it every pass so a fresh stack funds itself and a
rebuilt database refills it. ``due_week`` is the scheduling half: after
``digest_time_utc`` on a Monday it returns the Monday-to-Monday window
just ended, unless a ``weekly_report`` event already covers it. ``build``
writes that event with a Markdown body and a ``stats`` payload: paper
equity against ``backtest.engine.run`` over the same days on stored
candles (the tracking gap in percentage points), per-service uptime from
heartbeats, alarms, and approval response times.

The report lives in ``events`` (``kind="weekly_report"``, Markdown and
numbers under ``details``) because the spec lists no migration and
``digests.day`` is already taken by the daily digest.

CLI: ``python -m trader.ops.paper_report seed | report [start] [end]``.
"""

import argparse
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from trader.backtest import engine as bt_engine
from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS
from trader.ledger import accounting
from trader.ledger.models import (
    EquitySnapshot,
    Event,
    Heartbeat,
    Plan,
)
from trader.ops import digest
from trader.ops.watchdog import EXPECTED_SERVICES

log = logging.getLogger(__name__)

SEED_AMOUNT = Decimal("5000")
REPORT_KIND = "weekly_report"
# Advisory-lock key serializing the seed check + deposit, so the watchdog
# and a manual `seed` cannot both fund the account.
_SEED_LOCK = 510_000_001


def _paper_deposit_exists(session: Session) -> bool:
    rows = session.scalars(select(Event.details).where(Event.kind == "deposit")).all()
    return any(r.get("mode") == "paper" for r in rows)


def ensure_seeded(session: Session, clock: Clock) -> bool:
    """Deposit the $5,000 paper starting balance once.

    Returns True when the deposit was written. No-op in live mode or when
    any paper deposit already exists (manual funding counts).
    """
    if get_settings().mode != "paper":
        return False
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SEED_LOCK})
    if _paper_deposit_exists(session):
        return False
    accounting.deposit(session, "paper", SEED_AMOUNT, clock)
    log.info("paper account seeded with %s USD", SEED_AMOUNT)
    return True


def _week_key(start: datetime) -> str:
    return start.date().isoformat()


def _week_end_key(end: datetime) -> str:
    return (end - timedelta(seconds=1)).date().isoformat()


def _report_exists(session: Session, week_start: datetime, week_end: datetime) -> bool:
    # A report only settles a week when it covers the whole window; a
    # shorter on-demand report does not block the scheduled one.
    return (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.kind == REPORT_KIND,
                Event.details["week_start"].as_string() == _week_key(week_start),
                Event.details["week_end"].as_string() == _week_end_key(week_end),
            )
        )
        or 0
    ) > 0


def due_week(session: Session, clock: Clock) -> tuple[datetime, datetime] | None:
    """The [start, end) window whose report should exist now.

    Due once ``digest_time_utc`` has passed on the Monday after the week
    closed; a ``weekly_report`` event carrying that week's start and end
    dates marks it done.
    """
    now = clock.now()
    hour, minute = digest._digest_time(session)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now < today + timedelta(hours=hour, minutes=minute):
        return None
    end = today - timedelta(days=today.weekday())  # Monday 00:00
    start = end - timedelta(days=7)
    if _report_exists(session, start, end):
        return None
    return start, end


def _window_equity(
    session: Session, mode: str, start: datetime, end: datetime
) -> tuple[Decimal | None, Decimal | None]:
    """Paper equity at the edges of the window: last snapshot at or
    before each edge, the window's first snapshot as the opening
    fallback."""
    snaps = (
        session.execute(
            select(EquitySnapshot.equity_usd)
            .where(
                EquitySnapshot.mode == mode,
                EquitySnapshot.time >= start,
                EquitySnapshot.time < end,
            )
            .order_by(EquitySnapshot.time, EquitySnapshot.id)
        )
        .scalars()
        .all()
    )
    end_prices = digest._end_prices(session, end)
    eq_start = digest._equity_at(session, mode, start, end_prices)
    # The replay fallback reports an unfunded account as 0. When the
    # account was funded during the window (e.g. the seed week), the
    # first positive snapshot is the meaningful opening figure.
    if eq_start is None or eq_start <= 0:
        eq_start = next((v for v in snaps if v > 0), eq_start)
    eq_end = digest._equity_at(session, mode, end, end_prices)
    if eq_end is None and snaps:
        eq_end = snaps[-1]
    return eq_start, eq_end


def _candle_frames(
    session: Session, end: datetime
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Daily opens and closes from stored candles up to ``end``, one
    column per symbol; None when coverage is missing."""
    frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        df = candles_mod.load(
            session, symbol, "1d", datetime(1970, 1, 1, tzinfo=UTC), end
        )
        if df.empty:
            return None
        if df["source"].nunique() > 1:
            df = df[df["source"] == df["source"].value_counts().idxmax()]
        df = df[~df.index.duplicated(keep="first")].sort_index()
        frames[symbol] = df
    opens = pd.DataFrame({s: f["open"] for s, f in frames.items()}).dropna()
    closes = pd.DataFrame({s: f["close"] for s, f in frames.items()}).dropna()
    if opens.empty or closes.empty:
        return None
    return opens, closes


def tracking_gap(
    session: Session, mode: str, start: datetime, end: datetime
) -> dict[str, float | None]:
    """Paper return minus backtest return over the window, in points.

    The backtest runs ``backtest.engine.run`` on stored daily candles with
    the Bitstamp cost preset over ``start.date()`` to the last day before
    ``end``. Values are None when snapshots or candles cannot cover the
    window.
    """
    out: dict[str, float | None] = {
        "paper_return": None,
        "backtest_return": None,
        "gap_points": None,
    }
    eq_start, eq_end = _window_equity(session, mode, start, end)
    if eq_start is not None and eq_end is not None and eq_start > 0:
        out["paper_return"] = float(eq_end / eq_start - 1)
    frames = _candle_frames(session, end)
    if frames is not None:
        try:
            result = bt_engine.run(
                *frames,
                BITSTAMP,
                start=start.date().isoformat(),
                end=(end - timedelta(seconds=1)).date().isoformat(),
            )
            equity = result.equity.dropna()
            if len(equity) >= 1:
                # `run` starts with cash 1.0 at the first open, so
                # equity itself is the cumulative return; measuring
                # from equity[0] would drop opening-day performance.
                out["backtest_return"] = float(equity.iloc[-1] - 1)
        except (KeyError, ValueError, ZeroDivisionError):
            pass
    if out["paper_return"] is not None and out["backtest_return"] is not None:
        out["gap_points"] = (out["paper_return"] - out["backtest_return"]) * 100
    return out


def _uptime(session: Session, start: datetime, end: datetime) -> dict[str, float]:
    """Share of the window's minutes that held at least one heartbeat,
    per service that is expected or seen."""
    services = set(EXPECTED_SERVICES) | set(
        session.scalars(select(Heartbeat.service).distinct()).all()
    )
    minutes = max(1.0, (end - start).total_seconds() / 60)
    up: dict[str, float] = {}
    for service in sorted(services):
        covered = session.scalar(
            select(
                func.count(func.distinct(func.date_trunc("minute", Heartbeat.time)))
            ).where(
                Heartbeat.service == service,
                Heartbeat.time >= start,
                Heartbeat.time < end,
            )
        )
        up[service] = min(1.0, (covered or 0) / minutes)
    return up


def _approval_times(
    session: Session, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Minutes from proposal to decision for plans decided in the
    window; pending plans that expired unanswered count too."""
    plans = session.scalars(
        select(Plan)
        .where(Plan.decided_at >= start, Plan.decided_at < end)
        .order_by(Plan.decided_at, Plan.id)
    ).all()
    rows: list[dict[str, Any]] = []
    for p in plans:
        if p.decided_at is None:
            continue
        rows.append(
            {
                "plan_id": p.id,
                "status": p.status,
                "minutes": round((p.decided_at - p.created_at).total_seconds() / 60, 1),
            }
        )
    return rows


def build(session: Session, start: datetime, end: datetime) -> Event:
    """Write the ``weekly_report`` event covering [start, end)."""
    mode = get_settings().mode
    gap = tracking_gap(session, mode, start, end)
    up = _uptime(session, start, end)
    eq_start, eq_end = _window_equity(session, mode, start, end)

    alarms = session.scalars(
        select(Event)
        .where(
            Event.kind.in_(("alarm", "alarm_cleared")),
            Event.time >= start,
            Event.time < end,
        )
        .order_by(Event.time, Event.id)
    ).all()
    raised = [e for e in alarms if e.kind == "alarm"]

    approvals = _approval_times(session, start, end)
    answered = [a["minutes"] for a in approvals]

    week_end = _week_end_key(end)
    lines: list[str] = [
        f"# Weekly paper report — {_week_key(start)} to {week_end}",
        "",
    ]
    acct = f"**Account:** {digest._money(eq_end)}"
    if eq_start is not None:
        acct += f" (opened the week at {digest._money(eq_start)})"
    lines.append(acct + ".")
    if gap["paper_return"] is not None:
        lines.append(f"**Paper return:** {gap['paper_return']:+.2%}.")
    if gap["backtest_return"] is not None:
        lines.append(
            f"**Backtest over the same days (Bitstamp costs):** "
            f"{gap['backtest_return']:+.2%}."
        )
    if gap["gap_points"] is not None:
        lines.append(f"**Tracking gap:** {gap['gap_points']:+.2f} percentage points.")
    else:
        lines.append(
            "**Tracking gap:** not computable this week "
            "(missing snapshots or daily candles)."
        )
    lines.append("")

    lines.append("## Uptime")
    if up:
        lines += [f"- {s}: {v:.1%}" for s, v in up.items()]
    else:
        lines.append("- No heartbeat data.")
    lines.append("")

    lines.append("## Alarms")
    lines += [
        f"- {e.time:%Y-%m-%d %H:%M} {e.kind.replace('_', ' ')}: {e.message}"
        for e in alarms
    ] or ["- No alarms."]
    lines.append("")

    lines.append("## Approvals")
    if approvals:
        for a in approvals:
            lines.append(
                f"- Plan #{a['plan_id']} {a['status']}: answered in "
                f"{a['minutes']:.0f} minutes."
            )
        lines.append(
            f"Median response {median(answered):.0f} minutes, "
            f"slowest {max(answered):.0f} minutes."
        )
    else:
        lines.append("No plans were decided this week.")
    body = "\n".join(lines) + "\n"

    mean_uptime = sum(up.values()) / len(up) if up else None
    stats: dict[str, Any] = {
        "week_start": _week_key(start),
        "week_end": week_end,
        "equity_start_usd": str(eq_start) if eq_start is not None else None,
        "equity_end_usd": str(eq_end) if eq_end is not None else None,
        "paper_return": gap["paper_return"],
        "backtest_return": gap["backtest_return"],
        "tracking_gap_points": gap["gap_points"],
        "uptime": {s: round(v, 4) for s, v in up.items()},
        "mean_uptime": mean_uptime,
        "alarms_raised": len(raised),
        "alarms_cleared": sum(1 for e in alarms if e.kind == "alarm_cleared"),
        "approvals": approvals,
        "approval_median_minutes": median(answered) if answered else None,
        "approval_max_minutes": max(answered) if answered else None,
    }
    gap_txt = (
        f"{gap['gap_points']:+.2f} points"
        if gap["gap_points"] is not None
        else "not computable"
    )
    message = (
        f"Weekly paper report {_week_key(start)} to {week_end}: "
        f"account {digest._money(eq_end)}, tracking gap {gap_txt}, "
        f"{len(raised)} alarm(s) raised."
    )
    row = Event(
        time=end,
        kind=REPORT_KIND,
        severity="info",
        message=message,
        details={"week_start": _week_key(start), **stats, "body_md": body},
    )
    session.add(row)
    session.flush()
    log.info("weekly paper report written: %s", message)
    return row


def _parse_day(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="trader.ops.paper_report")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="deposit the $5,000 paper starting balance once")
    rep = sub.add_parser("report", help="write a weekly report event now")
    rep.add_argument("start", help="first day covered, ISO date")
    rep.add_argument("end", help="day after the last covered, ISO date")
    args = parser.parse_args(argv)

    with session_scope() as session:
        if args.cmd == "seed":
            if ensure_seeded(session, SystemClock()):
                print("Deposited $5,000 into the paper account.")
            else:
                print("Paper account already funded; nothing done.")
        else:
            row = build(session, _parse_day(args.start), _parse_day(args.end))
            print(row.details["body_md"])


if __name__ == "__main__":
    main()
