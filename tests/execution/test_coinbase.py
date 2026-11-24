"""Coinbase Advanced Trade live adapter tests (spec W1.1b).

Transport is a fake callable; nothing here talks to Coinbase. The EC
P-256 keypair is generated in memory per test run — no real keys are
ever committed. DB tests need TEST_DATABASE_URL (Compose Postgres).
"""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.core.config import get_settings
from trader.execution.coinbase import (
    CoinbaseAdvancedAdapter,
    CoinbaseError,
    _products_cache,
)
from trader.ledger.models import (
    AuditLog,
    Balance,
    Candle,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    Heartbeat,
    Order,
    Plan,
    Setting,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)


def _ec_private_key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class FakeHttp:
    """Routes adapter calls to canned payloads by method and path."""

    def __init__(
        self,
        gets: dict[str, Any] | None = None,
        posts: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, str], str | None]] = []
        self.gets = gets or {}
        self.posts = posts or {}

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> Any:
        self.calls.append((method, url, headers, body))
        table = self.posts if method == "POST" else self.gets
        path = url.split("/api/", 1)[1]
        for suffix, payload in table.items():
            if path.startswith(suffix) or url.split("?")[0].endswith(suffix):
                return payload
        raise AssertionError(f"no fixture for {method} {url}")


PRODUCT = {
    "product_id": "BTC-USD",
    "quote_increment": "0.01",
    "base_increment": "0.00000001",
    "quote_min_size": "1.00",
}

PLACE_OK = {
    "success": True,
    "success_response": {"order_id": "0000-aaaa-1111"},
    "order_configuration": {},
}


def _adapter(
    http: FakeHttp, session: Any = None, mode: str = "live"
) -> CoinbaseAdvancedAdapter:
    return CoinbaseAdvancedAdapter(
        session or _SessionStub(),
        "organizations/test-org/apiKeys/test-key-id",
        _ec_private_key_pem(),
        clock=FrozenClock(NOW),
        mode=mode,
        http=http,
        nonce_fn=lambda: "00000000-0000-0000-0000-000000000001",
    )


class _EmptyResult:
    def first(self) -> None:
        return None

    def __iter__(self):
        return iter(())


class _SessionStub:
    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders = orders or []

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def scalars(self, *_args: Any, **_kw: Any):
        return self._orders

    def scalar(self, *_args: Any, **_kw: Any) -> int:
        return 0


def _order(**kw: Any) -> Order:
    fields = dict(
        plan_id=1,
        venue="",
        client_order_id="1-1",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("49000.123"),
        quantity=Decimal("0.002000009"),
        status="new",
        mode="live",
    )
    fields.update(kw)
    return Order(**fields)


@pytest.fixture(autouse=True)
def _fresh_products_cache():
    _products_cache.clear()
    yield
    _products_cache.clear()


