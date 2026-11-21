"""Heartbeat writes shared by every service loop (spec 4.1).

``beat`` stamps the row with the database's own clock: the signature is
fixed by the spec (no ``Clock``), and the database time is the one the
watchdog and the status strip compare against anyway.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from trader.ledger.models import Heartbeat

DETAIL_LIMIT = 2000


def beat(
    session: Session, service: str, status: str = "ok", detail: str = ""
) -> Heartbeat:
    """Append one ``heartbeats`` row for ``service``."""
    row = Heartbeat(
        service=service,
        time=func.now(),
        status=status,
        detail=detail[:DETAIL_LIMIT],
    )
    session.add(row)
    return row
