"""Sudden-drop ("dip bid") maths for the five-minute evaluation cycle (spec 1.5).

Pure functions only: no clock, database or network access. The cycle observes
and records; placing dip bids is parked (spec 5.5).
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

RETURNS_WINDOW = 288  # 288 five-minute returns = one day of 5m bars


def hourly_sigma(close_5m: pd.Series) -> float:
    """Std of the last 288 five-minute log returns, times ``sqrt(12)``.

    NaN when fewer than 288 returns are available (needs 289 closes).
    """
    rets = (
        pd.Series(np.log(close_5m.astype(float))).diff().dropna().tail(RETURNS_WINDOW)
    )
    if len(rets) < RETURNS_WINDOW:
        return float("nan")
    return float(rets.std() * math.sqrt(12))


@dataclass(frozen=True)
class DipQuote:
    bid: float
    target: float
    stop: float


def quote(
    last_close: float,
    sigma: float,
    k: float = 4.0,
    target_mult: float = 0.5,
    stop_mult: float = 2.0,
) -> DipQuote:
    """The resting bid for a sudden drop and its exit levels.

    ``bid`` sits ``k * sigma`` under the last close, ``target`` is half that
    distance above the bid, ``stop`` twice that distance below it.
    """
    bid = last_close * (1 - k * sigma)
    return DipQuote(
        bid=bid,
        target=bid * (1 + target_mult * k * sigma),
        stop=bid * (1 - stop_mult * k * sigma),
    )


def trend_gate(yesterday_ensemble: float) -> bool:
    """Dip bids are only considered when yesterday's trend signal is >= 0.5."""
    return yesterday_ensemble >= 0.5
