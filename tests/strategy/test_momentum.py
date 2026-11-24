"""Cross-sectional momentum target weights (spec W1.2a): pure pandas tests."""

import numpy as np
import pandas as pd
import pytest

from trader.strategy import momentum

DAYS = 200


def _closes(drifts: dict[str, float], days: int = DAYS) -> pd.DataFrame:
    """Daily closes: each symbol a steady geometric ramp at ``drift`` a day."""
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    i = np.arange(days)
    return pd.DataFrame(
        {s: 100.0 * (1.0 + d) ** i for s, d in drifts.items()}, index=idx
    )


def test_top_k_equal_share() -> None:
    """With four positive-momentum assets and top_k=3, the three strongest
    share weight equally and the weakest gets 0."""
    closes = _closes(
        {
            "BTC-USD": 0.004,
            "ETH-USD": 0.003,
            "SOL-USD": 0.002,
            "XRP-USD": 0.001,
        }
    )
    w = momentum.target_weights(closes, vol_target=10.0, top_k=3, btc_gate=True)
    assert w["XRP-USD"] == 0.0
    assert w["BTC-USD"] == pytest.approx(1 / 3)
    assert w["ETH-USD"] == pytest.approx(1 / 3)
    assert w["SOL-USD"] == pytest.approx(1 / 3)


def test_lookback_window() -> None:
    """A rally entirely inside the lookback counts; one entirely before it
    does not."""
    days = DAYS
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    recent = np.ones(days)
    recent[-30:] = np.linspace(1.0, 2.0, 30)  # rallied in the last 30 days
    stale = np.linspace(1.0, 3.0, days)
    stale[-61:] = 3.0  # flat for the last 61 days: 60d return is 0
    closes = pd.DataFrame({"BTC-USD": recent * 100, "ETH-USD": stale * 100}, index=idx)
    w = momentum.target_weights(
        closes, vol_target=10.0, lookback_days=60, top_k=2, btc_gate=False
    )
    assert w["BTC-USD"] > 0.0
    assert w["ETH-USD"] == 0.0


def test_btc_gate_blocks_all() -> None:
    """BTC at or under its 50-day SMA zeroes every weight."""
    days = DAYS
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    i = np.arange(days)
    btc = 100.0 * 1.01**i
    btc[-10:] = btc[-10] * 0.7  # crash below the 50-day SMA
    closes = pd.DataFrame({"BTC-USD": btc, "ETH-USD": 100.0 * 1.005**i}, index=idx)
    w = momentum.target_weights(closes, btc_gate=True)
    assert all(v == 0.0 for v in w.values())
    # Gate off: ETH's positive momentum is eligible again.
    w = momentum.target_weights(closes, btc_gate=False)
    assert w["ETH-USD"] > 0.0


def test_btc_gate_needs_btc_column() -> None:
    """No BTC-USD column: the gate does not apply."""
    closes = _closes({"ETH-USD": 0.003, "SOL-USD": 0.002})
    w = momentum.target_weights(closes, vol_target=10.0, btc_gate=True)
    assert w["ETH-USD"] > 0.0


def test_gross_capped_at_one() -> None:
    """Low-vol assets at top_k=1 can never exceed a gross of 1.0."""
    closes = _closes({"BTC-USD": 0.004, "ETH-USD": 0.003})
    w = momentum.target_weights(closes, vol_target=100.0, top_k=1)
    assert sum(w.values()) <= 1.0
    assert w["BTC-USD"] <= 1.0


def test_short_history_and_nan_get_zero() -> None:
    """A symbol without a full lookback or with NaN scores is 0.0."""
    closes = _closes({"BTC-USD": 0.004, "ETH-USD": 0.003})
    closes["SOL-USD"] = np.nan
    closes.iloc[-30:, closes.columns.get_loc("SOL-USD")] = 100.0  # 30 days only
    w = momentum.target_weights(closes, vol_target=10.0, lookback_days=60, top_k=3)
    assert w["SOL-USD"] == 0.0
    assert w["BTC-USD"] > 0.0


def test_empty_frame() -> None:
    closes = pd.DataFrame({"BTC-USD": []})
    assert momentum.target_weights(closes) == {"BTC-USD": 0.0}
