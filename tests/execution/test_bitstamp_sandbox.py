"""Opt-in Bitstamp sandbox integration test (spec 5.2).

Runs only when TRADER_SANDBOX_TEST=1 and sandbox credentials are set:

    TRADER_SANDBOX_TEST=1 \
    TRADER_BITSTAMP_KEY=... TRADER_BITSTAMP_SECRET=... \
    TRADER_BITSTAMP_BASE_URL=https://sandbox.bitstamp.net/api/v2 \
    TRADER_MODE=live TRADER_LIVE_ENABLED=true \
    pytest tests/execution/test_bitstamp_sandbox.py

It places one tiny limit buy on the SANDBOX priced a cent above zero
(can never fill), reads it back through open_orders, then cancels it.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.execution.bitstamp import BitstampAdapter
from trader.ledger.models import Order

pytestmark = pytest.mark.skipif(
    os.environ.get("TRADER_SANDBOX_TEST") != "1",
    reason="opt-in: set TRADER_SANDBOX_TEST=1 with sandbox credentials",
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    with Session(engine) as s:
        yield s


def test_sandbox_limit_order_lifecycle(session) -> None:
    from trader.core.config import get_settings

    get_settings.cache_clear()
    adapter = BitstampAdapter.from_env(session)
    order = Order(
        plan_id=0,
        venue="",
        client_order_id="devin-sandbox-test",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("0.02"),
        quantity=Decimal("0.4"),
        status="new",
        mode="live",
    )
    venue_id = adapter.place(order)
    assert venue_id
    venue_orders = adapter.venue_open_orders()
    assert venue_id in {row["id"] for row in venue_orders}
    assert adapter.cancel(venue_id) is True
    get_settings.cache_clear()
