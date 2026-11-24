#!/usr/bin/env python3
"""Fetch daily OHLCV for the packaged multi-asset backtest fixture.

Pulls daily klines from Binance's public data API (no API key) for eight
liquid USD pairs and writes ``src/trader/backtest/data/multi_asset_daily.csv``
with columns ``date, <SYM>_open, <SYM>_high, <SYM>_low, <SYM>_close`` per
symbol. Pre-listing dates stay empty; nothing is forward-filled.

Usage::

    python scripts/fetch_backtest_data.py
"""

import csv
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://data-api.binance.vision/api/v3/klines"
OUT = (
    Path(__file__).resolve().parents[1]
    / "src/trader/backtest/data/multi_asset_daily.csv"
)
START = datetime(2019, 1, 1, tzinfo=UTC)

PAIRS = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
    "DOGEUSDT": "DOGE-USD",
    "ADAUSDT": "ADA-USD",
    "AVAXUSDT": "AVAX-USD",
    "LINKUSDT": "LINK-USD",
}

LIMIT = 1000
MS_DAY = 86_400_000


def fetch_daily(symbol: str) -> dict[str, tuple[float, float, float, float]]:
    """Return {YYYY-MM-DD: (open, high, low, close)} for closed UTC days."""
    rows: dict[str, tuple[float, float, float, float]] = {}
    start_ms = int(START.timestamp() * 1000)
    today_ms = int(
        datetime.now(tz=UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
        * 1000
    )
    while start_ms < today_ms:
        url = (
            f"{API}?symbol={symbol}&interval=1d"
            f"&startTime={start_ms}&endTime={today_ms - 1}&limit={LIMIT}"
        )
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            klines = json.loads(resp.read())
        for k in klines:
            open_ms = int(k[0])
            if open_ms >= today_ms:  # still-open day
                continue
            day = datetime.fromtimestamp(open_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
            rows[day] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
        if len(klines) < LIMIT:
            break
        start_ms = int(klines[-1][0]) + MS_DAY
        time.sleep(0.2)
    return rows


def main() -> int:
    data = {sym: fetch_daily(pair) for pair, sym in PAIRS.items()}
    dates = sorted({d for rows in data.values() for d in rows})
    fields = ["date"]
    for sym in PAIRS.values():
        fields += [f"{sym}_open", f"{sym}_high", f"{sym}_low", f"{sym}_close"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for day in dates:
            row = [day]
            for sym in PAIRS.values():
                ohlc = data[sym].get(day)
                row += list(ohlc) if ohlc else ["", "", "", ""]
            w.writerow(row)
    print(f"wrote {OUT} ({len(dates)} days, {dates[0]}..{dates[-1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
