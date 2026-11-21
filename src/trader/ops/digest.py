"""Daily digest (spec 4.2).

``build`` writes one ``digests`` row for a UTC day: account value and
change, invested share, checks run and failed, every daily decision
reason, plan outcomes, fills and fees, and the day's alarms. A day with
no fills still gets a digest; its closing line says why nothing traded.

``due_day`` is the scheduling half: the watchdog calls it each minute
and builds the digest once the clock is past ``digest_time_utc``
(``settings``, default ``"00:30"``) and the row for the day just ended
is missing.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.core.config import get_settings
from trader.data.sources.base import SYMBOLS
from trader.ledger import accounting
from trader.ledger.models import (
    Candle,
    Decision,
    Digest,
    EquitySnapshot,
    Event,
    Fill,
    Order,
    Plan,
    Setting,
)

log = logging.getLogger(__name__)

DEFAULT_DIGEST_TIME = "00:30"


def _digest_time(session: Session) -> tuple[int, int]:
    row = session.get(Setting, "digest_time_utc")
    value = str(row.value) if row is not None else DEFAULT_DIGEST_TIME
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except ValueError:
        log.warning("bad digest_time_utc %r; using %s", value, DEFAULT_DIGEST_TIME)
        hour, minute = (int(part) for part in DEFAULT_DIGEST_TIME.split(":"))
        return hour, minute


def due_day(session: Session, clock: Clock) -> date | None:
    """The UTC day whose digest should exist now, or None when not yet
    due or already written."""
    now = clock.now()
    hour, minute = _digest_time(session)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now < today + timedelta(hours=hour, minutes=minute):
        return None
    day = (today - timedelta(days=1)).date()
    exists = session.scalar(
        select(func.count()).select_from(Digest).where(Digest.day == day)
    )
    return None if exists else day


def _money(x: Decimal | None) -> str:
    return f"${(x or Decimal(0)):,.2f}"


def _end_prices(session: Session, end: datetime) -> dict[str, Decimal]:
    """Last 5m close per symbol at or before ``end``."""
    prices: dict[str, Decimal] = {}
    for symbol in SYMBOLS:
        close = session.scalar(
            select(Candle.close)
            .where(
                Candle.symbol == symbol,
                Candle.interval == "5m",
                Candle.open_time < end,
            )
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


def _equity_at(
    session: Session, mode: str, before: datetime, end_prices: dict[str, Decimal]
) -> Decimal | None:
    """Latest snapshot equity strictly before ``before``; falls back to
    the ledger marked at ``end_prices`` when no snapshot exists yet."""
    snap = session.scalar(
        select(EquitySnapshot.equity_usd)
        .where(EquitySnapshot.mode == mode, EquitySnapshot.time < before)
        .order_by(EquitySnapshot.time.desc(), EquitySnapshot.id.desc())
        .limit(1)
    )
    if snap is not None:
        return snap
    try:
        return accounting.equity(session, mode, end_prices)
    except KeyError:
        return None


def _window(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def build(session: Session, day: date) -> Digest:
    """Write (or rewrite) the digest for UTC ``day`` and return it."""
    start, end = _window(day)
    mode = get_settings().mode
    end_prices = _end_prices(session, end)

    equity_end = _equity_at(session, mode, end, end_prices)
    equity_start = _equity_at(session, mode, start, end_prices)
    if (
        equity_start is None
        or session.scalar(
            select(func.count())
            .select_from(EquitySnapshot)
            .where(EquitySnapshot.mode == mode, EquitySnapshot.time < start)
        )
        == 0
    ):
        # No snapshot before the day: the day's first snapshot is the
        # closest thing to its opening value.
        first = session.scalar(
            select(EquitySnapshot.equity_usd)
            .where(
                EquitySnapshot.mode == mode,
                EquitySnapshot.time >= start,
                EquitySnapshot.time < end,
            )
            .order_by(EquitySnapshot.time, EquitySnapshot.id)
            .limit(1)
        )
        if first is not None:
            equity_start = first
    change = (
        equity_end - equity_start
        if equity_end is not None and equity_start is not None
        else None
    )
    change_pct = (
        change / equity_start * 100 if change is not None and equity_start else None
    )

    invested: Decimal | None = None
    if equity_end:
        invested = Decimal(0)
        for asset, qty in accounting.positions(session, mode).items():
            price = next(
                (p for s, p in end_prices.items() if s.startswith(f"{asset}-")), None
            )
            if price is not None:
                invested += qty * price
        invested = invested / equity_end

    checks_run = (
        session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(
                Decision.cycle == "intraday",
                Decision.ran_at >= start,
                Decision.ran_at < end,
            )
        )
        or 0
    )
    checks_failed = (
        session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(
                Decision.action.in_(("error", "blocked")),
                Decision.ran_at >= start,
                Decision.ran_at < end,
            )
        )
        or 0
    ) + (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.severity == "error", Event.time >= start, Event.time < end)
        )
        or 0
    )

    daily = session.scalars(
        select(Decision)
        .where(
            Decision.cycle == "daily", Decision.ran_at >= start, Decision.ran_at < end
        )
        .order_by(Decision.ran_at, Decision.id)
    ).all()

    proposed = session.scalars(
        select(Plan).where(Plan.created_at >= start, Plan.created_at < end)
    ).all()
    decided = session.scalars(
        select(Plan).where(Plan.decided_at >= start, Plan.decided_at < end)
    ).all()
    plan_counts = {"proposed": len(proposed)}
    for status in ("approved", "denied", "expired", "voided", "completed"):
        plan_counts[status] = sum(1 for p in decided if p.status == status)

    fill_rows = session.execute(
        select(Fill, Order)
        .join(Order, Fill.order_id == Order.id)
        .where(
            Fill.time >= start,
            Fill.time < end,
            Order.mode == mode,
            Order.shadow.is_(False),
        )
        .order_by(Fill.time, Fill.id)
    ).all()
    fees_by_ccy: dict[str, Decimal] = {}
    for fill, _order in fill_rows:
        fees_by_ccy[fill.fee_currency] = (
            fees_by_ccy.get(fill.fee_currency, Decimal(0)) + fill.fee
        )

    alarm_events = session.scalars(
        select(Event)
        .where(
            Event.kind.in_(("alarm", "alarm_cleared")),
            Event.time >= start,
            Event.time < end,
        )
        .order_by(Event.time, Event.id)
    ).all()
    alarms_raised = sum(1 for e in alarm_events if e.kind == "alarm")

    lines: list[str] = [f"# Digest for {day.isoformat()}", ""]
    acct = f"**Account:** {_money(equity_end)}"
    if change is not None:
        sign = "+" if change >= 0 else "-"
        acct += f" ({sign}{_money(abs(change))}"
        if change_pct is not None:
            acct += f", {sign}{abs(change_pct):.2f}%"
        acct += " on the day)"
    if invested is not None:
        acct += f". Invested: {invested:.0%} of the account"
    lines += [
        acct + ".",
        f"**Checks:** {checks_run} price checks run, "
        f"{checks_failed} failed or blocked.",
        "",
    ]

    lines.append("## Daily decisions")
    lines += [f"- {d.reason}" for d in daily] or ["- No daily decision rows this day."]
    lines.append("")

    lines.append("## Plans")
    if proposed or decided:
        lines.append(
            "Proposed: {proposed}. Decided: {decided}.".format(
                proposed=plan_counts["proposed"],
                decided=", ".join(
                    f"{status} {n}"
                    for status, n in plan_counts.items()
                    if status != "proposed" and n
                )
                or "none",
            )
        )
        for plan in proposed:
            lines.append(f"- #{plan.id} {plan.side} {plan.symbol}: {plan.rationale}")
    else:
        lines.append("No plans proposed or decided.")
    lines.append("")

    lines.append("## Fills")
    if fill_rows:
        for fill, order in fill_rows:
            lines.append(
                f"- {order.side.capitalize()} {fill.quantity} {order.symbol} "
                f"at {fill.price:,.2f} (fee {fill.fee.normalize()} "
                f"{fill.fee_currency}, {fill.liquidity})"
            )
        lines.append(
            "Fees total: "
            + ", ".join(
                f"{fee.normalize()} {ccy}" for ccy, fee in sorted(fees_by_ccy.items())
            )
        )
    else:
        lines.append("No fills.")
    lines.append("")

    lines.append("## Alarms")
    lines += [
        f"- {e.time:%H:%M} {e.kind.replace('_', ' ')}: {e.message}"
        for e in alarm_events
    ] or ["- No alarms."]
    lines.append("")

    if not fill_rows:
        if any(d.action == "blocked" for d in daily):
            why = "the daily check was blocked"
        elif proposed:
            why = "the proposed plan(s) were never approved and filled"
        elif not daily:
            why = "no daily decision ran"
        else:
            why = "every daily decision stayed inside the trading band"
        lines.append(f"No trades today because {why}.")
    body = "\n".join(lines) + "\n"

    stats: dict[str, Any] = {
        "equity_usd": str(equity_end) if equity_end is not None else None,
        "equity_change_usd": str(change) if change is not None else None,
        "equity_change_pct": str(change_pct) if change_pct is not None else None,
        "invested_fraction": str(invested) if invested is not None else None,
        "checks_run": checks_run,
        "checks_failed": checks_failed,
        "plans": plan_counts,
        "fills": len(fill_rows),
        "fees": {ccy: str(fee) for ccy, fee in fees_by_ccy.items()},
        "alarms_raised": alarms_raised,
    }

    row = session.scalar(select(Digest).where(Digest.day == day))
    if row is None:
        row = Digest(day=day, body_md=body, stats=stats)
        session.add(row)
    else:
        row.body_md = body
        row.stats = stats
    session.flush()
    return row
