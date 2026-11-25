"""Contract tests for GET /api/subsidy (spec W1.2b). Seeded Postgres via
TEST_DATABASE_URL (Compose ``db`` service)."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.api.routes import get_session
from trader.core.config import get_settings
from trader.core.db import get_engine
from trader.ledger.models import (
    Balance,
    Candle,
    Decision,
    EquitySnapshot,
    Fill,
    Order,
    Plan,
    Setting,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

PASSWORD = "test-dashboard-password"
NOW = datetime.now(UTC)

_engine = None


def _shared_engine():
    global _engine
    if _engine is None:
        assert TEST_URL is not None
        _engine = create_engine(TEST_URL)
    return _engine


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("TRADER_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("TRADER_DASHBOARD_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    get_engine.cache_clear()
    engine = _shared_engine()
    with Session(engine) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Balance,
            Candle,
            Setting,
            EquitySnapshot,
        ):
            s.execute(delete(table))
        s.commit()
    from trader.api.app import app

    def _override() -> Iterator[Session]:
        with Session(engine) as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_engine.cache_clear()


def _login(client: TestClient) -> None:
    r = client.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 200


def test_subsidy_requires_login(client) -> None:
    assert client.get("/api/subsidy").status_code == 401


def test_subsidy_returns_snapshot(client) -> None:
    _login(client)
    r = client.get("/api/subsidy")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "disabled"
    assert body["tier"] == "Tier 1"
    assert float(body["trailing_30d_volume_usd"]) == 0
    assert body["booster_trades_30d"] == 0
    assert float(body["target_volume_usd"]) == 10000
    assert float(body["remaining_to_target_usd"]) == 10000
    assert body["progress_pct"] == 0
    assert body["adx"] is None
    assert float(body["open_orders_usd"]) == 0
    assert float(body["booster_net_pnl_30d_usd"]) == 0
    assert body["recent_fills"] == []
