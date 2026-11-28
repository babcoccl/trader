"""Backtest routes: POST /api/backtests/run, presets and sweep.

Executes the real daily backtest engine off the event loop via
``trader.backtest.service.execute_backtest``.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from trader.api.auth import AuthRequired
from trader.api.schemas import (
    BacktestParams,
    BacktestPresetsOut,
    BacktestResult,
    BacktestSweepParams,
    BacktestSweepResult,
)
from trader.backtest.costs import FEE_TIERS, FUT_TIERS, SPOT_TIERS
from trader.backtest.service import (
    data_availability,
    execute_backtest,
)
from trader.backtest.sleeves import CANDIDATE_PRESETS

router = APIRouter(
    prefix="/api/backtests", tags=["backtest"], dependencies=[AuthRequired]
)


@router.post("/run", response_model=BacktestResult)
async def run_backtest(params: BacktestParams) -> BacktestResult:
    try:
        return await run_in_threadpool(execute_backtest, params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/presets", response_model=BacktestPresetsOut)
def backtest_presets() -> BacktestPresetsOut:
    """Fee tiers, candidate preset schemas and dataset availability."""
    return BacktestPresetsOut(
        fee_tiers={
            name: {
                "maker_bps": t.maker_bps,
                "taker_bps": t.taker_bps,
                "default_slippage_bps": t.default_slippage_bps,
            }
            for name, t in FEE_TIERS.items()
        },
        candidate_presets=CANDIDATE_PRESETS,
        data_availability=data_availability(),
    )


def _run_sweep(params: BacktestSweepParams) -> BacktestSweepResult:
    strategies = params.strategies or list(CANDIDATE_PRESETS)
    rows: list[dict[str, Any]] = []
    for sleeve in strategies:
        if sleeve not in CANDIDATE_PRESETS:
            raise ValueError(f"unknown strategy {sleeve!r}")
        preset = CANDIDATE_PRESETS[sleeve]
        tiers = params.fee_tiers or (
            FUT_TIERS if preset.get("default_fee_tier") else SPOT_TIERS
        )
        for tier in tiers:
            run_params = params.base.model_copy(
                update={
                    "strategy_sleeve": sleeve,
                    "fee_tier": tier,
                    "include_series": False,
                }
            )
            try:
                result = execute_backtest(run_params)
            except ValueError as exc:
                rows.append(
                    {
                        "strategy": sleeve,
                        "fee_tier": tier,
                        "data_status": "error",
                        "message": str(exc),
                        "gates": {},
                    }
                )
                continue
            rows.append(result.compact_summary or {})

    header = (
        "| strategy | tier | OOS CAGR | Sharpe | MaxDD | NonUp PnL | gates |\n"
        "|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for r in rows:
        oos = (r.get("splits") or {}).get("oos_2022_plus") or {}
        non_up = sum(
            ((r.get("oos_regimes") or {}).get(k) or {}).get("net_pnl_pct", 0.0)
            for k in ("mixed", "downtrend")
        )
        gates = ",".join(
            f"{k.split('_')[0]}:{v}" for k, v in (r.get("gates") or {}).items()
        )
        lines.append(
            f"| {r.get('strategy', '?')} | {r.get('fee_tier', '?')} | "
            f"{oos.get('cagr', 0.0):.1%} | {oos.get('sharpe', 0.0):.2f} | "
            f"{oos.get('max_dd', 0.0):.1%} | {non_up:.1%} | {gates} |"
        )
    return BacktestSweepResult(rows=rows, combined_markdown="\n".join(lines) + "\n")


@router.post("/sweep", response_model=BacktestSweepResult)
def run_backtest_sweep(params: BacktestSweepParams) -> BacktestSweepResult:
    """Sync def: FastAPI runs it in the threadpool off the event loop."""
    try:
        return _run_sweep(params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
