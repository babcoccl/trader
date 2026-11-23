"""Regime label for a daily decision (spec G1).

Trend (uptrend, mixed or downtrend), volatility (calm or volatile) and a
sideways reading (Wilder ADX under 20), computed only from the candles the
decision already uses. The label is recorded in ``decisions.details`` and
shown in the reason and on the Rules screen; it enters no target weight,
plan, risk check or approval.
"""

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from trader.strategy import sizing

Trend = Literal["uptrend", "mixed", "downtrend"]
Volatility = Literal["calm", "volatile"]

ADX_PERIOD = 14
ADX_SIDEWAYS = 20.0
VOL_RANK_WINDOW = 365
VOL_RANK_MIN = 180


@dataclass(frozen=True)
class Regime:
    trend: Trend
    volatility: Volatility | None  # None: under 180 days of volatility
    sideways: bool | None  # None: ADX not yet defined
    vol_percentile: float | None
    adx: float | None

    def text(self) -> str:
        """``"Downtrend, volatile"``, ``"Mixed, calm, sideways reading"``."""
        parts = [self.trend.capitalize()]
        if self.volatility is not None:
            parts.append(self.volatility)
        if self.sideways is True:
            parts.append("sideways reading")
        return ", ".join(parts)

    def as_details(self) -> dict[str, Any]:
        """JSON-safe form for ``decisions.details``; ``None``, never NaN."""
        return {
            "trend": self.trend,
            "volatility": self.volatility,
            "sideways": self.sideways,
            "vol_percentile": self.vol_percentile,
            "adx": self.adx,
        }


def trend(rules_positive: int) -> Trend:
    """7-9 rules on is uptrend; 0-2 downtrend; 3-6 mixed."""
    if rules_positive >= 7:
        return "uptrend"
    if rules_positive <= 2:
        return "downtrend"
    return "mixed"


def vol_percentile(close: pd.Series) -> pd.Series:
    """Percentile rank of the annualized vol within its last 365 days."""
    return (
        sizing.annual_vol(close)
        .rolling(VOL_RANK_WINDOW, min_periods=VOL_RANK_MIN)
        .rank(pct=True)
    )


def _wilder(x: pd.Series) -> pd.Series:
    return x.ewm(alpha=1 / ADX_PERIOD, min_periods=ADX_PERIOD).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder's ADX over ``ADX_PERIOD`` days."""
    up = high - high.shift(1)
    down = low.shift(1) - low
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    tr_w = _wilder(tr)
    plus_di = 100 * _wilder(plus_dm) / tr_w
    minus_di = 100 * _wilder(minus_dm) / tr_w
    return _wilder(100 * (plus_di - minus_di).abs() / (plus_di + minus_di))


def label(
    rules_positive: int, high: pd.Series, low: pd.Series, close: pd.Series
) -> Regime:
    """The regime of the last row of the series given."""
    vp = float(vol_percentile(close).iloc[-1])
    a = float(adx(high, low, close).iloc[-1])
    volatility: Volatility | None = None
    if not pd.isna(vp):
        volatility = "calm" if vp < 0.5 else "volatile"
    return Regime(
        trend=trend(rules_positive),
        volatility=volatility,
        sideways=None if pd.isna(a) else bool(a < ADX_SIDEWAYS),
        vol_percentile=None if pd.isna(vp) else vp,
        adx=None if pd.isna(a) else a,
    )


def from_details(details: dict[str, Any]) -> Regime | None:
    """Inverse of ``as_details``; ``None`` when ``trend`` is missing."""
    if details.get("trend") is None:
        return None
    return Regime(
        trend=details["trend"],
        volatility=details.get("volatility"),
        sideways=details.get("sideways"),
        vol_percentile=details.get("vol_percentile"),
        adx=details.get("adx"),
    )
