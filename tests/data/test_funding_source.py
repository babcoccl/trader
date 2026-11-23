"""Coinbase funding-rate source tests (spec G4): the fixture response
parses, and an empty rate, a null time and a sub-hour interval raise.
Fixtures only — no network."""

import copy
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from trader.data.sources.coinbase_funding import (
    VENUE,
    CoinbaseFundingSource,
    FundingObservation,
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "research",
    "fixtures",
    "coinbase_perp_product.json",
)

BODY = json.load(open(FIXTURE))


def _source(body: Any = BODY) -> CoinbaseFundingSource:
    calls: list[tuple[str, dict[str, Any]]] = []

    def get_json(url: str, params: dict[str, Any]) -> Any:
        calls.append((url, params))
        return body

    source = CoinbaseFundingSource(get_json=get_json)
    source.calls = calls  # type: ignore[attr-defined]
    return source


def _broken(**changes: Any) -> dict[str, Any]:
    body = copy.deepcopy(BODY)
    body["future_product_details"].update(changes)
    return body


def test_fetch_parses_fixture() -> None:
    source = _source()
    obs = source.fetch("BTC-USD")
    assert obs == FundingObservation(
        symbol="BTC-USD",
        venue=VENUE,
        time=datetime(2026, 11, 23, 18, 0, tzinfo=UTC),
        rate=Decimal("0.000004"),
        interval_hours=1,
    )
    assert source.calls[0][0] == (
        "https://api.coinbase.com/api/v3/brokerage/market/products/BIP-20DEC30-CDE"
    )
    # A cache-busting parameter keeps the funding readings fresh.
    assert source.calls[0][1]


def test_poll_urls_differ() -> None:
    """Two fetches must produce different query parameters, or a cached
    response keeps serving the same funding_time."""
    source = _source()
    source.fetch("BTC-USD")
    source.fetch("BTC-USD")
    assert source.calls[0][1] != source.calls[1][1]


def test_empty_rate_raises() -> None:
    with pytest.raises(ValueError, match="funding_rate"):
        _source(_broken(funding_rate="")).fetch("BTC-USD")


def test_null_time_raises() -> None:
    with pytest.raises(ValueError, match="funding_time"):
        _source(_broken(funding_time=None)).fetch("BTC-USD")


def test_sub_hour_interval_raises() -> None:
    with pytest.raises(ValueError, match="funding_interval"):
        _source(_broken(funding_interval="1800s")).fetch("BTC-USD")
