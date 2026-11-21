"""Control routes (spec 3.1): halt, resume, flatten.

These call the risk engine's own functions — the same audit rows, the same
flag semantics — so the API can never set a risk flag some other way.
Flatten only raises the flag; the executor does the selling (spec 2.4).
"""

from fastapi import APIRouter
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import FlattenRequest, HaltRequest, OkOut
from trader.risk import engine as risk

router = APIRouter(dependencies=[AuthRequired])

ACTOR = "owner"


@router.post("/api/controls/halt", response_model=OkOut)
def halt(body: HaltRequest, session: Session = SessionDep) -> OkOut:
    risk.halt(session, ACTOR, body.reason)
    return OkOut(detail={"halted": True, "halt_reason": body.reason})


@router.post("/api/controls/resume", response_model=OkOut)
def resume(session: Session = SessionDep) -> OkOut:
    risk.resume(session, ACTOR)
    return OkOut(detail={"halted": False})


@router.post("/api/controls/flatten", response_model=OkOut)
def flatten(body: FlattenRequest, session: Session = SessionDep) -> OkOut:
    risk.flatten_requested(session, ACTOR)
    return OkOut(detail={"flatten_requested": True})
