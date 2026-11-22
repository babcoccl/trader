"""Opt-in Bitstamp sandbox integration test (spec 5.2).

Runs only when TRADER_SANDBOX_TEST=1 and sandbox credentials are set.

Unix shell:

    TRADER_SANDBOX_TEST=1 TRADER_MODE=live TRADER_LIVE_ENABLED=true \
    TRADER_BITSTAMP_KEY=... TRADER_BITSTAMP_SECRET=... \
    TRADER_BITSTAMP_BASE_URL=https://sandbox.bitstamp.net/api/v2 \
    pytest tests/execution/test_bitstamp_sandbox.py

PowerShell:

    $env:TRADER_SANDBOX_TEST="1"; $env:TRADER_MODE="live"
    $env:TRADER_LIVE_ENABLED="true"
    $env:TRADER_BITSTAMP_KEY="..."; $env:TRADER_BITSTAMP_SECRET="..."
    $env:TRADER_BITSTAMP_BASE_URL="https://sandbox.bitstamp.net/api/v2"
    pytest tests/execution/test_bitstamp_sandbox.py

Places one limit buy on the SANDBOX 15% under the best bid (inside the
venue's 20% deviation limit, far enough not to fill) sized at 1.5x the
market's minimum order value, reads it back through open_orders, then
cancels it.
"""

import os
from decimal import ROUND_UP, Decimal

import pytest

from trader.core.config import get_settings
from trader.execution.bitstamp import BitstampAdapter
from trader.ledger.models import Order

pytestmark = pytest.mark.skipif(
    os.environ.get("TRADER_SANDBOX_TEST") != "1",
    reason="opt-in: set TRADER_SANDBOX_TEST=1 with sandbox credentials",
)


class _NoDb:
    """The lifecycle needs no database: no stored order to update."""

    def flush(self) -> None:
        pass

    def scalars(self, *_args: object, **_kw: object) -> "_NoDb":
        return self

    def first(self) -> None:
        return None


def test_sandbox_limit_order_lifecycle() -> None:
    get_settings.cache_clear()
    assert "sandbox" in get_settings().bitstamp_base_url, "sandbox only"
    adapter = BitstampAdapter.from_env(_NoDb())  # type: ignore[arg-type]
    rules = adapter.symbol_rules("BTC-USD")
    price = (adapter.ticker("BTC-USD")["bid"] * Decimal("0.85")).quantize(rules.tick)
    quantity = (rules.min_notional * Decimal("1.5") / price).quantize(
        rules.lot, rounding=ROUND_UP
    )
    order = Order(
        plan_id=0,
        venue="",
        client_order_id="sandbox-lifecycle-test",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=price,
        quantity=quantity,
        status="new",
        mode="live",
    )
    venue_id = adapter.place(order)
    try:
        assert venue_id in {str(row["id"]) for row in adapter.venue_open_orders()}
    finally:
        assert adapter.cancel(venue_id) is True
    get_settings.cache_clear()
