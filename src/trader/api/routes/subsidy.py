"""GET /api/subsidy: ping-pong subsidy telemetry (spec W1.2b).

Read-only: fee-tier progress, trailing-30-day and 24-hour booster
spend/trades, and the sleeve's active/throttled/disabled state. Nothing
here can place an order.
"""

from fastapi import APIRouter
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import SubsidyFillOut, SubsidyGateOut, SubsidyOut
from trader.core.clock import SystemClock
from trader.core.config import get_settings
from trader.strategy import subsidy

router = APIRouter(dependencies=[AuthRequired])


@router.get("/api/subsidy", response_model=SubsidyOut)
def subsidy_status(session: Session = SessionDep) -> SubsidyOut:
    snap = subsidy.compute_status(session, SystemClock(), get_settings().mode)
    return SubsidyOut(
        mode=snap.mode,
        status=snap.status,
        reason=snap.reason,
        trailing_30d_volume_usd=snap.trailing_30d_volume_usd,
        today_booster_volume_usd=snap.today_booster_volume_usd,
        booster_trades_30d=snap.booster_trades_30d,
        booster_volume_30d_usd=snap.booster_volume_30d_usd,
        booster_fees_30d_usd=snap.booster_fees_30d_usd,
        tier=snap.tier,
        maker_bps=snap.maker_bps,
        taker_bps=snap.taker_bps,
        target_volume_usd=snap.target_volume_usd,
        remaining_to_target_usd=snap.remaining_to_target_usd,
        progress_pct=snap.progress_pct,
        daily_cap_usd=snap.daily_cap_usd,
        adx=snap.adx,
        adx_max=snap.adx_max,
        open_orders_usd=snap.open_orders_usd,
        booster_net_pnl_30d_usd=snap.booster_net_pnl_30d_usd,
        recent_fills=[
            SubsidyFillOut(
                time=f.time,
                side=f.side,
                price=f.price,
                quantity=f.quantity,
                notional_usd=f.notional_usd,
                fee_usd=f.fee_usd,
                pnl_usd=f.pnl_usd,
            )
            for f in snap.recent_fills
        ],
        expiring_5d_volume_usd=snap.expiring_5d_volume_usd,
        projected_30d_volume_usd=snap.projected_30d_volume_usd,
        gates=[
            SubsidyGateOut(name=g.name, passed=g.passed, detail=g.detail)
            for g in snap.gates
        ],
    )
