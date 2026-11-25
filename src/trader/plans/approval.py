"""Approval state machine (spec 2.5): every plan passes through the owner
or dies.

``pending`` is the only state an owner decision can move: ``approve`` and
``deny`` refuse anything else, and ``sweep`` (run every minute) moves
``pending`` plans to ``expired`` past ``expires_at`` or to ``voided`` when
the price moved more than ``limits.price_move_void`` from the plan's
``reference_price``.

Approving with a ``size_fraction`` scales every order quantity,
``est_fees`` and ``worst_case_loss`` before re-running ``risk.check_plan``;
a plan that fails the check stays ``pending`` and is refused.

Plans that die without approval (``denied``, ``expired``, ``voided``) get
shadow copies of their entry orders at full size (``orders.shadow=true``):
the paper adapter fills them like real ones, the ledger ignores them, and
reports can show what the trade would have done. Shadow order rows are
written ``open`` so the executor's normal fill polling picks them up
without any executor change.

Every transition writes an ``audit_log`` row.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock, SystemClock
from trader.core.config import get_settings
from trader.data.sources.base import SYMBOLS
from trader.execution.paper import PRIMARY_SOURCE
from trader.ledger import accounting
from trader.ledger.models import AuditLog, Candle, Order, Plan
from trader.risk import engine as risk
from trader.risk.limits import Limits


@dataclass
class _ScaledPlan:
    """PlanView for ``risk.check_plan`` on the would-be scaled plan, so a
    refused approval never mutates the real row."""

    symbol: str
    side: str
    target_weight: Decimal
    worst_case_loss: Decimal


def _locked_plan(session: Session, plan_id: int) -> Plan | None:
    """Fetch the plan under a row lock so a concurrent approve/deny/sweep
    either waits for this decision or loses to it."""
    return session.scalars(
        select(Plan).where(Plan.id == plan_id).with_for_update()
    ).first()


ALLOWED_FRACTIONS = (Decimal("1.0"), Decimal("0.5"), Decimal("0.25"))


class ApprovalError(ValueError):
    """A requested transition is not allowed."""


def _audit(
    session: Session, actor: str, action: str, plan: Plan, before: Mapping[str, object]
) -> None:
    session.add(
        AuditLog(
            time=func.now(),
            actor=actor,
            action=action,
            before=before,
            after={
                "plan_id": plan.id,
                "status": plan.status,
                "size_fraction": str(plan.size_fraction),
                "decided_at": str(plan.decided_at) if plan.decided_at else None,
            },
        )
    )


def _latest_prices(session: Session) -> dict[str, Decimal]:
    """Newest stored 5m close per symbol (primary source first)."""
    prices: dict[str, Decimal] = {}
    for symbol in SYMBOLS:
        close = session.scalar(
            select(Candle.close)
            .where(Candle.symbol == symbol, Candle.interval == "5m")
            .order_by(Candle.open_time.desc(), Candle.source != PRIMARY_SOURCE)
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


def _shadow_orders(session: Session, plan: Plan, clock: Clock) -> None:
    """Full-size shadow copies of the plan's entry orders."""
    used = (
        session.scalar(
            select(func.count()).select_from(Order).where(Order.plan_id == plan.id)
        )
        or 0
    )
    for i, spec in enumerate(plan.entry_orders, start=1):
        client_order_id = f"{plan.id}-shadow-{used + i}"
        session.add(
            Order(
                plan_id=plan.id,
                venue="paper",
                client_order_id=client_order_id,
                venue_order_id=f"paper-{client_order_id}",
                symbol=plan.symbol,
                side=str(spec.get("side", plan.side)),
                type=str(spec["type"]),
                price=(
                    Decimal(str(spec["price"]))
                    if spec.get("price") is not None
                    else None
                ),
                stop_price=(
                    Decimal(str(spec["stop_price"]))
                    if spec.get("stop_price") is not None
                    else None
                ),
                quantity=Decimal(str(spec["quantity"])),
                status="open",
                mode=get_settings().mode,
                shadow=True,
                created_at=clock.now(),
            )
        )


def _die(session: Session, plan: Plan, status: str, actor: str, clock: Clock) -> Plan:
    """Move ``plan`` to a terminal no-approval state, with shadow orders."""
    before = {"plan_id": plan.id, "status": plan.status}
    plan.status = status  # type: ignore[assignment]
    plan.decided_at = clock.now()
    _shadow_orders(session, plan, clock)
    _audit(session, actor, f"{status} plan", plan, before)
    return plan


