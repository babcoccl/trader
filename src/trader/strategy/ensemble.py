"""Ensemble signal: plain average of the nine rules, 0 to 1 per asset."""

import pandas as pd

from trader.strategy import rules


def signal(close: pd.Series) -> pd.Series:
    """Sum of the nine rules (NaN treated as 0) divided by 9.

    NaN where ``sma(close, 200)`` is NaN.
    """
    r = rules.all_rules(close)
    total = sum(
        (v.fillna(0.0) for v in r.values()),
        pd.Series(0.0, index=close.index),
    )
    return (total / 9).where(r["sma200"].notna())
