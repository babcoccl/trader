"""GET /api/activity: newest-first union of events, decisions, fills and
heartbeats (spec 3.1). One feed for the dashboard's log view."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import ActivityItem
from trader.ledger.models import Decision, Event, Fill, Heartbeat, Order

router = APIRouter(dependencies=[AuthRequired])


def _fill_text(fill: Fill, order: Order) -> str:
    return (
        f"{order.side.capitalize()} fill: {fill.quantity} {order.symbol} at "
        f"{fill.price} (fee {fill.fee} {fill.fee_currency}, {fill.liquidity})."
    )


def _fetch(
    session: Session, limit: int, before: datetime | None, q: str | None
) -> list[ActivityItem]:
    items: list[ActivityItem] = []

    estmt = select(Event).order_by(Event.time.desc(), Event.id.desc()).limit(limit)
    if before is not None:
        estmt = estmt.where(Event.time < before)
    for e in session.scalars(estmt).all():
        items.append(
            ActivityItem(
                time=e.time,
                kind=e.kind,
                ref_id=e.id,
                text=e.message,
                details={"severity": e.severity, **e.details},
            )
        )

    dstmt = (
        select(Decision)
        .order_by(Decision.ran_at.desc(), Decision.id.desc())
        .limit(limit)
    )
    if before is not None:
        dstmt = dstmt.where(Decision.ran_at < before)
    for d in session.scalars(dstmt).all():
        items.append(
            ActivityItem(
                time=d.ran_at,
                kind="decision",
                ref_id=d.id,
                text=d.reason,
                details={"cycle": d.cycle, "action": d.action, "symbol": d.symbol},
            )
        )

    fstmt = (
        select(Fill, Order)
        .join(Order, Fill.order_id == Order.id)
        .order_by(Fill.time.desc(), Fill.id.desc())
        .limit(limit)
    )
    if before is not None:
        fstmt = fstmt.where(Fill.time < before)
    fill_rows = session.execute(fstmt).all()
    for fill, order in fill_rows:
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

    hstmt = (
        select(Heartbeat)
        .order_by(Heartbeat.time.desc(), Heartbeat.id.desc())
        .limit(limit)
    )
    if before is not None:
        hstmt = hstmt.where(Heartbeat.time < before)
    for h in session.scalars(hstmt).all():
        items.append(
            ActivityItem(
                time=h.time,
                kind="heartbeat",
                ref_id=h.id,
                text=f"{h.service}: {h.status} {h.detail}".strip(),
                details={"service": h.service, "status": h.status, "detail": h.detail},
            )
        )

    if q is not None:
        needle = q.lower()
        items = [i for i in items if needle in i.text.lower()]

    items.sort(key=lambda i: (i.time, i.ref_id), reverse=True)
    return items[:limit]


@router.get("/api/activity", response_model=list[ActivityItem])
def activity(
    kind: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: datetime | None = None,
    session: Session = SessionDep,
) -> list[ActivityItem]:
    """``kind`` filters on the item kind (``decision``, ``fill``,
    ``heartbeat``, or any ``events.kind`` such as ``alarm``); ``q`` is a
    substring match on the item text; ``before`` is the time cursor for
    paging."""
    items = _fetch(session, limit, before, q)
    if kind is not None:
        items = [i for i in items if i.kind == kind]
    return items
