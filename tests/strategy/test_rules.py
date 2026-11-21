"""Hand-made edge cases for each rule."""

import numpy as np
import pandas as pd
import pytest

from trader.strategy import ensemble, rules, sizing


def test_sma_equal_to_mean_is_zero() -> None:
    # Constant price: close == rolling mean, so the rule is 0, not 1.
    close = pd.Series([5.0] * 60)
    result = rules.sma(close, 50)
    assert result.iloc[49] == 0.0
    assert result.iloc[59] == 0.0


def test_sma_first_valid_day_and_nans() -> None:
    close = pd.Series(range(1, 60), dtype=float)
    result = rules.sma(close, 50)
    assert result.iloc[:49].isna().all()
    assert result.iloc[49] == 1.0  # rising series, close above mean


def test_momentum_first_valid_day() -> None:
    close = pd.Series(range(1, 40), dtype=float)
    result = rules.momentum(close, 30)
    assert result.iloc[:30].isna().all()
    assert result.iloc[30] == 1.0  # 31 > 1


def test_momentum_equal_to_shifted_is_zero() -> None:
    close = pd.Series([7.0] * 40)
    result = rules.momentum(close, 30)
    assert result.iloc[30] == 0.0


def test_breakout_holds_state_between_signals() -> None:
    close = pd.Series(np.nan, index=range(30))
    # Days 0-19 establish hi at index 19; close never <= n//2=10-day low.
    close.iloc[:20] = 10.0
    close.iloc[19] = 20.0  # close >= rolling(20).max() -> state 1
    close.iloc[20:25] = 15.0  # inside band -> hold 1
    close.iloc[25] = 5.0  # close <= rolling(10).min() -> state 0
    close.iloc[26:] = 12.0  # inside band -> hold 0
    result = rules.breakout(close, 20)
    assert result.iloc[:19].isna().all()
    assert result.iloc[19] == 1.0
    assert (result.iloc[20:25] == 1.0).all()
    assert result.iloc[25] == 0.0
    assert (result.iloc[26:] == 0.0).all()


def test_breakout_exact_high_and_low_inclusive() -> None:
    close = pd.Series(np.nan, index=range(25))
    close.iloc[:20] = np.linspace(1.0, 20.0, 20)  # day 19 hits the high
    result = rules.breakout(close, 20)
    assert result.iloc[19] == 1.0
    # close <= lo is also inclusive: descending run of 10 days
    close2 = pd.Series(np.linspace(100.0, 50.0, 25))
    result2 = rules.breakout(close2, 20)
    # rolling(10).min() at index 9 is 91.89; close there is 63.89 <= it -> 0
    assert result2.iloc[19] == 0.0


def test_ensemble_nan_matches_sma200() -> None:
    close = pd.Series(range(1, 260), dtype=float)
    result = ensemble.signal(close)
    sma200 = rules.sma(close, 200)
    assert (result.isna() == sma200.isna()).all()
    assert (result.dropna() >= 0.0).all() and (result.dropna() <= 1.0).all()


def test_annual_vol_nans_before_min_periods() -> None:
    close = pd.Series(range(1, 25), dtype=float)
    result = sizing.annual_vol(close)
    assert result.iloc[:20].isna().all()
    assert result.iloc[20] > 0


def test_annual_vol_missing_close_is_not_a_zero_return() -> None:
    # Constant 1% daily returns with one missing close: if the gap were
    # padded into a zero return the vol would be clearly positive.
    close = pd.Series([100.0 * 1.01**i for i in range(40)])
    close.iloc[30] = np.nan
    assert sizing.annual_vol(close).iloc[39] < 1e-9


def test_target_weights_basic_and_nan() -> None:
    w = sizing.target_weights({"A": 1.0, "B": float("nan")}, {"A": 0.40, "B": 0.40})
    assert w == {"A": 0.5, "B": 0.0}


def test_target_weights_missing_vol_gives_zero() -> None:
    w = sizing.target_weights({"A": float("nan"), "B": 1.0}, {})
    assert w == {"A": 0.0, "B": 0.0}


def test_target_weights_full_signal_sums_to_one() -> None:
    w = sizing.target_weights({"A": 1.0, "B": 1.0}, {"A": 0.10, "B": 0.10})
    assert w == {"A": 0.5, "B": 0.5}


def test_target_weights_normalizes_gross_above_one() -> None:
    # Signals in [0, 1] cannot push the raw sum above 1.0, so use 3.0 to
    # reach the normalization branch: raw 1.5 + 0.5 = 2.0 -> 0.75, 0.25.
    w = sizing.target_weights({"A": 3.0, "B": 1.0}, {"A": 0.10, "B": 0.10})
    assert w["A"] == pytest.approx(0.75)
    assert w["B"] == pytest.approx(0.25)
    assert sum(w.values()) == pytest.approx(1.0)


def test_target_weights_zero_vol_keeps_signal_weight() -> None:
    w = sizing.target_weights({"A": 1.0, "B": 0.5}, {"A": 0.0, "B": 0.40})
    assert w["A"] == pytest.approx(0.5)
    assert w["B"] == pytest.approx(0.25)


def test_target_weights_vol_target_scaling() -> None:
    w = sizing.target_weights({"A": 1.0}, {"A": 0.80})
    assert w["A"] == pytest.approx(0.5)  # 1/1 * min(1, 0.40/0.80)


def test_should_trade() -> None:
    assert not sizing.should_trade(0.5, 0.45)
    assert sizing.should_trade(0.5, 0.3)
    assert sizing.should_trade(0.0, 0.1)  # exit always trades
    assert not sizing.should_trade(0.0, 0.0)
