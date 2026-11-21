"""GET /api/stream: server-sent events for the dashboard (spec 3.1).

There is no broker in this stack: the stream polls the database every 2
seconds and emits ``decision``, ``fill``, ``plan``, ``alarm`` and
``status`` events when the underlying rows appear or change. A ``status``
event with the full ``GET /api/status`` payload is sent on connect and
then each time a new ``equity_snapshots`` row lands (that is when the
account value actually moves).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from trader.api.auth import SESSION_COOKIE, AuthRequired, token_live
from trader.api.routes.status import build_status
from trader.core.db import session_scope
from trader.ledger.models import Decision, EquitySnapshot, Event, Fill, Order, Plan

router = APIRouter(dependencies=[AuthRequired])

POLL_SECONDS = 2.0
ALARM_KINDS = ("alarm", "alarm_cleared", "emergency_stop")


def _default(o: Any) -> str | None:
    if isinstance(o, Decimal):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()  # type: ignore[no-any-return]
    return str(o)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=_default)}\n\n"


def _new_rows() -> dict[str, Any]:
    """One poll: new decisions/fills/events/snapshots since the last ids,
    and the full plan fingerprint map for change detection."""
    with session_scope() as session:
        return {
            "decision_id": session.scalar(select(func.max(Decision.id))) or 0,
            "fill_id": session.scalar(select(func.max(Fill.id))) or 0,
            "event_id": session.scalar(select(func.max(Event.id))) or 0,
            "snapshot_id": session.scalar(select(func.max(EquitySnapshot.id))) or 0,
            "plans": {
                p.id: (p.status, p.size_fraction)
                for p in session.scalars(select(Plan)).all()
            },
        }


def _fetch_new(last: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Rows written since ``last`` plus the new high-water marks."""
    cur = _new_rows()
    events: list[tuple[str, Any]] = []
    with session_scope() as session:
        for d in session.scalars(
            select(Decision)
            .where(Decision.id > last["decision_id"])
            .order_by(Decision.id)
        ).all():
            events.append(
                (
                    "decision",
                    {
                        "id": d.id,
                        "cycle": d.cycle,
                        "ran_at": d.ran_at,
                        "symbol": d.symbol,
                        "action": d.action,
                        "reason": d.reason,
                    },
                )
            )
        for e in session.scalars(
            select(Event)
            .where(Event.id > last["event_id"], Event.kind.in_(ALARM_KINDS))
            .order_by(Event.id)
        ).all():
            events.append(
                (
                    "alarm",
                    {
                        "id": e.id,
                        "kind": e.kind,
                        "severity": e.severity,
                        "message": e.message,
                        "time": e.time,
                    },
                )
            )
        for fill, order in session.execute(
            select(Fill, Order)
            .join(Order, Fill.order_id == Order.id)
            .where(Fill.id > last["fill_id"], Order.shadow.is_(False))
            .order_by(Fill.id)
        ).all():
            events.append(
                (
                    "fill",
                    {
                        "id": fill.id,
                        "plan_id": order.plan_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "price": fill.price,
                        "quantity": fill.quantity,
                        "fee": fill.fee,
                        "time": fill.time,
                    },
                )
            )
    for plan_id, fp in cur["plans"].items():
        if last["plans"].get(plan_id) != fp:
            events.append(
                ("plan", {"id": plan_id, "status": fp[0], "size_fraction": fp[1]})
            )
    if cur["snapshot_id"] != last["snapshot_id"]:
        with session_scope() as session:
            events.append(("status", build_status(session).model_dump()))
    return cur, events


async def _stream(token: str | None) -> AsyncIterator[str]:
    last = await asyncio.to_thread(_new_rows)
    with session_scope() as session:
        yield _sse("status", build_status(session).model_dump())
    while True:
        await asyncio.sleep(POLL_SECONDS)
        # A logged-out (or expired) token ends the stream instead of
        # streaming trading data forever.
        if not token_live(token):
            return
        last, events = await asyncio.to_thread(_fetch_new, last)
        for event, data in events:
            yield _sse(event, data)


@router.get("/api/stream", include_in_schema=False)
async def stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _stream(request.cookies.get(SESSION_COOKIE)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
