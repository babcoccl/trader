"""Backtest routes: POST /api/backtests/run.

Executes the real daily backtest engine off the event loop via
``trader.backtest.service.execute_backtest``.
"""

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from trader.api.auth import AuthRequired
from trader.api.schemas import BacktestParams, BacktestResult
from trader.backtest.service import execute_backtest

router = APIRouter(
    prefix="/api/backtests", tags=["backtest"], dependencies=[AuthRequired]
)


@router.post("/run", response_model=BacktestResult)
async def run_backtest(params: BacktestParams) -> BacktestResult:
    try:
        return await run_in_threadpool(execute_backtest, params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
