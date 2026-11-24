"""Pure weight-computation sleeves for the backtest engine."""

import pandas as pd

from trader.strategy import sizing

BTC = "BTC-USD"


def cross_sectional_momentum_weights(
    closes: pd.DataFrame,
    vol_target: float = 0.70,
    lookback_days: int = 30,
    top_k: int = 3,
    btc_gate: bool = True,
) -> pd.DataFrame:
    """Top-k risk-adjusted momentum weights per day.

    Score = ``close / close.shift(lookback_days) - 1`` divided by annualized
    vol. Assets with NaN score or non-positive momentum are ineligible. When
    ``btc_gate`` is set and BTC is in the frame, every weight is 0 on days
    BTC trades at or below its 50-day SMA. Selected assets share weight
    equally, scaled by ``min(1, vol_target / vol)``, gross capped at 1.
    """
    if lookback_days <= 0:
        raise ValueError(f"lookback_days must be positive, got {lookback_days}")
    symbols = list(closes.columns)
    rets = closes / closes.shift(lookback_days) - 1.0
    vols = pd.DataFrame(
        {s: sizing.annual_vol(closes[s]) for s in symbols}, index=closes.index
    )
    scores = rets / vols
    scores = scores.where((rets > 0) & scores.notna())

    if btc_gate and BTC in closes.columns:
        btc = closes[BTC]
        gate_ok = btc > btc.rolling(50).mean()
    else:
        gate_ok = pd.Series(True, index=closes.index)

    w = pd.DataFrame(0.0, index=closes.index, columns=symbols)
    for day in closes.index:
        if not gate_ok[day]:
            continue
        row = scores.loc[day].dropna()
        selected = row.nlargest(top_k)
        if selected.empty:
            continue
        share = 1.0 / len(selected)
        for s in selected.index:
            vol = vols.loc[day, s]
            scale = 1.0 if vol == 0 else min(1.0, vol_target / vol)
            w.loc[day, s] = share * scale
        gross = w.loc[day].sum()
        if gross > 1.0:
            w.loc[day] /= gross
    return w
