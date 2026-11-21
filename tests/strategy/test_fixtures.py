"""Numerical identity with the study: every rule, ensemble, vol and weight
must match research/fixtures/expected_signals.csv within 1e-9."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trader.strategy import ensemble, rules, sizing

FIXTURES = Path(__file__).resolve().parents[2] / "research" / "fixtures"
SYMBOLS = ["BTC-USD", "ETH-USD"]
RULE_NAMES = [
    "sma50",
    "sma100",
    "sma200",
    "momentum30",
    "momentum90",
    "momentum180",
    "breakout20",
    "breakout55",
    "breakout100",
]

DAILY = pd.read_csv(FIXTURES / "btc_eth_daily.csv", parse_dates=["date"]).set_index(
    "date"
)
EXPECTED = pd.read_csv(FIXTURES / "expected_signals.csv", parse_dates=["date"])


def _expected(symbol: str) -> pd.DataFrame:
    return EXPECTED[EXPECTED.symbol == symbol].set_index("date")


def _assert_series_equal_within(actual: pd.Series, expected: pd.Series) -> None:
    a = actual.values.astype(float)
    b = expected.values.astype(float)
    assert (np.isnan(a) == np.isnan(b)).all(), "NaN pattern differs"
    np.testing.assert_allclose(a, b, atol=1e-9, rtol=0)


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("rule_name", RULE_NAMES)
def test_rule_matches_expected(symbol: str, rule_name: str) -> None:
    close = DAILY[f"{symbol}_close"]
    actual = rules.all_rules(close)[rule_name]
    _assert_series_equal_within(actual, _expected(symbol)[rule_name])


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_ensemble_matches_expected(symbol: str) -> None:
    close = DAILY[f"{symbol}_close"]
    _assert_series_equal_within(ensemble.signal(close), _expected(symbol)["ensemble"])


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_annual_vol_matches_expected(symbol: str) -> None:
    close = DAILY[f"{symbol}_close"]
    _assert_series_equal_within(
        sizing.annual_vol(close), _expected(symbol)["annual_vol"]
    )


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_target_weight_matches_expected(symbol: str) -> None:
    signals = {s: ensemble.signal(DAILY[f"{s}_close"]) for s in SYMBOLS}
    vols = {s: sizing.annual_vol(DAILY[f"{s}_close"]) for s in SYMBOLS}
    expected = _expected(symbol)["target_weight"]
    actual = pd.Series(np.nan, index=DAILY.index, dtype=float)
    for day in DAILY.index:
        w = sizing.target_weights(
            {s: signals[s][day] for s in SYMBOLS},
            {s: vols[s][day] for s in SYMBOLS},
        )
        actual[day] = w[symbol]
    np.testing.assert_allclose(actual.values, expected.values, atol=1e-9, rtol=0)
