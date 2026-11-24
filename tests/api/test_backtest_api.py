"""Contract tests for POST /api/backtests/run (real engine)."""

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
    # First point is marked at day-1 close: near, but not exactly, capital
    assert 3500.0 < float(result.timeseries_data[0].equity) < 7000.0


def test_run_defaults(client: TestClient) -> None:
    _login(client)
    r = client.post("/api/backtests/run", json={})
    assert r.status_code == 200
    result = BacktestResult.model_validate(r.json())
    assert 7000.0 < float(result.timeseries_data[0].equity) < 14000.0


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


def test_run_real_engine_btc_eth_2024(client: TestClient) -> None:
    _login(client)
    r = client.post(
        "/api/backtests/run",
        json={
            "asset_universe": ["BTC-USD", "ETH-USD"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "starting_capital": 10000.0,
        },
    )
    assert r.status_code == 200
    result = BacktestResult.model_validate(r.json())
    # ~365 daily points, all schema-validated
    assert len(result.timeseries_data) > 300
    m = result.summary_metrics
    for value in (m.cagr, m.benchmark_cagr, m.max_drawdown, m.win_rate):
        assert value == pytest.approx(value)  # finite numbers
    assert -1.0 < m.cagr < 10.0
    assert -1.0 < m.benchmark_cagr < 10.0
    assert 0.0 <= m.max_drawdown <= 1.0
    assert 0.0 <= m.win_rate <= 1.0
    assert m.total_fees_paid >= 0
    assert m.total_trades >= 0
    assert all(t.asset in ("BTC-USD", "ETH-USD") for t in result.trade_log)


def test_run_fee_tier_changes_total_fees(client: TestClient) -> None:
    _login(client)
    fees = {}
    for tier in ("coinbase_tier_1", "coinbase_tier_3"):
        r = client.post(
            "/api/backtests/run",
            json={
                "asset_universe": ["BTC-USD", "ETH-USD"],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "assumed_fee_tier": tier,
            },
        )
        assert r.status_code == 200, tier
        fees[tier] = float(
            BacktestResult.model_validate(r.json()).summary_metrics.total_fees_paid
        )
    # 40bps vs 15bps per side (plus shared 1bp slippage): ~2.56x, same trades
    assert fees["coinbase_tier_1"] > fees["coinbase_tier_3"] * 2
    assert fees["coinbase_tier_1"] < fees["coinbase_tier_3"] * 3


def test_run_unavailable_asset_400(client: TestClient) -> None:
    _login(client)
    r = client.post(
        "/api/backtests/run",
        json={"asset_universe": ["DOGE-USD", "SOL-USD"]},
    )
    assert r.status_code == 400
    assert "assets" in r.json()["detail"]


def test_run_partial_universe_graceful(client: TestClient) -> None:
    _login(client)
    r = client.post(
        "/api/backtests/run",
        json={"asset_universe": ["BTC-USD", "DOGE-USD"]},
    )
    assert r.status_code == 200
    result = BacktestResult.model_validate(r.json())
    assert result.timeseries_data


def test_run_duplicate_assets_ok(client: TestClient) -> None:
    _login(client)
    dup = client.post(
        "/api/backtests/run",
        json={"asset_universe": ["BTC-USD", "BTC-USD", "ETH-USD"]},
    )
    assert dup.status_code == 200
    deduped = client.post(
        "/api/backtests/run",
        json={"asset_universe": ["BTC-USD", "ETH-USD"]},
    )
    assert deduped.status_code == 200
    # Dedupe makes the requests identical end to end
    assert dup.json() == deduped.json()


def test_run_nonpositive_vol_target_400(client: TestClient) -> None:
    _login(client)
    for vt in (0, -0.4):
        r = client.post("/api/backtests/run", json={"vol_target": vt})
        assert r.status_code == 400, vt


def test_run_unknown_fee_tier_400(client: TestClient) -> None:
    _login(client)
    r = client.post(
        "/api/backtests/run",
        json={"assumed_fee_tier": "free_money"},
    )
    assert r.status_code == 400
