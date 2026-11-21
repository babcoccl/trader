"""GET /api/activity: newest-first union of events, decisions, fills and
heartbeats (spec 3.1). One feed for the dashboard's log view."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import ActivityItem
from trader.ledger.models import Decision, Event, Fill, Heartbeat, Order

router = APIRouter(dependencies=[AuthRequired])

# Item kind each dedicated source carries; every other kind is an
# events.kind, and the executor also writes its own "fill" events.
SOURCE_KINDS = ("decision", "fill", "heartbeat")


def _fill_text(fill: Fill, order: Order) -> str:
    return (
        f"{order.side.capitalize()} fill: {fill.quantity} {order.symbol} at "
        f"{fill.price} (fee {fill.fee} {fill.fee_currency}, {fill.liquidity})."
    )


def _fetch(
    session: Session,
    kind: str | None,
    q: str | None,
    limit: int,
    before: tuple[datetime, str, int] | None,
) -> list[ActivityItem]:
    """Merge the four sources newest-first. ``kind`` and ``q`` are pushed
    into SQL and the (time, kind, ref_id) cursor is applied per source
    before its limit, so a filtered page can't hide matching older rows."""
    items: list[ActivityItem] = []

    def cutoff(
        time_col: ColumnElement[datetime],
        id_col: ColumnElement[int] | None = None,
        source_kind: str | None = None,
    ) -> ColumnElement[bool] | None:
        """Rows strictly older than the cursor under the (time, kind,
        ref_id) sort order. ``source_kind`` is the constant item kind of
        the source; None means each row's own kind (events)."""
        if before is None:
            return None
        t, bkind, bid = before
        older = time_col < t
        if source_kind is None:
            return or_(
                older,
                and_(
                    time_col == t,
                    or_(
                        Event.kind < bkind,
                        and_(Event.kind == bkind, Event.id < bid),
                    ),
                ),
            )
        if source_kind < bkind:
            return time_col <= t
        if source_kind == bkind:
            return or_(older, and_(time_col == t, id_col < bid))
        return older

    # Events are always queried: their kind column is the item kind, so a
    # ``kind`` filter (e.g. "fill") must still see matching event rows.
    estmt = select(Event).order_by(Event.time.desc(), Event.id.desc())
    c = cutoff(Event.time)
    if c is not None:
        estmt = estmt.where(c)
    if kind is not None:
        estmt = estmt.where(Event.kind == kind)
    if q is not None:
        estmt = estmt.where(Event.message.ilike(f"%{q}%"))
    for e in session.scalars(estmt.limit(limit)).all():
        items.append(
            ActivityItem(
                time=e.time,
                kind=e.kind,
                ref_id=e.id,
                text=e.message,
                details={"severity": e.severity, **e.details},
            )
        )

    if kind in (None, "decision"):
        dstmt = select(Decision).order_by(Decision.ran_at.desc(), Decision.id.desc())
        c = cutoff(Decision.ran_at, Decision.id, "decision")
        if c is not None:
            dstmt = dstmt.where(c)
        if q is not None:
            dstmt = dstmt.where(Decision.reason.ilike(f"%{q}%"))
        for d in session.scalars(dstmt.limit(limit)).all():
            items.append(
                ActivityItem(
                    time=d.ran_at,
                    kind="decision",
                    ref_id=d.id,
                    text=d.reason,
                    details={
                        "cycle": d.cycle,
                        "action": d.action,
                        "symbol": d.symbol,
                    },
                )
            )

    if kind in (None, "fill"):
        fstmt = (
            select(Fill, Order)
            .join(Order, Fill.order_id == Order.id)
            .order_by(Fill.time.desc(), Fill.id.desc())
        )
        c = cutoff(Fill.time, Fill.id, "fill")
        if c is not None:
            fstmt = fstmt.where(c)
        if q is not None:
            # Match against the same text _fill_text renders so SQL rows
            # and the displayed text cannot disagree.
            fstmt = fstmt.where(
                func.concat(
                    Order.side,
                    " fill: ",
                    cast(Fill.quantity, String),
                    " ",
                    Order.symbol,
                    " at ",
                    cast(Fill.price, String),
                    " (fee ",
                    cast(Fill.fee, String),
                    " ",
                    Fill.fee_currency,
                    ", ",
                    Fill.liquidity,
                    ")",
                ).ilike(f"%{q}%")
            )
        for fill, order in session.execute(fstmt.limit(limit)).all():
            items.append(
                ActivityItem(
                    time=fill.time,
                    kind="fill",
                    ref_id=fill.id,
                    text=_fill_text(fill, order),
                    details={
                        "order_id": order.id,
                        "plan_id": order.plan_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "price": str(fill.price),
                        "quantity": str(fill.quantity),
                        "fee": str(fill.fee),
                        "fee_currency": fill.fee_currency,
                        "liquidity": fill.liquidity,
                        "shadow": order.shadow,
                    },
                )
            )

    if kind in (None, "heartbeat"):
        hstmt = select(Heartbeat).order_by(Heartbeat.time.desc(), Heartbeat.id.desc())
        c = cutoff(Heartbeat.time, Heartbeat.id, "heartbeat")
        if c is not None:
            hstmt = hstmt.where(c)
        if q is not None:
            # One concat so searches spanning fields ("executor: ok") work.
            hstmt = hstmt.where(
                func.concat(
                    Heartbeat.service,
                    ": ",
                    Heartbeat.status,
                    " ",
                    Heartbeat.detail,
                ).ilike(f"%{q}%")
            )
        for h in session.scalars(hstmt.limit(limit)).all():
            items.append(
                ActivityItem(
                    time=h.time,
                    kind="heartbeat",
                    ref_id=h.id,
                    text=f"{h.service}: {h.status} {h.detail}".strip(),
                    details={
                        "service": h.service,
                        "status": h.status,
                        "detail": h.detail,
                    },
                )
            )

    if q is not None:
        needle = q.lower()
        items = [i for i in items if needle in i.text.lower()]

    items.sort(key=lambda i: (i.time, i.kind, i.ref_id), reverse=True)
    return items[:limit]


@router.get("/api/activity", response_model=list[ActivityItem])
def activity(
    kind: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: datetime | None = None,
    before_kind: str | None = None,
    before_id: int | None = None,
    session: Session = SessionDep,
) -> list[ActivityItem]:
    """``kind`` filters on the item kind (``decision``, ``fill``,
    ``heartbeat``, or any ``events.kind`` such as ``alarm``); ``q`` is a
    substring match on the item text. Paging cursor: ``before`` plus the
    oldest item's ``kind`` as ``before_kind`` and ``ref_id`` as
    ``before_id`` (rows share timestamps, so a bare time would skip them).
    """
    boundary = (
        (before, before_kind or "", before_id or 0) if before is not None else None
    )
    return _fetch(session, kind, q, limit, boundary)
