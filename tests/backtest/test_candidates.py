"""Candidate strategies: fee tiers, causality, fills, presets, compact output."""

from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trader.api.schemas import BacktestParams
from trader.backtest import engine, service
from trader.backtest.costs import FEE_TIERS, CostModel, tier_cost_model
from trader.backtest.sleeves import CANDIDATE_PRESETS
from trader.core.config import get_settings

PASSWORD = "test-dashboard-password"


def _params(**kw) -> BacktestParams:
    kw.setdefault("asset_universe", ["BTC-USD", "ETH-USD"])
    kw.setdefault("start_date", "2021-01-01")
    kw.setdefault("end_date", "2024-12-31")
    return BacktestParams(**kw)


def test_fee_tiers_math() -> None:
    assert FEE_TIERS["legacy_45"].maker_bps == 45.0
    assert FEE_TIERS["adv2_spot"].maker_bps == 15.0
    assert FEE_TIERS["adv2_spot"].taker_bps == 30.0
    assert FEE_TIERS["adv2_fut"].maker_bps == 5.5
    assert FEE_TIERS["adv2_fut"].taker_bps == 7.0

    maker = tier_cost_model("adv2_spot", "maker")
    assert maker.maker_fee == pytest.approx(0.0015)
    assert maker.slippage == 0.0

    taker = tier_cost_model("adv2_spot", "taker", slippage_bps=5.0)
    assert taker.taker_fee == pytest.approx(0.003)
    assert taker.slippage == pytest.approx(0.0005)

    mixed = tier_cost_model("adv2_spot", "mixed", slippage_bps=5.0)
    assert mixed.taker_fee == pytest.approx((0.0015 + 0.003) / 2)
    assert mixed.slippage == pytest.approx(0.0005)

    legacy = tier_cost_model("legacy_45", "taker", slippage_bps=0.0)
    assert legacy == CostModel(0.0045, 0.0045, 0.0)


def test_intraday_fill_trade_through() -> None:
    """Maker limit fills only when the bar trades through the limit."""
    idx = pd.date_range("2024-01-01", periods=200, freq="h")
    close = pd.Series(100.0, index=idx)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
        }
    )
    # ATR ~0.05: entry limit = prev_close - 2.5*ATR ≈ -0.13%: bar low -0.1% no fill.
    cost = CostModel(0.0, 0.0, 0.0)
    res = engine.run_intraday_flush_mr(
        {"AAA-USD": frame},
        cost,
        atr_period=14,
        entry_atr_mult=2.5,
        target_atr_mult=1.0,
        max_hold_bars=24,
        crash_veto_24h=-0.12,
        max_weight=0.25,
        trade_through_bps=5.0,
        bars_per_day=24,
    )
    assert not res.trades, "low 0.1% below close must not fill a 0.13% limit"

    # Deepen the low so the limit trades through.
    frame2 = frame.copy()
    frame2.loc[frame2.index[100], "low"] *= 0.99
    res2 = engine.run_intraday_flush_mr(
        {"AAA-USD": frame2},
        cost,
        atr_period=14,
        entry_atr_mult=2.5,
        target_atr_mult=1.0,
        max_hold_bars=24,
        crash_veto_24h=-0.12,
        max_weight=0.25,
        trade_through_bps=5.0,
        bars_per_day=24,
    )
    assert res2.trades
    assert res2.trades[0]["side"] == "BUY"
    assert res2.trades[0]["date"] >= frame2.index[100]


