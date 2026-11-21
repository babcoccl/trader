"""Bitstamp live adapter tests (spec 5.2).

Transport is a recorded-fixture fake; nothing here talks to Bitstamp.
DB tests need TEST_DATABASE_URL (Compose Postgres).
"""

import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.core.config import get_settings
from trader.execution.__main__ import Executor
from trader.execution.bitstamp import (
    BitstampAdapter,
    BitstampError,
    LiveTradingDisabledError,
    sign,
)
from trader.ledger import accounting
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

FIXTURES = Path(__file__).parent / "fixtures"
TEST_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    """Routes adapter GET/POST calls to recorded fixtures by URL/path."""

    def __init__(
        self,
        posts: dict[str, Any] | None = None,
        queue: dict[str, list[Any]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.gets: list[str] = []
        self.posts = posts or {}
        self.queue = queue or {}

    def post(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls.append((url, params, headers))
        for path, payloads in self.queue.items():
            if url.endswith(path) and payloads:
                return payloads.pop(0)
        for path, payload in self.posts.items():
            if url.endswith(path):
                return payload
        raise AssertionError(f"no fixture for POST {url}")

    def get(self, url: str) -> Any:
        self.gets.append(url)
        if url.endswith("/markets/"):
            return _fixture("markets.json")
        if url.endswith("/ticker/btcusd/"):
            return _fixture("ticker_btcusd.json")
        raise AssertionError(f"no fixture for GET {url}")


class _EmptyResult:
    def first(self) -> None:
        return None


class _SessionStub:
    def __init__(self) -> None:
        self.flushed = 0

    def flush(self) -> None:
        self.flushed += 1

    def scalars(self, *_args: Any, **_kw: Any) -> _EmptyResult:
        return _EmptyResult()


def _adapter(
    transport: FakeTransport, session: Any = None, mode: str = "live"
) -> BitstampAdapter:
    return BitstampAdapter(
        session or _SessionStub(),
        "key",
        "secret",
        clock=FrozenClock(NOW),
        mode=mode,
        post=transport.post,
        get=transport.get,
        nonce_fn=lambda: "00000000-0000-0000-0000-000000000001",
    )


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


@pytest.fixture()
def live_env(monkeypatch):
    monkeypatch.setenv("TRADER_MODE", "live")
    monkeypatch.setenv("TRADER_LIVE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# -- signing -------------------------------------------------------------


def test_sign_matches_reference_example() -> None:
    """The documented Python example: 'BITSTAMP '+key+verb+host+path+query+
    content-type+nonce+timestamp+'v2'+body, HMAC-SHA256 hex."""
    message = (
        "BITSTAMP api_key"
        "POST"
        "www.bitstamp.net"
        "/api/v2/user_transactions/"
        ""  # empty query
        "application/x-www-form-urlencoded"
        "nonce123"
        "1700000000000"
        "v2"
        "offset=1"
    )
    expected = hmac.new(b"api_key_secret", message.encode(), hashlib.sha256).hexdigest()
    got = sign(
        "api_key",
        "api_key_secret",
        "POST",
        "www.bitstamp.net",
        "/api/v2/user_transactions/",
        "",
        "application/x-www-form-urlencoded",
        "nonce123",
        "1700000000000",
        "offset=1",
    )
    assert got == expected


def test_request_sends_signed_headers() -> None:
    t = FakeTransport(
        posts={"/api/v2/account_balances/": _fixture("account_balances.json")}
    )
    adapter = _adapter(t)
    adapter.balances()
    url, params, headers = t.calls[0]
    assert url.endswith("/api/v2/account_balances/")
    assert headers["X-Auth"] == "BITSTAMP key"
    assert headers["X-Auth-Nonce"] == "00000000-0000-0000-0000-000000000001"
    assert headers["X-Auth-Timestamp"] == str(int(NOW.timestamp() * 1000))
    assert headers["X-Auth-Version"] == "v2"
    # Body is empty (no params): Content-Type is omitted per the docs
    # ("when body is empty, Content-Type should be omitted ... otherwise it
    # might cause issues"). The signature still uses the documented formula.
    assert "Content-Type" not in headers
    assert headers["X-Auth-Signature"]
    assert headers["X-Auth-Signature"] == sign(
        "key",
        "secret",
        "POST",
        "www.bitstamp.net",
        "/api/v2/account_balances/",
        "",
        "",
        "00000000-0000-0000-0000-000000000001",
        str(int(NOW.timestamp() * 1000)),
        "",
    )


def test_request_sets_content_type_when_body_present() -> None:
    t = FakeTransport(posts={"/api/v2/cancel_order/": {"id": "1"}})
    _adapter(t).cancel("1")
    headers = t.calls[0][2]
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


# -- symbol rules ---------------------------------------------------------


def test_symbol_rules_from_markets() -> None:
    adapter = _adapter(FakeTransport())
    rules = adapter.symbol_rules("BTC-USD")
    assert rules.tick == Decimal("0.01")
    assert rules.lot == Decimal("0.00000001")
    assert rules.min_notional == Decimal("10.0")


def test_trading_fee_parses() -> None:
    t = FakeTransport(
        posts={"/api/v2/fees/trading/btcusd": _fixture("trading_fee.json")}
    )
    fee = _adapter(t).trading_fee("BTC-USD")
    assert fee == {"maker": Decimal("0.30000"), "taker": Decimal("0.40000")}


# -- place ----------------------------------------------------------------


def test_place_refuses_in_paper_mode() -> None:
    adapter = _adapter(FakeTransport())
    with pytest.raises(LiveTradingDisabledError):
        adapter.place(_order())


def test_place_refuses_stop_order(live_env) -> None:
    adapter = _adapter(FakeTransport())
    with pytest.raises(ValueError, match="stop"):
        adapter.place(_order(type="stop", stop_price=Decimal("40000")))


def test_place_refuses_below_minimum(live_env) -> None:
    adapter = _adapter(FakeTransport())
    with pytest.raises(ValueError, match="minimum"):
        adapter.place(_order(price=Decimal("49000"), quantity=Decimal("0.0001")))


def test_place_limit_posts_moc_and_rounds(live_env) -> None:
    t = FakeTransport(posts={"/api/v2/buy/btcusd/": _fixture("order_placed.json")})
    adapter = _adapter(t)
    order = _order(price=Decimal("49000.129"), quantity=Decimal("0.002000009"))
    venue_id = adapter.place(order)
    url, params, _ = t.calls[0]
    assert url.endswith("/api/v2/buy/btcusd/")
    assert params["price"] == "49000.12"  # rounded down to the $0.01 tick
    assert params["amount"] == "0.00200000"  # rounded down to 8 decimals
    assert params["moc_order"] == "true"
    assert params["client_order_id"] == "1-1"
    assert venue_id == "1458532827766784"
    assert order.venue_order_id == venue_id
    assert order.status == "open"


def test_place_market_order(live_env) -> None:
    t = FakeTransport(
        posts={"/api/v2/sell/market/btcusd/": _fixture("order_placed.json")}
    )
    adapter = _adapter(t)
    order = _order(side="sell", type="market", price=None, quantity=Decimal("0.002"))
    adapter.place(order)
    url, params, _ = t.calls[0]
    assert url.endswith("/api/v2/sell/market/btcusd/")
    assert "price" not in params
    assert "moc_order" not in params


def test_moc_rejection_retries_at_new_best_bid(live_env) -> None:
    t = FakeTransport(
        queue={
            "/api/v2/buy/btcusd/": [
                _fixture("order_canceled_moc.json"),
                _fixture("order_placed.json"),
            ]
        }
    )
    adapter = _adapter(t)
    venue_id = adapter.place(_order(price=Decimal("49000")))
    assert len(t.calls) == 2
    # Re-priced at the ticker's best bid ($50,000.00), not the old price.
    assert t.calls[1][1]["price"] == "50000.00"
    assert venue_id == "1458532827766784"


def test_moc_rejection_gives_up_after_retries(live_env) -> None:
    canceled = _fixture("order_canceled_moc.json")
    t = FakeTransport(queue={"/api/v2/buy/btcusd/": [canceled, canceled, canceled]})
    with pytest.raises(BitstampError, match="maker-or-cancel"):
        _adapter(t).place(_order(price=Decimal("49000")))
    assert len(t.calls) == 3


def test_error_response_raises(live_env) -> None:
    t = FakeTransport(
        posts={
            "/api/v2/buy/btcusd/": {
                "status": "error",
                "reason": {"__all__": ["Insufficient balance"]},
            }
        }
    )
    with pytest.raises(BitstampError, match="Insufficient balance"):
        _adapter(t).place(_order(price=Decimal("49000")))


# -- cancel / open orders / balances ---------------------------------------


def test_cancel_posts_id() -> None:
    t = FakeTransport(posts={"/api/v2/cancel_order/": {"id": "1458532827766784"}})
    assert _adapter(t).cancel("1458532827766784") is True
    assert t.calls[0][1] == {"id": "1458532827766784"}


def test_cancel_not_found_returns_false() -> None:
    t = FakeTransport(
        posts={
            "/api/v2/cancel_order/": {
                "status": "error",
                "reason": {"__all__": ["Order not found"]},
            }
        }
    )
    assert _adapter(t).cancel("999") is False


def test_balances_parses_fixture() -> None:
    t = FakeTransport(
        posts={"/api/v2/account_balances/": _fixture("account_balances.json")}
    )
    assert _adapter(t).balances() == {
        "USD": Decimal("1000.00"),
        "BTC": Decimal("0.002"),
    }


def test_venue_open_orders_hits_both_markets() -> None:
    t = FakeTransport(
        posts={
            "/api/v2/open_orders/btcusd/": _fixture("open_orders.json"),
            "/api/v2/open_orders/ethusd/": [],
        }
    )
    rows = _adapter(t).venue_open_orders()
    assert [r["id"] for r in rows] == ["1458532827766784"]
    assert len(t.calls) == 2


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
        venue="bitstamp",
        venue_order_id="1458532827766784",
        status="open",
        **kw,
    )
    session.add(order)
    session.commit()
    return order


def test_poll_fills_emits_transactions(db) -> None:
    order = _db_order(db)
    t = FakeTransport(
        posts={"/api/v2/order_status/": _fixture("order_status_finished.json")}
    )
    adapter = _adapter(t, session=db)
    fills = adapter.poll_fills(NOW)
    assert len(fills) == 1
    fill = fills[0]
    assert fill.order_id == order.id
    assert fill.price == Decimal("50000.00")
    assert fill.quantity == Decimal("0.002")
    assert fill.fee == Decimal("0.30")
    assert fill.fee_currency == "USD"
    assert fill.liquidity == "maker"
    assert fill.time == datetime(2026, 11, 21, 10, 1, tzinfo=UTC)
    assert order.status == "filled"


def test_poll_fills_is_idempotent(db) -> None:
    _db_order(db)
    t = FakeTransport(
        posts={"/api/v2/order_status/": _fixture("order_status_finished.json")}
    )
    adapter = _adapter(t, session=db)
    first = adapter.poll_fills(NOW)
    db.add_all(first)
    db.commit()
    assert adapter.poll_fills(NOW) == []


def test_poll_fills_partial(db) -> None:
    order = _db_order(db)
    t = FakeTransport(
        posts={"/api/v2/order_status/": _fixture("order_status_partial.json")}
    )
    fills = _adapter(t, session=db).poll_fills(NOW)
    assert len(fills) == 1
    assert order.status == "partially_filled"


def test_cancel_collects_pending_fills(db) -> None:
    order = _db_order(db)
    t = FakeTransport(
        posts={
            "/api/v2/cancel_order/": {"id": "1458532827766784"},
            "/api/v2/order_status/": _fixture("order_status_partial.json"),
        }
    )
    adapter = _adapter(t, session=db)
    assert adapter.cancel("1458532827766784") is True
    assert order.status == "cancelled"
    # The cancelled order left the open set; its fill comes out of the
    # next poll_fills exactly once.
    (fill,) = adapter.poll_fills(NOW)
    assert fill.order_id == order.id
    assert fill.quantity == Decimal("0.001")
    assert adapter.poll_fills(NOW) == []


# -- executor wiring (live mode) -------------------------------------------


def _live_executor(db: Session, t: FakeTransport) -> Executor:
    return Executor(
        clock=FrozenClock(NOW),
        session_factory=lambda: _cm(db),
        live_adapter=lambda session, mode: _adapter(t, session=session, mode=mode),
    )


@contextmanager
def _cm(session: Session):
    yield session
    session.commit()


def _approved_buy_plan(db: Session) -> Plan:
    decision = Decision(
        cycle="daily", ran_at=NOW, action="none", reason="seed", details={}
    )
    db.add(decision)
    db.flush()
    plan = Plan(
        sleeve="trend",
        symbol="BTC-USD",
        side="buy",
        status="approved",
        reference_price=Decimal("49000"),
        target_weight=Decimal("0.5"),
        entry_orders=[
            {"side": "buy", "type": "limit", "price": "49000", "quantity": "0.002"}
        ],
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="test",
        expires_at=NOW,
        decided_at=NOW,
        decision_id=decision.id,
    )
    db.add(plan)
    db.flush()
    return plan


def test_executor_live_places_approved_plan_on_bitstamp(db) -> None:
    plan = _approved_buy_plan(db)
    t = FakeTransport(
        posts={
            "/api/v2/buy/btcusd/": _fixture("order_placed.json"),
            "/api/v2/order_status/": _fixture("order_status_open.json"),
        }
    )
    _live_executor(db, t).tick()
    (order,) = db.scalars(select(Order).where(Order.plan_id == plan.id)).all()
    assert order.venue == "bitstamp"
    assert order.venue_order_id == "1458532827766784"
    assert order.status == "open"
    url, params, _ = t.calls[0]
    assert url.endswith("/api/v2/buy/btcusd/")
    assert params["moc_order"] == "true"


def test_executor_live_refuses_without_live_enabled(db, monkeypatch) -> None:
    monkeypatch.setenv("TRADER_LIVE_ENABLED", "false")
    get_settings.cache_clear()
    _approved_buy_plan(db)
    t = FakeTransport()
    with pytest.raises(LiveTradingDisabledError):
        _live_executor(db, t).tick()
    assert t.calls == []  # nothing reached the venue


def test_executor_live_emergency_stop_sells_via_venue(db) -> None:
    decision = Decision(
        cycle="daily", ran_at=NOW, action="none", reason="seed", details={}
    )
    db.add(decision)
    db.flush()
    plan = Plan(
        sleeve="trend",
        symbol="BTC-USD",
        side="buy",
        status="completed",
        reference_price=Decimal("60000"),
        target_weight=Decimal("0.5"),
        entry_orders=[],
        stop_price=Decimal("50000"),
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="test",
        expires_at=NOW,
        decided_at=NOW,
        decision_id=decision.id,
    )
    db.add(plan)
    db.flush()
    db.add(
        Order(
            plan_id=plan.id,
            venue="bitstamp",
            client_order_id=f"{plan.id}-0",
            venue_order_id="1",
            symbol="BTC-USD",
            side="buy",
            type="limit",
            quantity=Decimal("0.002"),
            status="filled",
            mode="live",
        )
    )
    db.add(Balance(time=NOW, mode="live", asset="BTC", quantity=Decimal("0.002")))
    db.add(Balance(time=NOW, mode="live", asset="USD", quantity=Decimal("0")))
    db.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=NOW - timedelta(minutes=10),
            open=Decimal("49500"),
            high=Decimal("49500"),
            low=Decimal("48000"),
            close=Decimal("49000"),
            volume=Decimal("1"),
            source="bitstamp",
        )
    )
    db.flush()
    finished_sell = {
        "id": 77,
        "status": "Finished",
        "amount_remaining": "0",
        "transactions": [
            {
                "tid": 1,
                "price": "49000.00",
                "usd": "98.00",
                "btc": "0.00200000",
                "fee": "0.40",
                "datetime": "2026-11-21 11:59:00",
                "type": 2,
            }
        ],
    }
    t = FakeTransport(
        posts={
            "/api/v2/sell/market/btcusd/": {"id": 77},
            "/api/v2/order_status/": finished_sell,
        }
    )
    _live_executor(db, t).tick()
    assert t.calls[0][0].endswith("/api/v2/sell/market/btcusd/")
    assert t.calls[0][1]["amount"] == "0.00200000"
    sell = db.scalars(select(Order).where(Order.side == "sell")).one()
    assert sell.status == "filled"
    (fill,) = db.scalars(select(Fill).where(Fill.order_id == sell.id)).all()
    assert fill.liquidity == "taker"
    assert accounting.positions(db, "live") == {}
    assert db.scalars(select(Event).where(Event.kind == "emergency_stop")).one()
