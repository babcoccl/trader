"""Candle source interface and the in-memory candle record.

A ``CandleSource`` fetches public OHLC candles for one symbol and interval
over ``[start, end)`` where only candles whose whole period fits inside the
range may be returned (a source never yields an unfinished candle). No source
needs an API key.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from trader.core.config import get_settings

INTERVAL_SECONDS = {"1d": 86400, "5m": 300}


@dataclass(frozen=True)
class Asset:
    """One tradable asset's venue names and rounding/backfill parameters."""

    symbol: str
    bitstamp_market: str
    coinbase_product: str
    tick: Decimal  # price step
    lot: Decimal  # quantity step
    daily_start: datetime  # first daily candle to backfill (UTC)


ASSETS: dict[str, Asset] = {
    "BTC-USD": Asset(
        "BTC-USD",
        "btcusd",
        "BTC-USD",
        Decimal("0.01"),
        Decimal("0.00000001"),
        datetime(2019, 1, 1, tzinfo=UTC),
    ),
    "ETH-USD": Asset(
        "ETH-USD",
        "ethusd",
        "ETH-USD",
        Decimal("0.01"),
        Decimal("0.00000001"),
        datetime(2019, 1, 1, tzinfo=UTC),
    ),
    "SOL-USD": Asset(
        "SOL-USD",
        "solusd",
        "SOL-USD",
        Decimal("0.001"),
        Decimal("0.00001"),
        datetime(2022, 8, 18, tzinfo=UTC),
    ),
    "XRP-USD": Asset(
        "XRP-USD",
        "xrpusd",
        "XRP-USD",
        Decimal("0.00001"),
        Decimal("0.00000001"),
        datetime(2019, 1, 1, tzinfo=UTC),
    ),
    "DOGE-USD": Asset(
        "DOGE-USD",
        "dogeusd",
        "DOGE-USD",
        Decimal("0.00001"),
        Decimal("1"),
        datetime(2022, 12, 21, tzinfo=UTC),
    ),
    "ADA-USD": Asset(
        "ADA-USD",
        "adausd",
        "ADA-USD",
        Decimal("0.0001"),
        Decimal("0.1"),
        datetime(2021, 1, 1, tzinfo=UTC),
    ),
    "AVAX-USD": Asset(
        "AVAX-USD",
        "avaxusd",
        "AVAX-USD",
        Decimal("0.01"),
        Decimal("0.01"),
        datetime(2022, 1, 1, tzinfo=UTC),
    ),
    "LINK-USD": Asset(
        "LINK-USD",
        "linkusd",
        "LINK-USD",
        Decimal("0.001"),
        Decimal("0.01"),
        datetime(2021, 1, 1, tzinfo=UTC),
    ),
}


def _checked_symbols() -> tuple[str, ...]:
    """Configured assets; an unknown symbol refuses every service at import."""
    symbols = get_settings().symbol_list
    for symbol in symbols:
        if symbol not in ASSETS:
            raise ValueError(f"TRADER_SYMBOLS names an unknown asset: {symbol}")
    return symbols


SYMBOLS: tuple[str, ...] = _checked_symbols()


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
