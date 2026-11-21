"""Volatility-targeted position sizing and the trading band."""

import math

import pandas as pd


def annual_vol(close: pd.Series) -> pd.Series:
    """Annualized vol: EW std (span 30, min 20) of daily returns * sqrt(365).

    A missing close leaves its return undefined (as research/bt.py does),
    rather than pandas 2.x's default of padding it into a zero return.
    """
    returns = close.pct_change(fill_method=None)
    return returns.ewm(span=30, min_periods=20).std() * math.sqrt(365)


def target_weights(
    signals: dict[str, float],
    vols: dict[str, float],
    vol_target: float = 0.40,
) -> dict[str, float]:
    """Per-asset weight = signal / n_assets * min(1, vol_target / vol).

    NaN signal or vol gives 0. Zero vol gives a scale of 1, the limit of
    min(1, vol_target / vol), as in research/bt.py. If the weights sum
    above 1.0, scale all down to sum 1.0. No leverage, no shorts.
    """
    n_assets = len(signals)
    weights: dict[str, float] = {}
    for symbol, sig in signals.items():
        vol = vols[symbol]
        if pd.isna(sig) or pd.isna(vol):
            weights[symbol] = 0.0
        else:
            scale = 1.0 if vol == 0 else min(1.0, vol_target / vol)
            weights[symbol] = sig / n_assets * scale
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
