"""GET /api/funding: latest recorded funding rate per symbol/venue against
the Treasury-bill setting (spec 3.4 funding-watch line)."""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import FundingOut, FundingRateOut
from trader.ledger.models import FundingRate, Setting

router = APIRouter(dependencies=[AuthRequired])


@router.get("/api/funding", response_model=FundingOut)
def funding(session: Session = SessionDep) -> FundingOut:
    latest = (
        select(FundingRate.symbol, FundingRate.venue, func.max(FundingRate.time).label("t"))
        .group_by(FundingRate.symbol, FundingRate.venue)
        .subquery()
    )
    rows = session.execute(
        select(FundingRate).join(
            latest,
            (FundingRate.symbol == latest.c.symbol)
            & (FundingRate.venue == latest.c.venue)
            & (FundingRate.time == latest.c.t),
        )
    ).scalars()
    rates = [
        FundingRateOut(symbol=r.symbol, venue=r.venue, time=r.time, rate=r.rate)
        for r in rows
    ]
    rates.sort(key=lambda r: (r.symbol, r.venue))

    raw: Any = session.scalar(select(Setting.value).where(Setting.key == "t_bill_rate"))
    t_bill_rate = float(raw) if raw is not None else 0.0
    return FundingOut(rates=rates, t_bill_rate=t_bill_rate)
