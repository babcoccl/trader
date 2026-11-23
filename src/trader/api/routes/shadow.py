"""GET /api/shadow: the shadow sleeves' hypothetical books (spec G3).

Read-only: every number comes from ``shadow_marks`` and
``shadow_trades`` rows the shadow runner wrote. Nothing here can place a
real or paper order.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.auth import AuthRequired
from trader.api.routes import SessionDep
from trader.api.schemas import ShadowOut, ShadowSleeveOut, ShadowTradeOut
from trader.core.config import get_settings
from trader.ledger.models import EquitySnapshot, ShadowMark, ShadowTrade
from trader.shadow import runner
from trader.strategy import sleeves

router = APIRouter(dependencies=[AuthRequired])

START_USD = Decimal("10000")


def _trade_out(t: ShadowTrade) -> ShadowTradeOut:
    return ShadowTradeOut(
        symbol=t.symbol,
        opened_at=t.opened_at,
        entry_price=t.entry_price,
        cost_usd=t.cost_usd,
        closed_at=t.closed_at,
        exit_price=t.exit_price,
        pnl_usd=t.pnl_usd,
        exit_reason=t.exit_reason,
    )


def _account_return(session: Session, first_mark: date | None) -> float | None:
    """Newest account equity over the first snapshot at or after the
    sleeve's first mark day, minus 1 (spec G3 step 6)."""
    if first_mark is None:
        return None
    mode = get_settings().mode
    newest = session.scalar(
        select(EquitySnapshot.equity_usd)
        .where(EquitySnapshot.mode == mode)
        .order_by(EquitySnapshot.time.desc())
        .limit(1)
    )
    first = session.scalar(
        select(EquitySnapshot.equity_usd)
        .where(EquitySnapshot.mode == mode, EquitySnapshot.time >= first_mark)
        .order_by(EquitySnapshot.time)
        .limit(1)
    )
    if newest is None or first is None or first == 0:
        return None
    return float(newest / first - 1)


def _sleeve(session: Session, name: str) -> ShadowSleeveOut:
    spec = sleeves.get(name)
    description = getattr(
        next(m for m in (*runner.DAILY, *runner.INTRADAY) if m.NAME == name),
        "DESCRIPTION",
        "",
    )
    marks = list(
        session.scalars(
            select(ShadowMark).where(ShadowMark.sleeve == name).order_by(ShadowMark.day)
        )
    )
    trades = list(
        session.scalars(select(ShadowTrade).where(ShadowTrade.sleeve == name))
    )
    closed = [t for t in trades if t.closed_at is not None]
    closed.sort(key=lambda t: (t.closed_at, t.id), reverse=True)
    open_trades = [t for t in trades if t.closed_at is None]
    fees = sum(
        (t.entry_fee_usd + (t.exit_fee_usd or Decimal("0")) for t in trades),
        Decimal("0"),
    )

    last_mark = marks[-1] if marks else None
    equity = last_mark.equity_usd if last_mark else None
    running_high: Decimal | None = None
    worst: Decimal | None = None
    for m in marks:
        if running_high is None or m.equity_usd > running_high:
            running_high = m.equity_usd
        drop = m.equity_usd / running_high - 1
        if worst is None or drop < worst:
            worst = drop

    return ShadowSleeveOut(
        name=name,
        title=spec.title,
        description=description,
        started_on=marks[0].day if marks else None,
        days=len(marks),
        equity_usd=equity,
        return_fraction=(float(equity / START_USD - 1) if equity is not None else None),
        worst_drawdown=float(worst) if worst is not None else None,
        account_return_fraction=_account_return(
            session, marks[0].day if marks else None
        ),
        closed_trades=len(closed),
        winning_trades=sum(1 for t in closed if (t.pnl_usd or 0) > 0),
        fees_usd=fees,
        last_note=last_mark.note if last_mark else None,
        last_day=last_mark.day if last_mark else None,
        open=[_trade_out(t) for t in open_trades],
        recent_closed=[_trade_out(t) for t in closed[:20]],
    )


@router.get("/api/shadow", response_model=ShadowOut)
def shadow(session: Session = SessionDep) -> ShadowOut:
    names = [m.NAME for m in (*runner.DAILY, *runner.INTRADAY)]
    return ShadowOut(sleeves=[_sleeve(session, name) for name in names])
