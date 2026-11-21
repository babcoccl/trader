"""Heartbeat writes shared by every service loop (spec 4.1).

``beat`` reads time through ``Clock`` (spec section A). The spec's
signature has no clock, so it is an optional keyword: a service passes its
own so heartbeats and the watchdog share one time source under a frozen
clock; without one, ``SystemClock``.
"""

from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.ledger.models import Heartbeat

DETAIL_LIMIT = 2000


def beat(
    session: Session,
    service: str,
    status: str = "ok",
    detail: str = "",
    *,
    clock: Clock | None = None,
) -> Heartbeat:
    """Append one ``heartbeats`` row for ``service``."""
    row = Heartbeat(
        service=service,
        time=(clock or SystemClock()).now(),
        status=status,
        detail=detail[:DETAIL_LIMIT],
    )
    session.add(row)
    return row
