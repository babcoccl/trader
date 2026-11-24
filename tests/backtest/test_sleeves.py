"""Unit tests for cross_sectional_momentum_weights."""

import numpy as np
import pandas as pd

from trader.backtest.sleeves import cross_sectional_momentum_weights

N = 160
IDX = pd.date_range("2023-01-01", periods=N, freq="D")


def _series(r: float, amp: float, base: float = 100.0) -> pd.Series:
    t = np.arange(N)
    return pd.Series(base * (1 + r) ** t * (1 + amp * np.sin(t)), index=IDX)


def _closes(**cols: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(cols, index=IDX)


def test_top_k_picks_highest_risk_adjusted() -> None:
    closes = _closes(
        **{
            "FAST-USD": _series(0.005, 0.001),
            "VOLA-USD": _series(0.006, 0.02),
            "SLOW-USD": _series(0.002, 0.05),
        }
    )
    w = cross_sectional_momentum_weights(closes, top_k=1, btc_gate=False)
    late = w.iloc[-1]
    assert late["FAST-USD"] > 0
    assert late["VOLA-USD"] == 0.0
    assert late["SLOW-USD"] == 0.0

    w2 = cross_sectional_momentum_weights(closes, top_k=2, btc_gate=False)
    late2 = w2.iloc[-1]
    assert late2["FAST-USD"] > 0
    assert late2["VOLA-USD"] > 0
    assert late2["SLOW-USD"] == 0.0


def test_negative_momentum_gets_zero_weight() -> None:
    closes = _closes(
        **{
            "UP-USD": _series(0.005, 0.001),
            "DOWN-USD": _series(-0.005, 0.001),
        }
    )
    w = cross_sectional_momentum_weights(closes, top_k=2, btc_gate=False)
    assert (w["DOWN-USD"] == 0.0).all()
    assert w["UP-USD"].iloc[-1] > 0


def test_btc_gate_flattens_when_below_sma50() -> None:
    btc = pd.Series(
        np.concatenate([np.full(N - 40, 100.0), 100.0 * 0.99 ** np.arange(40)]),
        index=IDX,
    )
    closes = _closes(**{"BTC-USD": btc, "UP-USD": _series(0.005, 0.001)})

    gated = cross_sectional_momentum_weights(closes, top_k=2, btc_gate=True)
    # Once BTC trades below its 50-day SMA the whole frame goes to cash.
    assert (gated.iloc[-30:] == 0.0).all().all()

    ungated = cross_sectional_momentum_weights(closes, top_k=2, btc_gate=False)
    assert ungated["UP-USD"].iloc[-1] > 0


def test_gross_weight_never_exceeds_one() -> None:
    closes = _closes(
        **{f"A{i}-USD": _series(0.004 + i * 0.001, 0.001) for i in range(5)}
    )
    w = cross_sectional_momentum_weights(
        closes, vol_target=0.70, top_k=5, btc_gate=False
    )
    assert (w.sum(axis=1) <= 1.0 + 1e-12).all()
