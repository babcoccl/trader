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

from trader.data.sources.base import ASSETS, Candle, period_seconds

BASE_URL = "https://api.exchange.coinbase.com"
PRODUCTS = {s: a.coinbase_product for s, a in ASSETS.items()}
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
        seen: set[int] = set()
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
            for time, low, high, open_, close, volume in rows:
                ts = int(time)
                t = datetime.fromtimestamp(ts, UTC)
                # Skip candles before this chunk (Coinbase may return them) and
                # the boundary candle already collected from the previous chunk.
                if t < cursor or ts in seen:
                    continue
                if t + timedelta(seconds=step) > end:
                    continue
                seen.add(ts)
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
            # An empty chunk means no trades in that window, not the end of
            # history: keep paging.
            cursor = chunk_end
        out.sort(key=lambda c: c.open_time)
        return out
