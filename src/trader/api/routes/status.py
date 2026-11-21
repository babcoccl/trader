"""GET /api/status: the one glance at the whole system (spec 3.1)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import latest_prices
from trader.api.schemas import ServiceStatus, StatusOut
from trader.core.config import get_settings
from trader.core.db import session_scope
from trader.ledger import accounting
from trader.ledger.models import (
    Decision,
    EquitySnapshot,
    Fill,
    Heartbeat,
    Order,
    Plan,
)
from trader.risk import engine as risk
from trader.risk.limits import Limits

router = APIRouter(dependencies=[AuthRequired])


def _next_daily_decision(now: datetime) -> datetime:
    """Next 00:05 UTC (the daily cycle's decision time, spec 1.4)."""
    today_0005 = now.replace(hour=0, minute=5, second=0, microsecond=0)
    return today_0005 if now < today_0005 else today_0005 + timedelta(days=1)


def build_status(session: Session) -> StatusOut:
    mode = get_settings().mode
    state = risk.state(session)
    limits = Limits.load(session)

    snapshot = session.scalars(
        select(EquitySnapshot)
        .where(EquitySnapshot.mode == mode)
        .order_by(EquitySnapshot.time.desc(), EquitySnapshot.id.desc())
        .limit(1)
    ).first()
    prices = latest_prices(session)
    if snapshot is not None:
        equity = snapshot.equity_usd
    elif prices:
        equity = accounting.equity(session, mode, prices)
    else:
        equity = None

    invested = 0.0
    if prices:
        invested = sum(
            w for w in accounting.portfolio_view(session, mode, prices).weights.values()
        )

    pending = session.scalar(
        select(func.count()).select_from(Plan).where(Plan.status == "pending")
    )
    last_decision = session.scalars(
        select(Decision).order_by(Decision.ran_at.desc(), Decision.id.desc()).limit(1)
    ).first()
    last_trade = session.scalar(
        select(func.max(Fill.time))
        .join(Order, Fill.order_id == Order.id)
        .where(Order.shadow.is_(False))
    )
    # Newest heartbeat per service.
    latest = (
        select(Heartbeat.service, func.max(Heartbeat.time).label("t"))
        .group_by(Heartbeat.service)
        .subquery()
    )
    services = session.execute(
        select(Heartbeat.service, Heartbeat.status, Heartbeat.time).join(
            latest,
            (Heartbeat.service == latest.c.service) & (Heartbeat.time == latest.c.t),
        )
    ).all()

    return StatusOut(
        mode=mode,
        halted=state.halted,
        halt_reason=state.halt_reason,
        equity_usd=equity,
        invested_fraction=invested,
        drawdown=state.drawdown,
        halt_at=limits.drawdown_halt,
        pending_plans=pending or 0,
        last_check_at=last_decision.ran_at if last_decision else None,
        last_check_reason=last_decision.reason if last_decision else None,
        next_daily_decision_at=_next_daily_decision(datetime.now(UTC)),
        last_trade_at=last_trade,
        services=[
            ServiceStatus(name=name, status=status, last_heartbeat_at=t)
            for name, status, t in services
        ],
    )


@router.get("/api/status", response_model=StatusOut)
def status() -> StatusOut:
    # Its own session: the SSE loop calls this every poll, off the request dep.
    with session_scope() as session:
        return build_status(session)
