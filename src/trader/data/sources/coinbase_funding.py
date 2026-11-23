"""Coinbase Advanced Trade public perpetual funding-rate source (spec G4).

``GET {BASE}/api/v3/brokerage/market/products/{product}`` returns product
details; ``future_product_details`` holds ``funding_rate`` (a decimal
string, the rate for one interval, positive when longs pay),
``funding_time`` (ISO time) and ``funding_interval`` (``"3600s"``). No API
key. The same fields under ``perpetual_details`` are empty for these
products and must not be used. Coinbase publishes no funding history on
this endpoint, so the record starts when the collector first runs.
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

BASE_URL = "https://api.coinbase.com"
VENUE = "coinbase_perp"
PRODUCTS = {"BTC-USD": "BIP-20DEC30-CDE", "ETH-USD": "ETP-20DEC30-CDE"}

GetJson = Callable[[str, dict[str, Any]], Any]


def _get_json(url: str, params: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "trader/0.1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


@dataclass(frozen=True)
class FundingObservation:
    symbol: str
    venue: str
    time: datetime  # funding_time, UTC
    rate: Decimal
    interval_hours: int


def _interval_hours(raw: Any) -> int:
    """``"3600s"`` -> 1; ``ValueError`` unless a whole number of hours."""
    text = str(raw or "")
    if not text.endswith("s") or not text[:-1].isdigit():
        raise ValueError(f"unreadable funding_interval {raw!r}")
    seconds = int(text[:-1])
    if seconds <= 0 or seconds % 3600 != 0:
        raise ValueError(f"funding_interval {raw!r} is not a whole number of hours")
    return seconds // 3600


class CoinbaseFundingSource:
    def __init__(self, get_json: GetJson = _get_json, base_url: str = BASE_URL) -> None:
        self._get = get_json
        self._base = base_url.rstrip("/")
        self._seq = 0

    def fetch(self, symbol: str) -> FundingObservation:
        product = PRODUCTS[symbol]
        self._seq += 1
        body = self._get(
            f"{self._base}/api/v3/brokerage/market/products/{product}",
            # Unknown parameter, ignored by Coinbase: it varies the URL so a
            # cached response cannot keep serving the same funding_time.
            {"_": str(self._seq)},
        )
        details = body.get("future_product_details") or {}
        if not details.get("funding_rate"):
            raise ValueError(f"{product}: empty funding_rate")
        if details.get("funding_time") is None:
            raise ValueError(f"{product}: null funding_time")
        interval = _interval_hours(details.get("funding_interval"))
        raw_time = str(details["funding_time"]).replace("Z", "+00:00")
        time = datetime.fromisoformat(raw_time)
        return FundingObservation(
            symbol=symbol,
            venue=VENUE,
            time=time,
            rate=Decimal(str(details["funding_rate"])),
            interval_hours=interval,
        )
