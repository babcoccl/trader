"""Coinbase Exchange public candles source (backup).

``GET {BASE}/products/{product}/candles?granularity=<300|86400>&start=<ISO>
&end=<ISO>`` returns a list of ``[time, low, high, open, close, volume]``
rows (numbers, newest first). No API key. At most 300 candles per request —
a wider range is rejected — so ``fetch`` pages in 300-period chunks.
Docs: https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/
products/get-product-candles
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trader.data.sources.base import Candle, period_seconds

BASE_URL = "https://api.exchange.coinbase.com"
PRODUCTS = {"BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD"}
MAX_CANDLES = 300

GetJson = Callable[[str, dict[str, Any]], Any]


def _get_json(url: str, params: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "trader/0.1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class CoinbaseSource:
    name = "coinbase"

    def __init__(self, get_json: GetJson = _get_json, base_url: str = BASE_URL) -> None:
        self._get = get_json
        self._base = base_url.rstrip("/")

    def fetch(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        step = period_seconds(interval)
        product = PRODUCTS[symbol]
        out: list[Candle] = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(seconds=step * MAX_CANDLES), end)
            rows = self._get(
                f"{self._base}/products/{product}/candles",
                {
                    "granularity": step,
                    "start": cursor.isoformat(),
                    "end": chunk_end.isoformat(),
                },
            )
            if not rows:
                break
            for time, low, high, open_, close, volume in rows:
                t = datetime.fromtimestamp(int(time), UTC)
                if t < start or t + timedelta(seconds=step) > end:
                    continue
                out.append(
                    Candle(
                        symbol=symbol,
                        interval=interval,
                        open_time=t,
                        open=Decimal(str(open_)),
                        high=Decimal(str(high)),
                        low=Decimal(str(low)),
                        close=Decimal(str(close)),
                        volume=Decimal(str(volume)),
                        source=self.name,
                    )
                )
            cursor = chunk_end
        out.sort(key=lambda c: c.open_time)
        return out
