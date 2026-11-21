"""GET /api/activity: newest-first union of events, decisions, fills and
heartbeats (spec 3.1). One feed for the dashboard's log view."""

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import ActivityItem
from trader.ledger.models import Decision, Event, Fill, Heartbeat, Order

router = APIRouter(dependencies=[AuthRequired])

# Item kinds that come from each table; every other kind is an events.kind.
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
    """Merge the four sources newest-first, applying the filters before
    the final limit so a filter can't hide matching older rows."""
    items: list[ActivityItem] = []
    t = before[0] if before is not None else None

    # Each source's own kind labels; a ``kind`` filter skips a source
    # entirely when it cannot produce that kind.
    want_events = kind is None or kind not in SOURCE_KINDS
    want_decisions = kind in (None, "decision")
    want_fills = kind in (None, "fill")
    want_heartbeats = kind in (None, "heartbeat")

    if want_events:
        estmt = select(Event).order_by(Event.time.desc(), Event.id.desc())
        if t is not None:
            estmt = estmt.where(Event.time <= t)
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

    if want_decisions:
        dstmt = select(Decision).order_by(Decision.ran_at.desc(), Decision.id.desc())
        if t is not None:
            dstmt = dstmt.where(Decision.ran_at <= t)
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

    if want_fills:
        fstmt = (
            select(Fill, Order)
            .join(Order, Fill.order_id == Order.id)
            .order_by(Fill.time.desc(), Fill.id.desc())
        )
        if t is not None:
            fstmt = fstmt.where(Fill.time <= t)
        if q is not None:
            # The synthesized text mentions symbol and side; match both in
            # SQL so a text search does not drop older matching fills.
            fstmt = fstmt.where(
                Order.symbol.ilike(f"%{q}%") | Order.side.ilike(f"%{q}%")
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

    if want_heartbeats:
        hstmt = select(Heartbeat).order_by(Heartbeat.time.desc(), Heartbeat.id.desc())
        if t is not None:
            hstmt = hstmt.where(Heartbeat.time <= t)
        if q is not None:
            hstmt = hstmt.where(
                Heartbeat.service.ilike(f"%{q}%")
                | Heartbeat.status.ilike(f"%{q}%")
                | Heartbeat.detail.ilike(f"%{q}%")
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
    if before is not None:
        cutoff = (before[0], before[1], before[2])
        items = [i for i in items if (i.time, i.kind, i.ref_id) < cutoff]
    return items[:limit]


@router.get("/api/activity", response_model=list[ActivityItem])
def activity(
    kind: str | None = None,
    q: str | None = None,
    limit: int = 50,
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
