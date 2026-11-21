"""The nine trend rules as pure functions.

Each takes a ``pandas.Series`` of daily closes (oldest first) and returns a
Series of 1.0 / 0.0 values aligned to it, NaN where the rule is undefined.
Constants come from the studies (SPECS.md section A).
"""

import numpy as np
import pandas as pd

SMA_PERIODS = (50, 100, 200)
MOMENTUM_PERIODS = (30, 90, 180)
BREAKOUT_PERIODS = (20, 55, 100)


def sma(close: pd.Series, n: int) -> pd.Series:
    """1.0 where close is above its n-day mean; NaN where the mean is NaN."""
    mean = close.rolling(n, min_periods=n).mean()
    return (close > mean).astype(float).where(mean.notna())


def momentum(close: pd.Series, n: int) -> pd.Series:
    """1.0 where close is above its close n days ago; NaN where that is NaN."""
    prev = close.shift(n)
    return (close > prev).astype(float).where(prev.notna())


def breakout(close: pd.Series, n: int) -> pd.Series:
    """1.0 on a close at the n-day high, 0.0 at the n//2-day low; holds state."""
    hi = close.rolling(n, min_periods=n).max()
    lo = close.rolling(n // 2, min_periods=n // 2).min()
    state = pd.Series(np.nan, index=close.index)
    state[close >= hi] = 1.0
    state[close <= lo] = 0.0
    return state.ffill().where(hi.notna())


def all_rules(close: pd.Series) -> dict[str, pd.Series]:
    """The nine rules, keyed by the names used in expected_signals.csv."""
    rules = {f"sma{n}": sma(close, n) for n in SMA_PERIODS}
    rules.update({f"momentum{n}": momentum(close, n) for n in MOMENTUM_PERIODS})
    rules.update({f"breakout{n}": breakout(close, n) for n in BREAKOUT_PERIODS})
    return rules
