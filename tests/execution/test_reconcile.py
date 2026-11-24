"""reconcile.run tests (spec 5.2): ledger vs venue balances and open orders."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.core.config import get_settings
from trader.execution.reconcile import TOLERANCE_USD, run
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

TEST_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)


class FakeAdapter:
    """Minimal ExecutionAdapter stub returning canned venue state."""

    def __init__(self, balances: dict[str, Decimal], open_orders: list[dict]) -> None:
        self._balances = balances
        self._open_orders = open_orders

    def balances(self) -> dict[str, Decimal]:
        return self._balances

    def venue_open_orders(self) -> list[dict]:
        return self._open_orders

    # Unused ExecutionAdapter members.
    def place(self, order):  # pragma: no cover
        raise NotImplementedError

    def cancel(self, venue_order_id):  # pragma: no cover
        raise NotImplementedError

    def open_orders(self):  # pragma: no cover
        return []

    def poll_fills(self, since):  # pragma: no cover
        return []

    def symbol_rules(self, symbol):  # pragma: no cover
        raise NotImplementedError


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


def _clock() -> FrozenClock:
    return FrozenClock(NOW)


def _order(session: Session, **kw: Any) -> Order:
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
    defaults = {"venue": "bitstamp", "venue_order_id": "1458532827766784"}
    defaults.update(kw)
    order = Order(
        plan_id=plan.id,
        client_order_id="1-1",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("49000"),
        quantity=Decimal("0.002"),
        status="open",
        mode="live",
        **defaults,
    )
    session.add(order)
    session.commit()
    return order


def test_no_alarms_when_ledger_matches(db) -> None:
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    adapter = FakeAdapter(
        {"USD": Decimal("1000")},
        [{"id": "1458532827766784"}],
    )
    _order(db)
    assert run(db, _clock(), adapter=adapter, mode="live") == []


def test_balance_difference_over_tolerance_alarms(db) -> None:
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    adapter = FakeAdapter({"USD": Decimal("999.97")}, [])
    alarms = run(db, _clock(), adapter=adapter, mode="live")
    assert [a.key for a in alarms] == ["ledger_vs_venue"]
    assert "USD" in alarms[0].message


def test_difference_at_tolerance_does_not_alarm(db) -> None:
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    exact = Decimal("1000") - TOLERANCE_USD
    adapter = FakeAdapter({"USD": exact}, [])
    assert run(db, _clock(), adapter=adapter, mode="live") == []


def test_missing_venue_order_alarms(db) -> None:
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    _order(db)
    adapter = FakeAdapter({"USD": Decimal("1000")}, [])
    alarms = run(db, _clock(), adapter=adapter, mode="live")
    assert [a.key for a in alarms] == ["open_orders_vs_venue"]


def test_unknown_venue_order_alarms(db) -> None:
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    adapter = FakeAdapter(
        {"USD": Decimal("1000")},
        [{"id": "999"}],
    )
    alarms = run(db, _clock(), adapter=adapter, mode="live")
    assert [a.key for a in alarms] == ["open_orders_vs_venue"]


def test_crypto_difference_without_price_alarms(db) -> None:
    # No candles stored: a crypto diff can't be valued, so any nonzero
    # difference must alarm rather than silently compare raw quantity.
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    adapter = FakeAdapter({"USD": Decimal("1000"), "BTC": Decimal("0.00000001")}, [])
    alarms = run(db, _clock(), adapter=adapter, mode="live")
    assert [a.key for a in alarms] == ["ledger_vs_venue"]


def test_coinbase_venue_uses_coinbase_adapter(db, monkeypatch) -> None:
    """TRADER_VENUE=coinbase routes the default adapter to Coinbase."""
    from trader.execution import reconcile

    monkeypatch.setenv("TRADER_VENUE", "coinbase")
    get_settings.cache_clear()
    calls: list[str] = []
    fake = FakeAdapter({}, [])

    def coinbase_from_env(*args: Any, **kwargs: Any) -> FakeAdapter:
        calls.append("coinbase")
        return fake

    def bitstamp_from_env(*args: Any, **kwargs: Any) -> FakeAdapter:
        calls.append("bitstamp")
        return fake

    monkeypatch.setattr(
        reconcile.CoinbaseAdvancedAdapter, "from_env", coinbase_from_env
    )
    monkeypatch.setattr(reconcile.BitstampAdapter, "from_env", bitstamp_from_env)
    assert run(db, _clock(), mode="live") == []
    assert calls == ["coinbase"]


def test_bitstamp_venue_uses_bitstamp_adapter(db, monkeypatch) -> None:
    """Default/bitstamp venue still routes to BitstampAdapter."""
    from trader.execution import reconcile

    monkeypatch.delenv("TRADER_VENUE", raising=False)
    get_settings.cache_clear()
    calls: list[str] = []
    fake = FakeAdapter({}, [])
    monkeypatch.setattr(
        reconcile.CoinbaseAdvancedAdapter,
        "from_env",
        lambda *args, **kwargs: calls.append("coinbase") or fake,
    )
    monkeypatch.setattr(
        reconcile.BitstampAdapter,
        "from_env",
        lambda *args, **kwargs: calls.append("bitstamp") or fake,
    )
    assert run(db, _clock(), mode="live") == []
    assert calls == ["bitstamp"]


def test_coinbase_from_env_failure_is_venue_unreachable(db, monkeypatch) -> None:
    """A CoinbaseAdvancedAdapter.from_env failure alarms venue_unreachable."""
    from trader.execution import reconcile

    monkeypatch.setenv("TRADER_VENUE", "coinbase")
    get_settings.cache_clear()

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("missing TRADER_COINBASE_KEY")

    monkeypatch.setattr(reconcile.CoinbaseAdvancedAdapter, "from_env", broken)
    alarms = run(db, _clock(), mode="live")
    assert [a.key for a in alarms] == ["venue_unreachable"]
    assert "missing TRADER_COINBASE_KEY" in alarms[0].message


def test_coinbase_orders_compared_by_venue_and_order_id(db) -> None:
    """Coinbase adapter orders match on order_id, not id."""
    accounting.deposit(db, "live", Decimal("1000"), FrozenClock(NOW))
    _order(db, venue="coinbase", venue_order_id="cb-123")
    adapter = FakeAdapter({"USD": Decimal("1000")}, [{"order_id": "cb-123"}])
    adapter.venue = "coinbase"
    assert run(db, _clock(), adapter=adapter, mode="live") == []


def test_venue_unreachable_alarms(db) -> None:
    class DeadAdapter(FakeAdapter):
        def __init__(self) -> None:
            pass

        def balances(self):
            raise RuntimeError("connection refused")

    alarms = run(db, _clock(), adapter=DeadAdapter(), mode="live")
    assert [a.key for a in alarms] == ["venue_unreachable"]