def approve(
    session: Session,
    plan_id: int,
    size_fraction: float | Decimal,
    actor: str = "owner",
    clock: Clock | None = None,
    limits: Limits | None = None,
) -> Plan:
    """Approve a pending plan, optionally at half or quarter size.

    Refuses (``ApprovalError``) when the plan is not pending, is past its
    expiry, the fraction is not one of ``{1.0, 0.5, 0.25}``, or the scaled
    plan no longer passes ``risk.check_plan``.
    """
    clock = clock or SystemClock()
    limits = limits or Limits.load(session)
    plan = _locked_plan(session, plan_id)
    if plan is None:
        raise ApprovalError(f"plan {plan_id} not found")
    fraction = Decimal(str(size_fraction))
    if fraction not in ALLOWED_FRACTIONS:
        raise ApprovalError(
            f"size_fraction {fraction} is not one of {ALLOWED_FRACTIONS}"
        )
    if plan.status != "pending":
        raise ApprovalError(
            f"plan {plan_id} is {plan.status}; only pending plans can be approved"
        )
    if clock.now() > plan.expires_at:
        raise ApprovalError(f"plan {plan_id} expired at {plan.expires_at}")

    # Risk-check the scaled values before touching the row: a refused
    # approval must leave the pending plan untouched.
    scaled = _ScaledPlan(
        symbol=plan.symbol,
        side=plan.side,
        target_weight=plan.target_weight,
        worst_case_loss=plan.worst_case_loss * fraction,
    )
    mode = get_settings().mode
    prices = _latest_prices(session)
    portfolio = accounting.portfolio_view(session, mode, prices)
    verdict = risk.check_plan(scaled, portfolio, limits, risk.state(session, clock))
    if not verdict.ok:
        raise ApprovalError(
            f"plan {plan_id} failed risk checks: {'; '.join(verdict.reasons)}"
        )

    if fraction != 1:
        plan.entry_orders = [
            {**spec, "quantity": str(Decimal(str(spec["quantity"])) * fraction)}
            for spec in plan.entry_orders
        ]
        plan.worst_case_loss *= fraction
        plan.est_fees *= fraction
    plan.size_fraction = fraction

    before = {"plan_id": plan.id, "status": "pending"}
    plan.status = "approved"
    plan.decided_at = clock.now()
    _audit(session, actor, "approve plan", plan, before)
    session.flush()
    return plan


def maybe_auto_approve(
    session: Session, plan: Plan, clock: Clock, limits: Limits | None = None
) -> bool:
    """Autonomy Level 2: approve ``plan`` immediately when it is small and
    risk-bounded, else leave it ``pending`` for the owner.

    Both bounds must hold: plan notional at or under
    ``auto_approve_max_notional_usd`` and worst-case loss at or under
    ``auto_approve_max_loss_pct`` of current equity. The approval still
    goes through ``approve`` — a ``risk.check_plan`` veto or a halt
    refuses it and the plan stays ``pending``.
    """
    settings = get_settings()
    if not settings.auto_approve_enabled or plan.status != "pending":
        return False
    notional = sum(
        Decimal(str(o["quantity"])) * Decimal(str(o["price"] or plan.reference_price))
        for o in plan.entry_orders
    )
    if notional > settings.auto_approve_max_notional_usd:
        return False
    equity = accounting.equity(session, settings.mode, _latest_prices(session))
    if equity <= 0 or (plan.worst_case_loss / equity) > (
        settings.auto_approve_max_loss_pct
    ):
        return False
    try:
        approve(
            session,
            plan.id,
            size_fraction=Decimal("1"),
            clock=clock,
            limits=limits,
            actor="auto",
        )
    except ApprovalError:
        return False
    return True


def deny(session: Session, plan_id: int, actor: str, clock: Clock) -> Plan:
    """Deny a pending plan. Refuses any other state."""
    plan = _locked_plan(session, plan_id)
    if plan is None:
        raise ApprovalError(f"plan {plan_id} not found")
    if plan.status != "pending":
        raise ApprovalError(
            f"plan {plan_id} is {plan.status}; only pending plans can be denied"
        )
    plan = _die(session, plan, "denied", actor, clock)
    session.flush()
    return plan


def sweep(
    session: Session, prices: Mapping[str, Decimal | float], clock: Clock
) -> list[Plan]:
    """Expire or void every pending plan that has run out of time or whose
    price moved too far from its reference price. Returns the changed
    plans."""
    limits = Limits.load(session)
    now = clock.now()
    changed: list[Plan] = []
    pending = session.scalars(
        select(Plan).where(Plan.status == "pending").order_by(Plan.id).with_for_update()
    ).all()
    for plan in pending:
        if now > plan.expires_at:
            changed.append(_die(session, plan, "expired", "sweep", clock))
            continue
        price = prices.get(plan.symbol)
        if price is None:
            continue
        move = abs(Decimal(str(price)) / plan.reference_price - 1)
        if move > Decimal(str(limits.price_move_void)):
            changed.append(_die(session, plan, "voided", "sweep", clock))
    session.flush()
    return changed
