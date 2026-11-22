"""GET /api/positions: holdings with cost, gain, stop and orders (spec 3.1)."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep, latest_prices, order_out
from trader.api.schemas import OpenOrderOut, PositionOut
from trader.core.config import get_settings
from trader.ledger import accounting
from trader.ledger.models import Order, Plan

router = APIRouter(dependencies=[AuthRequired])

RESTING = ("new", "open", "partially_filled")
LIVE_STOP_STATUSES = ("pending", "approved", "completed")


@router.get("/api/orders/open", response_model=list[OpenOrderOut])
def open_orders(session: Session = SessionDep) -> list[OpenOrderOut]:
    """Resting orders of the current mode, oldest first (spec R10).

    Unlike ``/api/positions`` this does not require a holding, so an
    approved-but-unfilled buy is visible between approval and first fill.
    """
    rows = session.scalars(
        select(Order)
        .where(
            Order.mode == get_settings().mode,
            Order.status.in_(RESTING),
            Order.shadow.is_(False),
        )
        .order_by(Order.id)
    ).all()
    return [
        OpenOrderOut(
            **order_out(o).model_dump(),
            symbol=o.symbol,
            plan_id=o.plan_id,
        )
        for o in rows
    ]


@router.get("/api/positions", response_model=list[PositionOut])
def positions(session: Session = SessionDep) -> list[PositionOut]:
    mode = get_settings().mode
    prices = latest_prices(session)
    equity = accounting.equity(session, mode, prices) if prices else None

    out: list[PositionOut] = []
    for asset, qty in sorted(accounting.positions(session, mode).items()):
        symbol = f"{asset}-USD"
        price = prices.get(symbol)
        value = qty * price if price is not None else None
        share = float(value / equity) if value is not None and equity else None
        avg_cost = accounting.cost_basis(session, mode, symbol)
        gain = (price - avg_cost) * qty if price is not None else None

        # The governing stop comes from the plan that actually bought: a
        # pending or approved-but-unfilled plan's stop does not protect the
        # holding (the executor's emergency-stop check uses filled buys).
        plan = session.scalars(
            select(Plan)
            .where(
                Plan.symbol == symbol,
                Plan.stop_price.is_not(None),
                Plan.status.in_(LIVE_STOP_STATUSES),
                select(Order.id)
                .where(
                    Order.plan_id == Plan.id,
                    Order.symbol == symbol,
                    Order.status.in_(("filled", "partially_filled")),
                    Order.shadow.is_(False),
                )
                .exists(),
            )
            .order_by(Plan.id.desc())
            .limit(1)
        ).first()
        resting = session.scalars(
            select(Order)
            .where(
                Order.symbol == symbol,
                Order.status.in_(RESTING),
                Order.shadow.is_(False),
            )
            .order_by(Order.id)
        ).all()

        out.append(
            PositionOut(
                asset=asset,
                symbol=symbol,
                quantity=qty,
                price=price,
                value_usd=value,
                share=share,
                avg_cost=avg_cost,
                unrealised_gain_usd=gain,
                stop_price=plan.stop_price if plan else None,
                plan_id=plan.id if plan else None,
                resting_orders=[order_out(o) for o in resting],
            )
        )
    return out
