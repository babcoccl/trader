"""Contract tests for POST /api/backtests/run (mock result for now)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from trader.api.schemas import BacktestResult
from trader.core.config import get_settings

PASSWORD = "test-dashboard-password"


@pytest.fixture()
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADER_DASHBOARD_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    from trader.api.app import app

    yield TestClient(app)
    get_settings.cache_clear()


def _login(client: TestClient) -> None:
    r = client.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 200


def test_run_requires_login(client: TestClient) -> None:
    assert client.post("/api/backtests/run", json={}).status_code == 401


def test_run_ok(client: TestClient) -> None:
    _login(client)
    r = client.post("/api/backtests/run", json={"starting_capital": 5000.0})
    assert r.status_code == 200
    result = BacktestResult.model_validate(r.json())
    assert result.timeseries_data
    assert result.timeseries_data[0].equity == pytest.approx(5000.0)


def test_run_defaults(client: TestClient) -> None:
    _login(client)
    r = client.post("/api/backtests/run", json={})
    assert r.status_code == 200
    result = BacktestResult.model_validate(r.json())
    assert result.timeseries_data[0].equity == pytest.approx(10000.0)


def test_run_bad_payload_422(client: TestClient) -> None:
    _login(client)
    for body in (
        {"starting_capital": "lots"},
        {"starting_capital": 0},
        {"starting_capital": -100},
        {"starting_capital": "Infinity"},
        {"start_date": "not-a-date"},
        {"start_date": "2024-12-31", "end_date": "2024-01-01"},
    ):
        r = client.post("/api/backtests/run", json=body)
        assert r.status_code == 422, body
