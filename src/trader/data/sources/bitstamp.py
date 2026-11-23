"""Bitstamp public REST source (primary).

``GET {BASE}/ohlc/{market}/?step=<300|86400>&limit=<=1000&start=<unix s>``
returns ``data.ohlc[]`` rows ``{timestamp, open, high, low, close, volume}``
as strings. No API key. Documented limit: 10,000 requests per 10 minutes
(https://www.bitstamp.net/api/ — "What is API" rate limits).
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trader.data.sources.base import ASSETS, Candle, period_seconds

BASE_URL = "https://www.bitstamp.net/api/v2"
MARKETS = {s: a.bitstamp_market for s, a in ASSETS.items()}

GetJson = Callable[[str, dict[str, Any]], Any]


def _get_json(url: str, params: dict[str, Any]) -> Any:
    with urllib.request.urlopen(
        url + "?" + urllib.parse.urlencode(params), timeout=30
    ) as r:
        return json.loads(r.read())


class BitstampSource:
    name = "bitstamp"

    def __init__(self, get_json: GetJson = _get_json, base_url: str = BASE_URL) -> None:
        self._get = get_json
        self._base = base_url.rstrip("/")

    def fetch(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        step = period_seconds(interval)
        market = MARKETS[symbol]
        out: list[Candle] = []
        cursor = start
        while cursor < end:
            payload = self._get(
                f"{self._base}/ohlc/{market}/",
                {"step": step, "limit": 1000, "start": int(cursor.timestamp())},
            )
            rows = payload["data"]["ohlc"]
            if not rows:
                break
            last_ts = 0
            for row in rows:
                ts = int(row["timestamp"])
                last_ts = max(last_ts, ts)
                t = datetime.fromtimestamp(ts, UTC)
                if t < start or t + timedelta(seconds=step) > end:
                    continue
                out.append(
                    Candle(
                        symbol=symbol,
                        interval=interval,
                        open_time=t,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                        source=self.name,
                    )
                )
            if len(rows) < 1000:
                break
            cursor = datetime.fromtimestamp(last_ts + step, UTC)
        return out
