"""Backtest routes: POST /api/backtests/run.

Returns a deterministic in-memory mock ``BacktestResult`` scaled to the
requested starting capital until the real backtest engine is wired in.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter

from trader.api.auth import AuthRequired
from trader.api.schemas import (
    BacktestParams,
    BacktestPoint,
    BacktestResult,
    BacktestSummaryMetrics,
    BacktestTrade,
)

router = APIRouter(
    prefix="/api/backtests", tags=["backtest"], dependencies=[AuthRequired]
)

POINTS = 12


def _mock_result(params: BacktestParams) -> BacktestResult:
    capital = params.starting_capital
    start = date.fromisoformat(params.start_date)
    end = date.fromisoformat(params.end_date)
    span = (end - start).days

    points: list[BacktestPoint] = []
    peak = capital
    for i in range(POINTS):
        frac = Decimal(i) / (POINTS - 1)
        # Fixed path: 8% total growth with a mid-window dip, benchmark 5%.
        dip = Decimal("0.10") * frac * (1 - frac) * 4
        equity = capital * (1 + Decimal("0.08") * frac - dip)
        peak = max(peak, equity)
        benchmark = capital * (1 + Decimal("0.05") * frac)
        day = start.fromordinal(start.toordinal() + round(span * frac))
        points.append(
            BacktestPoint(
                timestamp=day.isoformat(),
                equity=equity,
                benchmark_equity=benchmark,
                drawdown=float(1 - equity / peak),
                asset_price=Decimal("50000") * (1 + Decimal("0.06") * frac),
            )
        )

    trades = [
        BacktestTrade(
            date=start.isoformat(),
            asset=params.asset_universe[0] if params.asset_universe else "BTC-USD",
            side="BUY",
            size=capital * Decimal("0.05") / Decimal("50000"),
            price=Decimal("50000"),
            pnl=Decimal("0"),
            fee=capital * Decimal("0.05") * Decimal("0.001"),
        ),
        BacktestTrade(
            date=end.isoformat(),
            asset=params.asset_universe[0] if params.asset_universe else "BTC-USD",
            side="SELL",
            size=capital * Decimal("0.05") / Decimal("50000"),
            price=Decimal("53000"),
            pnl=capital * Decimal("0.05") * Decimal("0.06"),
            fee=capital * Decimal("0.05") * Decimal("0.001"),
        ),
    ]

    return BacktestResult(
        summary_metrics=BacktestSummaryMetrics(
            cagr=1.08 ** (365 / span) - 1,
            benchmark_cagr=1.05 ** (365 / span) - 1,
            max_drawdown=max(p.drawdown for p in points),
            total_trades=len(trades),
            # Win rate counts closed trades (sells), not fills.
            win_rate=sum(1 for t in trades if t.side == "SELL" and t.pnl > 0)
            / max(sum(1 for t in trades if t.side == "SELL"), 1),
            total_fees_paid=sum((t.fee for t in trades), Decimal("0")),
        ),
        timeseries_data=points,
        trade_log=trades,
    )


@router.post("/run", response_model=BacktestResult)
def run_backtest(params: BacktestParams) -> BacktestResult:
    return _mock_result(params)
