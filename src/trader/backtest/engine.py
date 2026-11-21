"""Daily backtest engine.

Reproduces research/bt.py exactly on the committed fixture: weights decided
at the close of day t are applied at the open of day t+1, subject to the
trading band, with ``taker_fee + slippage`` charged on traded notional.
"""

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from trader.backtest.costs import CostModel
from trader.strategy import ensemble, sizing

ANN = 365


def load_fixture(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the fixture CSV into (opens, closes) indexed by date.

    Columns are ``<symbol>_open`` and ``<symbol>_close``; returned frames have
    one column per symbol (e.g. ``BTC-USD``).
    """
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    symbols = [c[: -len("_open")] for c in d.columns if c.endswith("_open")]
    opens = d[[f"{s}_open" for s in symbols]]
    closes = d[[f"{s}_close" for s in symbols]]
    opens.columns = symbols
    closes.columns = symbols
    return opens, closes


def _target_weights(closes: pd.DataFrame, vol_target: float) -> pd.DataFrame:
    """Target weight per symbol for every date, from that day's close."""
    signals = {s: ensemble.signal(closes[s]) for s in closes.columns}
    vols = {s: sizing.annual_vol(closes[s]) for s in closes.columns}
    w = pd.DataFrame(index=closes.index, columns=closes.columns, dtype=float)
    for day in closes.index:
        w.loc[day] = sizing.target_weights(
            {s: signals[s][day] for s in closes.columns},
            {s: vols[s][day] for s in closes.columns},
            vol_target,
        )
    return w


@dataclass
class BacktestResult:
    """Daily series produced by ``run`` plus summary statistics."""

    equity: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    exposure: pd.Series = field(repr=False)

    def stats(
        self, start: str | None = None, end: str | None = None
    ) -> dict[str, float]:
        """CAGR, max drawdown, Sharpe, annual turnover, mean exposure.

        ``years`` is row count / 365, so a sliced equity is measured against
        the slice's own length. Sharpe uses pandas' sample std of daily
        returns.
        """
        e = self.equity.loc[start:end]
        t = self.turnover.loc[start:end]
        x = self.exposure.loc[start:end]
        years = len(e) / ANN
        ret = e.pct_change().dropna()
        return {
            "cagr": (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1,
            "maxdd": (e / e.cummax() - 1).min(),
            "sharpe": ret.mean() / ret.std() * math.sqrt(ANN),
            "turnover_yr": t.sum() / years,
            "exposure": x.mean(),
        }


def run(
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    cost: CostModel,
    vol_target: float = 0.40,
    band: float = 0.20,
    start: str = "2018-04-01",
    end: str = "2026-11-19",
) -> BacktestResult:
    """Run the daily backtest over ``closes.loc[start:end]``.

    Weights are computed for every date in ``closes`` first (weights used on
    day t are those of day t-1, 0 if none), so the start date can sit inside
    a longer history.
    """
    w = _target_weights(closes, vol_target)
    idx = closes.loc[start:end].index
    tgt = w.shift(1).reindex(idx).fillna(0.0)

    symbols = list(closes.columns)
    units = {s: 0.0 for s in symbols}
    cash = 1.0
    equity = pd.Series(index=idx, dtype=float)
    turnover = pd.Series(0.0, index=idx)
    exposure = pd.Series(index=idx, dtype=float)
    fee_rate = cost.taker_fee + cost.slippage

    for day in idx:
        o = opens.loc[day].astype(float)
        e = cash + sum(units[s] * o[s] for s in symbols)
        traded = 0.0
        new_units = dict(units)
        for s in symbols:
            cur = units[s] * o[s] / e
            t = float(tgt.loc[day, s])
            if sizing.should_trade(t, cur, band):
                traded += abs(t - cur) * e
                new_units[s] = t * e / o[s]
        if traded > 0:
            units = new_units
            cash = e - traded * fee_rate - sum(units[s] * o[s] for s in symbols)
            turnover[day] = traded / e
        c = closes.loc[day].astype(float)
        value = sum(units[s] * c[s] for s in symbols)
        equity[day] = cash + value
        exposure[day] = value / equity[day] if equity[day] != 0 else np.nan

    return BacktestResult(
        equity=equity, weights=tgt, turnover=turnover, exposure=exposure
    )
