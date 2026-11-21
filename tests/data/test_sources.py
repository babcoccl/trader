"""Source parsing tests with recorded JSON fixtures. No network or database."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.data.sources.bitstamp import BitstampSource
from trader.data.sources.coinbase import CoinbaseSource

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


START = datetime(2026, 11, 17, tzinfo=UTC)
END = datetime(2026, 11, 20, tzinfo=UTC)


def test_bitstamp_fetch_parses_fixture() -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any]) -> Any:
        calls.append(params)
        assert url.endswith("/ohlc/btcusd/")
        return _fixture("bitstamp_ohlc.json")

    src = BitstampSource(get_json=fake_get)
    out = src.fetch("BTC-USD", "1d", START, END)

    assert len(out) == 3
    assert calls[0]["step"] == 86400
    first = out[0]
    assert first.symbol == "BTC-USD"
    assert first.interval == "1d"
    assert first.open_time == START
    assert first.open == Decimal("94100.00")
    assert first.close == Decimal("95000.00")
    assert first.source == "bitstamp"


def test_bitstamp_paginates_at_1000_rows() -> None:
    step = 300
    first_page = {
        "data": {
            "ohlc": [
                {
                    "timestamp": str(1794873600 + i * step),
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "volume": "1",
                }
                for i in range(1000)
            ]
        }
    }
    second_page = {
        "data": {
            "ohlc": [
                {
                    "timestamp": str(1794873600 + i * step),
                    "open": "2",
                    "high": "2",
                    "low": "2",
                    "close": "2",
                    "volume": "2",
                }
                for i in (1000, 1001)
            ]
        }
    }
    pages = [first_page, second_page]
    calls: list[int] = []

    def fake_get(url: str, params: dict[str, Any]) -> Any:
        calls.append(params["start"])
        return pages[len(calls) - 1]

    src = BitstampSource(get_json=fake_get)
    end = datetime.fromtimestamp(1794873600 + 1002 * step, UTC)
    out = src.fetch("BTC-USD", "5m", datetime.fromtimestamp(1794873600, UTC), end)

    assert len(calls) == 2
    assert calls[1] == 1794873600 + 1000 * step
    assert len(out) == 1002
    assert out[-1].open == Decimal("2")


def test_bitstamp_excludes_out_of_range_and_unfinished() -> None:
    src = BitstampSource(get_json=lambda u, p: _fixture("bitstamp_ohlc.json"))
    # end cuts off the last candle's period.
    out = src.fetch("BTC-USD", "1d", START, datetime(2026, 11, 19, 12, tzinfo=UTC))
    assert [c.open_time for c in out] == [
        datetime(2026, 11, 17, tzinfo=UTC),
        datetime(2026, 11, 18, tzinfo=UTC),
    ]


def test_coinbase_fetch_parses_fixture() -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any]) -> Any:
        calls.append(params)
        assert url.endswith("/products/BTC-USD/candles")
        return _fixture("coinbase_candles.json")

    src = CoinbaseSource(get_json=fake_get)
    out = src.fetch("BTC-USD", "1d", START, END)

    assert calls[0]["granularity"] == 86400
    assert [c.open_time for c in out] == [
        datetime(2026, 11, 17, tzinfo=UTC),
        datetime(2026, 11, 18, tzinfo=UTC),
        datetime(2026, 11, 19, tzinfo=UTC),
    ]
    first = out[0]
    assert first.low == Decimal("93800.0")
    assert first.high == Decimal("95200.0")
    assert first.volume == Decimal("1230.75")
    assert first.source == "coinbase"


def test_coinbase_pages_in_300_candle_chunks() -> None:
    seen: list[tuple[str, str]] = []

    def fake_get(url: str, params: dict[str, Any]) -> Any:
        seen.append((params["start"], params["end"]))
        if len(seen) == 1:
            return [[1794873600, 1, 1, 1, 1, 1]]
        return []

    src = CoinbaseSource(get_json=fake_get)
    end = START + timedelta(days=2)  # 2 days > 300 5m periods
    src.fetch("BTC-USD", "5m", START, end)

    assert len(seen) == 2
    assert seen[0][0] == START.isoformat()
    assert seen[0][1] == (START + timedelta(seconds=300 * 300)).isoformat()
    assert seen[1][1] == end.isoformat()
