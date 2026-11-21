"""Plan routes (spec 3.1): list, inspect, approve, deny.

Approve and deny go through ``plans.approval`` — the same state machine
everything else uses, with its risk re-check, expiry check, shadow orders
and audit rows. The API never mutates a plan's status itself.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep, order_out
from trader.api.schemas import ApproveRequest, OrderOut, PlanOut
from trader.core.clock import SystemClock
from trader.ledger.models import Order, Plan
from trader.plans import approval

router = APIRouter(dependencies=[AuthRequired])

ACTOR = "owner"


def _out(plan: Plan) -> PlanOut:
    return PlanOut(
        id=plan.id,
        sleeve=plan.sleeve,
        symbol=plan.symbol,
        side=plan.side,
        status=plan.status,
        reference_price=plan.reference_price,
        target_weight=plan.target_weight,
        size_fraction=plan.size_fraction,
        entry_orders=plan.entry_orders,
        stop_price=plan.stop_price,
        worst_case_loss=plan.worst_case_loss,
        est_fees=plan.est_fees,
        rationale=plan.rationale,
        expires_at=plan.expires_at,
        decided_at=plan.decided_at,
        decision_id=plan.decision_id,
        created_at=plan.created_at,
    )


@router.get("/api/plans", response_model=list[PlanOut])
def plans(
    status: str | None = None,
    session: Session = SessionDep,
) -> list[PlanOut]:
    stmt = select(Plan).order_by(Plan.id.desc())
    if status is not None:
        stmt = stmt.where(Plan.status == status)
    return [_out(p) for p in session.scalars(stmt).all()]


@router.get("/api/plans/{plan_id}", response_model=PlanOut)
def plan(plan_id: int, session: Session = SessionDep) -> PlanOut:
    row = session.get(Plan, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return _out(row)


@router.get("/api/plans/{plan_id}/orders")
def plan_orders(
    plan_id: int, session: Session = SessionDep
) -> dict[str, list[OrderOut]]:
    row = session.get(Plan, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    orders = session.scalars(
        select(Order).where(Order.plan_id == plan_id).order_by(Order.id)
    ).all()
    return {
        "orders": [order_out(o) for o in orders if not o.shadow],
        "shadow_orders": [order_out(o) for o in orders if o.shadow],
    }


@router.post("/api/plans/{plan_id}/approve", response_model=PlanOut)
def approve(
    plan_id: int,
    body: ApproveRequest,
    session: Session = SessionDep,
) -> PlanOut:
    try:
        plan = approval.approve(
            session, plan_id, body.size_fraction, ACTOR, SystemClock()
        )
    except approval.ApprovalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _out(plan)


@router.post("/api/plans/{plan_id}/deny", response_model=PlanOut)
def deny(plan_id: int, session: Session = SessionDep) -> PlanOut:
    try:
        plan = approval.deny(session, plan_id, ACTOR, SystemClock())
    except approval.ApprovalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _out(plan)
