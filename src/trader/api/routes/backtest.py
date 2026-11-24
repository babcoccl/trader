"""Backtest routes: POST /api/backtests/run.

Returns a deterministic in-memory mock ``BacktestResult`` scaled to the
requested starting capital until the real backtest engine is wired in.
"""

from datetime import date

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
    try:
        start = date.fromisoformat(params.start_date)
        end = date.fromisoformat(params.end_date)
    except ValueError:
        start = end = date(1970, 1, 1)
    span = max((end - start).days, 0)

    points: list[BacktestPoint] = []
    for i in range(POINTS):
        frac = i / (POINTS - 1)
        # Fixed path: 8% total growth with a mid-window dip, benchmark 5%.
        dip = 0.10 * frac * (1 - frac) * 4
        equity = capital * (1 + 0.08 * frac - dip)
        benchmark = capital * (1 + 0.05 * frac)
        day = start.fromordinal(start.toordinal() + round(span * frac))
        points.append(
            BacktestPoint(
                timestamp=day.isoformat(),
                equity=equity,
                benchmark_equity=benchmark,
                drawdown=dip,
                asset_price=50000.0 * (1 + 0.06 * frac),
            )
        )

    trades = [
        BacktestTrade(
            date=start.isoformat(),
            asset=params.asset_universe[0] if params.asset_universe else "BTC-USD",
            side="BUY",
            size=capital * 0.05 / 50000.0,
            price=50000.0,
            pnl=0.0,
            fee=capital * 0.05 * 0.001,
        ),
        BacktestTrade(
            date=end.isoformat(),
            asset=params.asset_universe[0] if params.asset_universe else "BTC-USD",
            side="SELL",
            size=capital * 0.05 / 50000.0,
            price=53000.0,
            pnl=capital * 0.05 * 0.06,
            fee=capital * 0.05 * 0.001,
        ),
    ]

    return BacktestResult(
        summary_metrics=BacktestSummaryMetrics(
            cagr=0.08,
            benchmark_cagr=0.05,
            max_drawdown=max(p.drawdown for p in points),
            total_trades=len(trades),
            win_rate=0.5,
            total_fees_paid=sum(t.fee for t in trades),
        ),
        timeseries_data=points,
        trade_log=trades,
    )


@router.post("/run", response_model=BacktestResult)
def run_backtest(params: BacktestParams) -> BacktestResult:
    return _mock_result(params)
