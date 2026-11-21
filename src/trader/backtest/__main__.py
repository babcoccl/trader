"""CLI: ``python -m trader.backtest --data <csv> --cost <preset>``.

Prints full-period and 2022-onward stats at bands 0, 0.10, 0.20, 0.30.
"""

import argparse

from trader.backtest import engine
from trader.backtest.costs import PRESETS

BANDS = (0.0, 0.10, 0.20, 0.30)


def main() -> None:
    parser = argparse.ArgumentParser(prog="trader.backtest")
    parser.add_argument("--data", required=True, help="fixture CSV path")
    parser.add_argument(
        "--cost", required=True, choices=sorted(PRESETS), help="cost preset"
    )
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
