"""Backtest must reproduce the study's committed expected values."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trader.backtest import engine
from trader.backtest.costs import STUDY_040, CostModel

FIXTURES = Path(__file__).resolve().parents[2] / "research" / "fixtures"
OPENS, CLOSES = engine.load_fixture(str(FIXTURES / "btc_eth_daily.csv"))
EXPECTED = json.loads((FIXTURES / "expected_backtest.json").read_text())["runs"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_run_matches_expected(name: str) -> None:
    exp = EXPECTED[name]
    cost = CostModel(
        maker_fee=exp["taker_fee"],
        taker_fee=exp["taker_fee"],
        slippage=exp["slippage"],
    )
    result = engine.run(OPENS, CLOSES, cost, band=exp["band"])

    full = result.stats()
    for metric in ("cagr", "maxdd", "sharpe", "turnover_yr", "exposure"):
        assert full[metric] == pytest.approx(exp["full"][metric], abs=1e-6), metric

    recent = result.stats(start="2022-01-01")
    for metric in ("cagr", "maxdd", "sharpe"):
        assert recent[metric] == pytest.approx(exp["from_2022"][metric], abs=1e-6), (
            metric
        )

    assert result.equity.iloc[-1] == pytest.approx(exp["final_equity"], rel=1e-6)


def test_equity_series_study_040_band_02() -> None:
    expected = pd.read_csv(
        FIXTURES / "expected_equity_study_040_band_0.2.csv", parse_dates=["date"]
    ).set_index("date")["equity"]
    result = engine.run(OPENS, CLOSES, STUDY_040, band=0.20)
    pd.testing.assert_index_equal(result.equity.index, expected.index)
    np.testing.assert_allclose(result.equity.values, expected.values, atol=1e-6, rtol=0)


def test_no_look_ahead() -> None:
    """Prices changed after day t must not change results at or before t."""
    cut = pd.Timestamp("2023-06-15")
    base = engine.run(OPENS, CLOSES, STUDY_040, band=0.20)

    opens2 = OPENS.copy()
    closes2 = CLOSES.copy()
    later = closes2.index > cut
    opens2.loc[later] = opens2.loc[later] * 1.5
    closes2.loc[later] = closes2.loc[later] * 1.5
    shocked = engine.run(opens2, closes2, STUDY_040, band=0.20)

    np.testing.assert_array_equal(
        shocked.weights.loc[:cut].values, base.weights.loc[:cut].values
    )
    np.testing.assert_array_equal(
        shocked.equity.loc[:cut].values, base.equity.loc[:cut].values
    )


def test_zero_cost_full_weight_is_buy_and_hold() -> None:
    """Constant gentle uptrend: signal 1, vol ~0, weight 1 -> buy and hold."""
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.Series(100.0 * 1.001 ** np.arange(n), index=idx)
    opens = pd.DataFrame({"AAA-USD": close})
    closes = pd.DataFrame({"AAA-USD": close})

    result = engine.run(
        opens,
        closes,
        CostModel(0.0, 0.0, 0.0),
        band=0.0,
        start=idx[0].strftime("%Y-%m-%d"),
        end=idx[-1].strftime("%Y-%m-%d"),
    )
    # Once every rule is defined (day 200) the weight is 1: fully invested,
    # zero cost, so daily equity returns equal the asset's.
    asset_ret = close.pct_change().iloc[201:]
    equity_ret = result.equity.pct_change().iloc[201:]
    np.testing.assert_allclose(equity_ret.values, asset_ret.values, rtol=1e-12)
    assert (result.weights.iloc[201:] == 1.0).all().all()
