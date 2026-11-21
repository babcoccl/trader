"""Candle source interface and the in-memory candle record.

A ``CandleSource`` fetches public OHLC candles for one symbol and interval
over ``[start, end)`` where only candles whose whole period fits inside the
range may be returned (a source never yields an unfinished candle). No source
needs an API key.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

INTERVAL_SECONDS = {"1d": 86400, "5m": 300}

SYMBOLS = ("BTC-USD", "ETH-USD")


@dataclass(frozen=True)
class Candle:
    """One finished OHLC candle as returned by a source."""

    symbol: str
    interval: str
    open_time: datetime  # UTC, timezone-aware, period start
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str  # venue name, e.g. "bitstamp"


class CandleSource(Protocol):
    """A public market-data endpoint."""

    name: str

    def fetch(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Candles with ``open_time >= start`` and ``open_time + period <= end``."""
        ...


def period_seconds(interval: str) -> int:
    return INTERVAL_SECONDS[interval]


def is_closed(candle: Candle, now: datetime) -> bool:
    """True when the candle's whole period lies at or before ``now``."""
    return (candle.open_time.timestamp() + period_seconds(candle.interval)) <= (
        now.timestamp()
    )
