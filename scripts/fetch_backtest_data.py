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
# Same start as research/fixtures/btc_eth_daily.csv so SMA-200 warmup is kept.
START = datetime(2017, 8, 17, tzinfo=UTC)

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
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", action="store_true", help="refresh daily CSV")
    parser.add_argument(
        "--funding", action="store_true", help="write funding_daily.csv"
    )
    parser.add_argument(
        "--intraday", action="store_true", help="write btc_eth_intraday_1h.csv"
    )
    args = parser.parse_args()
    if args.funding:
        write_funding()
    if args.intraday:
        write_intraday()
    if (args.funding or args.intraday) and not args.daily:
        return 0
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


FUNDING_API = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
# Binance fapi funding history is geo-blocked in some regions (HTTP 451/
# empty 202); Deribit's public endpoint serves the same series for
# BTC/ETH perpetuals.
FUNDING_INSTRUMENTS = {"BTC-PERPETUAL": "BTC-USD", "ETH-PERPETUAL": "ETH-USD"}
FUNDING_CHUNK_MS = 30 * 86_400_000
FUNDING_OUT = OUT.parent / "funding_daily.csv"
INTRADAY_OUT = OUT.parent / "btc_eth_intraday_1h.csv"
FUNDING_PAIRS = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}
INTRADAY_START = datetime(2020, 1, 1, tzinfo=UTC)
MS_HOUR = 3_600_000


def _get(url: str) -> list:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


def fetch_funding(instrument: str) -> dict[str, float]:
    """{YYYY-MM-DD: mean funding rate} from Deribit perp funding history."""
    rows: dict[str, list[float]] = {}
    start_ms = int(INTRADAY_START.timestamp() * 1000)
    end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    while start_ms < end_ms:
        data = _get(
            f"{FUNDING_API}?instrument_name={instrument}"
            f"&start_timestamp={start_ms}"
            f"&end_timestamp={min(start_ms + FUNDING_CHUNK_MS, end_ms)}"
        )["result"]
        if not data:
            break
        for r in data:
            day = datetime.fromtimestamp(int(r["timestamp"]) / 1000, tz=UTC).strftime(
                "%Y-%m-%d"
            )
            rows.setdefault(day, []).append(float(r["interest_8h"]))
        start_ms = int(data[-1]["timestamp"]) + 1
        if len(data) < 90:
            break
        time.sleep(0.2)
    return {d: sum(v) / len(v) for d, v in rows.items()}


def fetch_intraday(symbol: str) -> dict[str, tuple[float, float, float, float]]:
    """{ISO hour: (open, high, low, close)} of closed 1h bars."""
    rows: dict[str, tuple[float, float, float, float]] = {}
    start_ms = int(INTRADAY_START.timestamp() * 1000)
    end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    while start_ms < end_ms:
        klines = _get(
            f"{API}?symbol={symbol}&interval=1h"
            f"&startTime={start_ms}&endTime={end_ms - 1}&limit={LIMIT}"
        )
        if not klines:
            break
        for k in klines:
            open_ms = int(k[0])
            if open_ms + MS_HOUR > end_ms:  # still-open bar
                continue
            ts = datetime.fromtimestamp(open_ms / 1000, tz=UTC).strftime(
                "%Y-%m-%d %H:00:00"
            )
            rows[ts] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
        start_ms = int(klines[-1][0]) + MS_HOUR
        if len(klines) < LIMIT:
            break
        time.sleep(0.2)
    return rows


def write_funding() -> None:
    data = {sym: fetch_funding(inst) for inst, sym in FUNDING_INSTRUMENTS.items()}
    dates = sorted({d for rows in data.values() for d in rows})
    with FUNDING_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", *FUNDING_INSTRUMENTS.values()])
        for day in dates:
            row = [day]
            row += [data[s].get(day, "") for s in FUNDING_INSTRUMENTS.values()]
            w.writerow(row)
    print(f"wrote {FUNDING_OUT} ({len(dates)} days)")


def write_intraday() -> None:
    data = {sym: fetch_intraday(pair) for pair, sym in FUNDING_PAIRS.items()}
    stamps = sorted({t for rows in data.values() for t in rows})
    with INTRADAY_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        fields = ["timestamp"]
        for sym in FUNDING_PAIRS.values():
            fields += [f"{sym}_open", f"{sym}_high", f"{sym}_low", f"{sym}_close"]
        w.writerow(fields)
        for ts in stamps:
            row = [ts]
            for sym in FUNDING_PAIRS.values():
                ohlc = data[sym].get(ts)
                row += list(ohlc) if ohlc else ["", "", "", ""]
            w.writerow(row)
    print(f"wrote {INTRADAY_OUT} ({len(stamps)} hours)")


if __name__ == "__main__":
    sys.exit(main())
