"""Live cross-sectional momentum target weights (spec W1.2a, Run 5).

Thin wrapper over ``backtest.sleeves.cross_sectional_momentum_weights`` so the
daily cycle sizes with exactly the math the backtest validated: score =
``close / close.shift(lookback) - 1`` over annualized vol, assets with NaN or
non-positive momentum dropped, an optional BTC-over-SMA-50 gate, top-k equal
shares scaled by ``min(1, vol_target / vol)``, gross capped at 1.0.
"""

import pandas as pd

from trader.backtest.sleeves import cross_sectional_momentum_weights


def target_weights(
    closes: pd.DataFrame,
    *,
    vol_target: float = 0.55,
    lookback_days: int = 60,
    top_k: int = 3,
    btc_gate: bool = True,
) -> dict[str, float]:
    """Latest day's weight per column of ``closes``.

    Symbols not selected — or without enough history for the lookback or the
    vol estimate — get 0.0.
    """
    w = cross_sectional_momentum_weights(
        closes,
        vol_target=vol_target,
        lookback_days=lookback_days,
        top_k=top_k,
        btc_gate=btc_gate,
    )
    if w.empty:
        return {s: 0.0 for s in closes.columns}
    latest = w.iloc[-1]
    return {s: float(latest[s]) for s in closes.columns}