def test_intraday_causality() -> None:
    """Mutating bar t must not change signals taken before t."""
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=400, freq="h")
    rets = rng.normal(0, 0.003, 400)
    close = pd.Series(100 * np.cumprod(1 + rets), index=idx)
    frame = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
        }
    )
    cost = CostModel(0.0005, 0.001, 0.0005)
    kw = dict(
        atr_period=14,
        entry_atr_mult=2.5,
        target_atr_mult=1.0,
        max_hold_bars=24,
        crash_veto_24h=-0.12,
        max_weight=0.25,
        trade_through_bps=5.0,
        bars_per_day=24,
    )
    base = engine.run_intraday_flush_mr({"AAA-USD": frame}, cost, **kw)

    frame2 = frame.copy()
    frame2.iloc[300:] *= 1.4
    shocked = engine.run_intraday_flush_mr({"AAA-USD": frame2}, cost, **kw)

    cut = idx[299]
    np.testing.assert_array_equal(
        shocked.weights.loc[:cut].values, base.weights.loc[:cut].values
    )
    np.testing.assert_allclose(
        shocked.equity.loc[:cut].values, base.equity.loc[:cut].values
    )


@pytest.mark.parametrize("sleeve", sorted(CANDIDATE_PRESETS))
def test_each_candidate_preset_runs(sleeve: str) -> None:
    result = service.execute_backtest(_params(strategy_sleeve=sleeve, params={}))
    assert result.compact_summary is not None
    if sleeve == "intraday_flush_mr" and service.load_intraday() is None:
        assert result.compact_summary["data_status"] == "missing"
        return
    assert result.compact_summary["data_status"] == "ok"
    assert result.compact_report
    full = result.compact_summary["splits"]["full"]
    for metric in ("cagr", "sharpe", "max_dd", "calmar", "fee_drag_yr"):
        assert metric in full
    assert set(result.compact_summary["gates"]) == {
        "G1_OOS_SHARPE",
        "G2_NON_UPTREND",
        "G3_DD_LIMIT",
        "G4_ADV1_SURVIVAL",
        "G5_CADENCE",
    }


def test_candidate_missing_data_graceful(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "load_funding", lambda: None)
    result = service.execute_backtest(
        _params(strategy_sleeve="funding_downtrend", params={})
    )
    assert result.compact_summary["data_status"] == "missing"
    assert result.summary_metrics.total_trades == 0
    assert "missing" in result.compact_report


def test_combine_trend_reports_combined() -> None:
    result = service.execute_backtest(
        _params(
            strategy_sleeve="regime_composite",
            params={"mixed_mode": "dip"},
            combine_trend=True,
        ),
    )
    assert result.compact_summary["combined"] is not None
    comb = result.compact_summary["combined"]
    for k in ("cagr", "max_dd", "calmar", "avg_30d_vol", "est_subsidy_saved_yr"):
        assert k in comb


def test_include_series_false() -> None:
    result = service.execute_backtest(
        _params(
            strategy_sleeve="funding_downtrend",
            params={},
            include_series=False,
        ),
    )
    assert result.timeseries_data == []
    assert result.trade_log == []
    # Metrics and compact summary still populated.
    assert result.summary_metrics.total_trades >= 0
    assert result.compact_summary["data_status"] in ("ok", "missing")


@pytest.fixture()
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADER_DASHBOARD_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    from trader.api.app import app

    yield TestClient(app)
    get_settings.cache_clear()


def _login(client: TestClient) -> None:
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_presets_endpoint(client: TestClient) -> None:
    _login(client)
    r = client.get("/api/backtests/presets")
    assert r.status_code == 200
    body = r.json()
    assert "adv2_spot" in body["fee_tiers"]
    for sleeve in CANDIDATE_PRESETS:
        assert sleeve in body["candidate_presets"]
        assert "defaults" in body["candidate_presets"][sleeve]
    assert "daily" in body["data_availability"]


def test_sweep_endpoint(client: TestClient) -> None:
    _login(client)
    r = client.post(
        "/api/backtests/sweep",
        json={
            "strategies": ["funding_downtrend"],
            "fee_tiers": ["adv1_spot"],
            "base": {
                "start_date": "2022-01-01",
                "end_date": "2023-12-31",
                "include_series": False,
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["strategy"] == "funding_downtrend"
    assert body["rows"][0]["fee_tier"] == "adv1_spot"
    assert body["combined_markdown"]
    row = body["rows"][0]
    assert row["data_status"] in ("ok", "missing")
    assert "splits" in row or row["data_status"] == "missing"
