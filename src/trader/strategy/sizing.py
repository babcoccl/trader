"""Volatility-targeted position sizing and the trading band."""

import math

import pandas as pd


def annual_vol(close: pd.Series) -> pd.Series:
    """Annualized vol: EW std (span 30, min 20) of daily returns * sqrt(365)."""
    return close.pct_change().ewm(span=30, min_periods=20).std() * math.sqrt(365)


def target_weights(
    signals: dict[str, float],
    vols: dict[str, float],
    vol_target: float = 0.40,
) -> dict[str, float]:
    """Per-asset weight = signal / n_assets * min(1, vol_target / vol).

    NaN signal or vol gives 0. If the weights sum above 1.0, scale all
    down to sum 1.0. No leverage, no shorts.
    """
    n_assets = len(signals)
    weights = {
        symbol: (
            0.0
            if (pd.isna(signals[symbol]) or pd.isna(vols[symbol]) or vols[symbol] == 0)
            else signals[symbol] / n_assets * min(1.0, vol_target / vols[symbol])
        )
        for symbol in signals
    }
    gross = sum(weights.values())
    if gross > 1.0:
        return {symbol: w / gross for symbol, w in weights.items()}
    return weights


def should_trade(target: float, current: float, band: float = 0.20) -> bool:
    """Trade only if abs(target - current) > band * max(target, current),
    or target is 0 and current is not."""
    return abs(target - current) > band * max(target, current) or (
        target == 0 and current != 0
    )
