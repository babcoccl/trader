"""CLI: ``python -m trader.backtest``.

Two modes:

* Legacy: ``--data <csv> --cost <preset>`` prints full-period and
  2022-onward stats at bands 0, 0.10, 0.20, 0.30.
* Candidates: ``--strategy <preset> [--fee-tier adv2_spot] [--exec-mode
  maker|taker|mixed] [--params '{"k": v}'] [--combine-trend] [--format
  compact|json|full]``, or ``--sweep-all`` for the full leaderboard.
"""

import argparse
import json
import sys

from trader.backtest import engine
from trader.backtest.costs import FEE_TIERS, PRESETS

BANDS = (0.0, 0.10, 0.20, 0.30)


def _legacy(args: argparse.Namespace) -> None:
    opens, closes = engine.load_fixture(args.data)
    cost = PRESETS[args.cost]
    for band in BANDS:
        result = engine.run(opens, closes, cost, band=band)
        full = result.stats()
        recent = result.stats(start="2022-01-01")
        print(
            f"band {band:.2f}  "
            f"full: cagr {full['cagr'] * 100:5.1f}%  "
            f"maxdd {full['maxdd'] * 100:5.1f}%  sharpe {full['sharpe']:.2f}  "
            f"turnover {full['turnover_yr']:.1f}/yr  "
            f"exposure {full['exposure'] * 100:.0f}%  |  "
            f"2022+: cagr {recent['cagr'] * 100:5.1f}%  "
            f"maxdd {recent['maxdd'] * 100:5.1f}%  sharpe {recent['sharpe']:.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="trader.backtest")
    parser.add_argument("--data", help="fixture CSV path (legacy mode)")
    parser.add_argument("--cost", choices=sorted(PRESETS), help="cost preset")
    parser.add_argument("--strategy", help="strategy_sleeve / candidate preset")
    parser.add_argument("--preset", help="alias of --strategy")
    parser.add_argument("--fee-tier", choices=sorted(FEE_TIERS))
    parser.add_argument(
        "--exec-mode", choices=["maker", "taker", "mixed"], default="maker"
    )
    parser.add_argument("--params", help="JSON dict of preset param overrides")
    parser.add_argument("--combine-trend", action="store_true")
    parser.add_argument("--sweep-all", action="store_true")
    parser.add_argument(
        "--format", choices=["compact", "json", "full"], default="compact"
    )
    parser.add_argument("--start", default="2018-04-01")
    parser.add_argument("--end", default="2026-11-19")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--funding-csv", help="funding_daily.csv path")
    parser.add_argument("--intraday-csv", help="intraday 1h CSV path")
    args = parser.parse_args()

    if args.data and args.cost:
        _legacy(args)
        return

    from pathlib import Path

    from trader.api.routes.backtest import _run_sweep
    from trader.api.schemas import BacktestParams, BacktestSweepParams
    from trader.backtest import service
    from trader.backtest.sleeves import CANDIDATE_PRESETS

    if args.funding_csv:
        service.FUNDING_CSV = Path(args.funding_csv)
    if args.intraday_csv:
        service.INTRADAY_CSV = Path(args.intraday_csv)

    sleeve = args.strategy or args.preset

    if args.sweep_all:
        sweep = _run_sweep(
            BacktestSweepParams(
                base=BacktestParams(
                    start_date=args.start,
                    end_date=args.end,
                    starting_capital=args.capital,
                    execution_mode=args.exec_mode,
                    combine_trend=args.combine_trend,
                )
            )
        )
        print(sweep.combined_markdown)
        if args.format == "json":
            print(json.dumps(sweep.rows, default=str, indent=2))
        return

    if sleeve not in CANDIDATE_PRESETS:
        print(
            f"need --strategy one of {sorted(CANDIDATE_PRESETS)} "
            "(or --data/--cost for legacy, --sweep-all for the sweep)",
            file=sys.stderr,
        )
        sys.exit(2)

    result = service.execute_backtest(
        BacktestParams(
            strategy_sleeve=sleeve,
            start_date=args.start,
            end_date=args.end,
            starting_capital=args.capital,
            fee_tier=args.fee_tier,
            execution_mode=args.exec_mode,
            params=json.loads(args.params) if args.params else {},
            combine_trend=args.combine_trend,
        )
    )
    if args.format == "json":
        print(json.dumps(result.compact_summary, default=str, indent=2))
    else:
        print(result.compact_report)
        if args.format == "full":
            print("\nsummary:", result.summary_metrics.model_dump())


if __name__ == "__main__":
    main()
