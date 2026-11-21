"""GET /api/decisions: the decision log (spec 3.1)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import DecisionOut
from trader.ledger.models import Decision

router = APIRouter(dependencies=[AuthRequired])


def _out(d: Decision) -> DecisionOut:
    return DecisionOut(
        id=d.id,
        cycle=d.cycle,
        ran_at=d.ran_at,
        symbol=d.symbol,
        target_weight=d.target_weight,
        current_weight=d.current_weight,
        action=d.action,
        reason=d.reason,
        details=d.details,
    )


@router.get("/api/decisions", response_model=list[DecisionOut])
def decisions(
    cycle: Literal["daily", "intraday"] | None = None,
    limit: int = 50,
    before: datetime | None = None,
    before_id: int | None = None,
    session: Session = SessionDep,
) -> list[DecisionOut]:
    """Newest decisions first. Paging cursor: pass the oldest row's
    ``ran_at`` as ``before`` plus its ``id`` as ``before_id`` — sibling
    rows share ``ran_at`` (the daily cycle writes both assets at once), so
    a bare timestamp would skip the rest of the group."""
    stmt = select(Decision).order_by(Decision.ran_at.desc(), Decision.id.desc())
    if cycle is not None:
        stmt = stmt.where(Decision.cycle == cycle)
    if before is not None:
        if before_id is not None:
            stmt = stmt.where(
                or_(
                    Decision.ran_at < before,
                    (Decision.ran_at == before) & (Decision.id < before_id),
                )
            )
        else:
            stmt = stmt.where(Decision.ran_at < before)
    rows = session.scalars(stmt.limit(limit)).all()
    return [_out(d) for d in rows]