@pytest.fixture()
def live_env(monkeypatch):
    monkeypatch.setenv("TRADER_MODE", "live")
    monkeypatch.setenv("TRADER_LIVE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _product_http() -> FakeHttp:
    return FakeHttp(gets={"products/BTC-USD": PRODUCT})


# -- JWT ------------------------------------------------------------------


def test_jwt_header_and_claims() -> None:
    http = _product_http()
    adapter = _adapter(http)
    adapter.symbol_rules("BTC-USD")
    _, _, headers, _ = http.calls[0]
    token = headers["Authorization"].removeprefix("Bearer ")
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "organizations/test-org/apiKeys/test-key-id"
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "cdp"
    assert claims["sub"] == "organizations/test-org/apiKeys/test-key-id"
    assert claims["uri"] == ("GET api.coinbase.com/api/v3/brokerage/products/BTC-USD")
    assert claims["exp"] - claims["nbf"] == 120


def test_jwt_signature_verifies() -> None:
    http = _product_http()
    adapter = _adapter(http)
    adapter.symbol_rules("BTC-USD")
    token = http.calls[0][2]["Authorization"].removeprefix("Bearer ")
    # Recover the public key from the adapter's private key PEM.
    private_key = serialization.load_pem_private_key(
        adapter._secret.encode(), password=None
    )
    public_key = private_key.public_key()
    claims = jwt.decode(
        token, public_key, algorithms=["ES256"], options={"verify_exp": False}
    )
    assert claims["iss"] == "cdp"


# -- place safety gate -----------------------------------------------------


def test_place_refuses_in_paper_mode() -> None:
    adapter = _adapter(_product_http())
    with pytest.raises(RuntimeError, match="refusing to place"):
        adapter.place(_order())


def test_place_refuses_when_live_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TRADER_MODE", "live")
    monkeypatch.setenv("TRADER_LIVE_ENABLED", "false")
    get_settings.cache_clear()
    adapter = _adapter(_product_http())
    with pytest.raises(RuntimeError, match="refusing to place"):
        adapter.place(_order())
    get_settings.cache_clear()


# -- place ----------------------------------------------------------------


def test_place_limit_posts_post_only_and_rounds(live_env) -> None:
    http = FakeHttp(
        gets={"products/BTC-USD": PRODUCT},
        posts={"brokerage/orders": PLACE_OK},
    )
    adapter = _adapter(http)
    order = _order(price=Decimal("49000.129"), quantity=Decimal("0.002000009"))
    venue_id = adapter.place(order)
    method, url, _, body = http.calls[-1]
    assert method == "POST"
    assert url.endswith("/api/v3/brokerage/orders")
    sent = json.loads(body or "{}")
    assert sent["product_id"] == "BTC-USD"
    assert sent["side"] == "BUY"
    cfg = sent["order_configuration"]["limit_limit_gtc"]
    assert cfg["post_only"] is True
    assert cfg["limit_price"] == "49000.12"  # rounded down to the tick
    assert cfg["base_size"] == "0.00200000"  # rounded down to the lot
    assert venue_id == "0000-aaaa-1111"
    assert order.venue == "coinbase"
    assert order.venue_order_id == venue_id
    assert order.status == "open"


def test_place_market_order(live_env) -> None:
    http = FakeHttp(
        gets={"products/BTC-USD": PRODUCT},
        posts={"brokerage/orders": PLACE_OK},
    )
    adapter = _adapter(http)
    order = _order(side="sell", type="market", price=None, quantity=Decimal("0.002"))
    adapter.place(order)
    sent = json.loads(http.calls[-1][3] or "{}")
    assert sent["side"] == "SELL"
    assert "market_market_ioc" in sent["order_configuration"]
    assert "limit_limit_gtc" not in sent["order_configuration"]


def test_place_refuses_below_minimum(live_env) -> None:
    http = _product_http()
    adapter = _adapter(http)
    with pytest.raises(ValueError, match="minimum"):
        adapter.place(_order(price=Decimal("49000"), quantity=Decimal("0.00000001")))


def test_place_refuses_stop_order(live_env) -> None:
    adapter = _adapter(_product_http())
    with pytest.raises(ValueError, match="stop"):
        adapter.place(_order(type="stop", stop_price=Decimal("40000")))


def test_place_post_only_rejection_requotes_at_best_bid(live_env) -> None:
    http = FakeHttp(
        gets={
            "products/BTC-USD": PRODUCT,
            "brokerage/best_bid_ask": {
                "pricebooks": [
                    {
                        "product_id": "BTC-USD",
                        "bids": [{"price": "49500.50", "size": "1"}],
                        "asks": [{"price": "49501.00", "size": "1"}],
                    }
                ]
            },
        },
        posts={
            "brokerage/orders": [
                {
                    "success": False,
                    "error_response": {"error": "INVALID_LIMIT_PRICE_POST_ONLY"},
                },
                PLACE_OK,
            ]
        },
    )

    # Pop the first response, then serve the second.
    responses = http.posts["brokerage/orders"]

    def http_call(method, url, headers, body):
        http.calls.append((method, url, headers, body))
        if method == "POST":
            return responses.pop(0)
        path = url.split("?")[0]
        for suffix, payload in http.gets.items():
            if path.endswith(suffix):
                return payload
        raise AssertionError(url)

    adapter = CoinbaseAdvancedAdapter(
        _SessionStub(),
        "organizations/test-org/apiKeys/test-key-id",
        _ec_private_key_pem(),
        clock=FrozenClock(NOW),
        http=http_call,
    )
    venue_id = adapter.place(_order(price=Decimal("49501.50")))
    assert venue_id == "0000-aaaa-1111"
    sent = json.loads(http.calls[-1][3] or "{}")
    cfg = sent["order_configuration"]["limit_limit_gtc"]
    assert cfg["limit_price"] == "49500.50"  # re-quoted at the best bid


def test_place_post_only_rejection_gives_up(live_env) -> None:
    rejected = {
        "success": False,
        "error_response": {"error": "INVALID_LIMIT_PRICE_POST_ONLY"},
    }
    calls = []

    def http_call(method, url, headers, body):
        calls.append((method, url))
        if method == "POST":
            return rejected
        path = url.split("?")[0]
        if path.endswith("products/BTC-USD"):
            return PRODUCT
        return {
            "pricebooks": [
                {"bids": [{"price": "49500.50"}], "asks": [{"price": "49501"}]}
            ]
        }

    adapter = CoinbaseAdvancedAdapter(
        _SessionStub(),
        "organizations/test-org/apiKeys/test-key-id",
        _ec_private_key_pem(),
        clock=FrozenClock(NOW),
        http=http_call,
    )
    with pytest.raises(CoinbaseError, match="post-only"):
        adapter.place(_order(price=Decimal("49501.50")))


def test_place_post_only_requote_never_crosses_limit(live_env) -> None:
    """If the book moves so the best bid sits above the submitted limit,
    the re-quote is refused instead of buying higher than approved."""
    rejected = {
        "success": False,
        "error_response": {"error": "INVALID_LIMIT_PRICE_POST_ONLY"},
    }

    def http_call(method, url, headers, body):
        if method == "POST":
            return rejected
        path = url.split("?")[0]
        if path.endswith("products/BTC-USD"):
            return PRODUCT
        return {
            "pricebooks": [
                {"bids": [{"price": "99999.00"}], "asks": [{"price": "99999.50"}]}
            ]
        }

    adapter = CoinbaseAdvancedAdapter(
        _SessionStub(),
        "organizations/test-org/apiKeys/test-key-id",
        _ec_private_key_pem(),
        clock=FrozenClock(NOW),
        http=http_call,
    )
    with pytest.raises(CoinbaseError, match="cross the submitted limit"):
        adapter.place(_order(price=Decimal("49000")))


def test_place_error_raises(live_env) -> None:
    http = FakeHttp(
        gets={"products/BTC-USD": PRODUCT},
        posts={
            "brokerage/orders": {
                "success": False,
                "error_response": {"error": "INSUFFICIENT_FUND"},
            }
        },
    )
    with pytest.raises(CoinbaseError):
        _adapter(http).place(_order(price=Decimal("49000")))


# -- cancel / balances / symbol_rules / fee_summary -------------------------


def test_cancel_posts_order_ids() -> None:
    http = FakeHttp(
        posts={
            "brokerage/orders/batch_cancel": {
                "results": [{"success": True, "order_id": "abc"}]
            }
        }
    )
    assert _adapter(http).cancel("abc") is True
    sent = json.loads(http.calls[0][3] or "{}")
    assert sent == {"order_ids": ["abc"]}


def test_cancel_not_success_returns_false() -> None:
    http = FakeHttp(
        posts={"brokerage/orders/batch_cancel": {"results": [{"success": False}]}}
    )
    assert _adapter(http).cancel("abc") is False


def test_balances_parses_accounts() -> None:
    http = FakeHttp(
        gets={
            "brokerage/accounts": {
                "accounts": [
                    {
                        "currency": "USD",
                        "available_balance": {"value": "1000.00", "currency": "USD"},
                        "hold": {"value": "0", "currency": "USD"},
                    },
                    {
                        "currency": "BTC",
                        "available_balance": {"value": "0.001", "currency": "BTC"},
                        "hold": {"value": "0.001", "currency": "BTC"},
                    },
                    {
                        "currency": "ETH",
                        "available_balance": {"value": "0", "currency": "ETH"},
                        "hold": {"value": "0", "currency": "ETH"},
                    },
                ]
            }
        }
    )
    assert _adapter(http).balances() == {
        "USD": Decimal("1000.00"),
        "BTC": Decimal("0.002"),
    }


def test_symbol_rules_from_product() -> None:
    http = _product_http()
    rules = _adapter(http).symbol_rules("BTC-USD")
    assert rules.tick == Decimal("0.01")
    assert rules.lot == Decimal("0.00000001")
    assert rules.min_notional == Decimal("1.00")


def test_symbol_rules_cached_an_hour() -> None:
    http = _product_http()
    adapter = _adapter(http)
    adapter.symbol_rules("BTC-USD")
    _adapter(http).symbol_rules("BTC-USD")
    assert len(http.calls) == 1


def test_fee_summary_parses() -> None:
    http = FakeHttp(
        gets={
            "brokerage/transaction_summary": {
                "fee_tier": {
                    "maker_fee_rate": "0.001",
                    "taker_fee_rate": "0.002",
                },
                "total_volume": "123456.78",
            }
        }
    )
    assert _adapter(http).fee_summary() == (
        Decimal("0.001"),
        Decimal("0.002"),
        Decimal("123456.78"),
    )


# -- open_orders ----------------------------------------------------------


def test_venue_open_orders_parses() -> None:
    http = FakeHttp(
        gets={
            "brokerage/orders/historical/batch": {
                "orders": [
                    {"order_id": "abc", "status": "OPEN", "product_id": "BTC-USD"}
                ]
            }
        }
    )
    rows = _adapter(http).venue_open_orders()
    assert rows[0]["order_id"] == "abc"
    method, url, _, _ = http.calls[0]
    assert method == "GET"
    assert "order_status=OPEN" in url


# -- DB-backed tests -------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

_engine = None


def _shared_engine():
    global _engine
    if _engine is None:
        from alembic import command
        from alembic.config import Config

        assert TEST_URL is not None
        _engine = create_engine(TEST_URL)
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        cfg = Config(os.path.join(root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(root, "migrations"))
        with _engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
    return _engine


@pytest.fixture()
def db(monkeypatch):
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("TRADER_MODE", "live")
    monkeypatch.setenv("TRADER_LIVE_ENABLED", "true")
    get_settings.cache_clear()
    engine = _shared_engine()
    with Session(engine) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Balance,
            Event,
            Candle,
            Setting,
            EquitySnapshot,
            Heartbeat,
            AuditLog,
        ):
            s.execute(delete(table))
        s.commit()
        yield s
    get_settings.cache_clear()


def _db_order(session: Session, **kw: Any) -> Order:
    decision = Decision(
        cycle="daily", ran_at=NOW, action="none", reason="seed", details={}
    )
    session.add(decision)
    session.flush()
    plan = Plan(
        sleeve="trend",
        symbol="BTC-USD",
        side="buy",
        status="approved",
        reference_price=Decimal("60000"),
        target_weight=Decimal("0.5"),
        entry_orders=[],
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="test",
        expires_at=NOW,
        decided_at=NOW,
        decision_id=decision.id,
    )
    session.add(plan)
    session.flush()
    order = _order(
        plan_id=plan.id,
        venue="coinbase",
        venue_order_id="0000-aaaa-1111",
        status="open",
        **kw,
    )
    session.add(order)
    session.commit()
    return order


FILLS = {
    "fills": [
        {
            "entry_id": "e1",
            "trade_id": "t1",
            "order_id": "0000-aaaa-1111",
            "trade_time": "2026-11-24T10:01:00Z",
            "trade_type": "FILL",
            "price": "50000.00",
            "size": "0.002",
            "commission": "0.10",
            "product_id": "BTC-USD",
            "liquidity_indicator": "MAKER",
        }
    ]
}


BATCH_FILLED = {"orders": [{"order_id": "0000-aaaa-1111", "status": "FILLED"}]}


def test_poll_fills_emits_fills(db) -> None:
    order = _db_order(db, quantity=Decimal("0.002"))
    http = FakeHttp(
        gets={
            "brokerage/orders/historical/fills": FILLS,
            "brokerage/orders/historical/batch": BATCH_FILLED,
        }
    )
    adapter = _adapter(http, session=db)
    fills = adapter.poll_fills(NOW)
    assert len(fills) == 1
    fill = fills[0]
    assert fill.order_id == order.id
    assert fill.price == Decimal("50000.00")
    assert fill.quantity == Decimal("0.002")
    assert fill.fee == Decimal("0.10")
    assert fill.fee_currency == "USD"
    assert fill.liquidity == "maker"
    assert order.status == "filled"


def test_poll_fills_is_idempotent(db) -> None:
    _db_order(db)
    http = FakeHttp(
        gets={
            "brokerage/orders/historical/fills": FILLS,
            "brokerage/orders/historical/batch": BATCH_FILLED,
        }
    )
    adapter = _adapter(http, session=db)
    first = adapter.poll_fills(NOW)
    db.add_all(first)
    db.commit()
    assert adapter.poll_fills(NOW) == []


def test_poll_fills_new_fill_before_recorded_one(db) -> None:
    """A fill that lands ahead of a recorded one is still emitted; the
    recorded one is not emitted again (dedupe by fill identity)."""
    order = _db_order(db, quantity=Decimal("0.003"))
    db.add(
        Fill(
            order_id=order.id,
            time=datetime(2026, 11, 24, 10, 2, tzinfo=UTC),
            price=Decimal("50000.00"),
            quantity=Decimal("0.001"),
            fee=Decimal("0.05"),
            fee_currency="USD",
            liquidity="maker",
        )
    )
    db.commit()
    fills = {
        "fills": [
            # Earlier-time fill that arrived after the recorded one.
            dict(FILLS["fills"][0], trade_time="2026-11-24T10:01:00Z", size="0.001"),
            dict(FILLS["fills"][0], trade_time="2026-11-24T10:02:00Z", size="0.001"),
        ]
    }
    http = FakeHttp(
        gets={
            "brokerage/orders/historical/fills": fills,
            "brokerage/orders/historical/batch": {
                "orders": [{"order_id": "0000-aaaa-1111", "status": "OPEN"}]
            },
        }
    )
    adapter = _adapter(http, session=db)
    out = adapter.poll_fills(NOW)
    assert len(out) == 1
    assert out[0].time == datetime(2026, 11, 24, 10, 1, tzinfo=UTC)
    assert out[0].quantity == Decimal("0.001")
    assert order.status == "partially_filled"


def test_poll_fills_marks_cancelled(db) -> None:
    order = _db_order(db)
    http = FakeHttp(
        gets={
            "brokerage/orders/historical/fills": {"fills": []},
            "brokerage/orders/historical/batch": {
                "orders": [{"order_id": "0000-aaaa-1111", "status": "CANCELLED"}]
            },
        }
    )
    adapter = _adapter(http, session=db)
    assert adapter.poll_fills(NOW) == []
    assert order.status == "cancelled"


def test_poll_fills_pages_until_cursor_exhausted(db) -> None:
    order = _db_order(db, quantity=Decimal("0.004"))
    pages = iter(
        [
            {"fills": [FILLS["fills"][0]], "cursor": "page2"},
            {
                "fills": [
                    dict(
                        FILLS["fills"][0],
                        trade_time="2026-11-24T10:02:00Z",
                    )
                ]
            },
        ]
    )

    def http_call(method, url, headers, body):
        http.calls.append((method, url, headers, body))
        if "historical/fills" in url:
            return next(pages)
        if "historical/batch" in url:
            return BATCH_FILLED
        raise AssertionError(url)

    http = FakeHttp()
    adapter = CoinbaseAdvancedAdapter(
        db,
        "organizations/test-org/apiKeys/test-key-id",
        _ec_private_key_pem(),
        clock=FrozenClock(NOW),
        http=http_call,
    )
    out = adapter.poll_fills(NOW)
    assert len(out) == 2
    assert order.status == "filled"


def test_poll_fills_cancelled_with_partial_fill(db) -> None:
    """A venue-cancelled partially filled order is marked cancelled, not
    left working — and its last fills are still emitted."""
    order = _db_order(db, quantity=Decimal("0.004"))
    http = FakeHttp(
        gets={
            "brokerage/orders/historical/fills": FILLS,
            "brokerage/orders/historical/batch": {
                "orders": [{"order_id": "0000-aaaa-1111", "status": "CANCELLED"}]
            },
        }
    )
    adapter = _adapter(http, session=db)
    (fill,) = adapter.poll_fills(NOW)
    assert fill.order_id == order.id
    assert fill.quantity == Decimal("0.002")
    assert order.status == "cancelled"
