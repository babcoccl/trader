"""Regime label tests (spec G1): parity with expected_regimes.csv plus
unit edges. Pure pandas; no database."""

import os

import numpy as np
import pandas as pd

from trader.strategy import regime

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "research",
    "fixtures",
)
SYMBOLS = ("BTC-USD", "ETH-USD")


def _frames() -> dict[str, pd.DataFrame]:
    oc = pd.read_csv(os.path.join(FIXTURES, "btc_eth_daily.csv"), parse_dates=["date"])
    hl = pd.read_csv(
        os.path.join(FIXTURES, "btc_eth_daily_hl.csv"), parse_dates=["date"]
    )
    assert len(oc) == len(hl) and (oc["date"] == hl["date"]).all()
    return {
        symbol: pd.DataFrame(
            {
                "high": hl[f"{symbol}_high"].to_numpy(),
                "low": hl[f"{symbol}_low"].to_numpy(),
                "close": oc[f"{symbol}_close"].to_numpy(),
            },
            index=pd.DatetimeIndex(oc["date"]),
        )
        for symbol in SYMBOLS
    }


def _expected() -> pd.DataFrame:
    return pd.read_csv(os.path.join(FIXTURES, "expected_regimes.csv"))


def test_series_match_fixture() -> None:
    """Every row: vol_percentile and adx match the fixture to 1e-9,
    including NaN positions."""
    expected = _expected()
    for symbol, df in _frames().items():
        rows = expected[expected["symbol"] == symbol]
        assert len(rows) == len(df)
        vp = regime.vol_percentile(df["close"]).to_numpy()
        a = regime.adx(df["high"], df["low"], df["close"]).to_numpy()
        assert np.allclose(
            vp, rows["vol_percentile"].to_numpy(dtype=float), atol=1e-9, equal_nan=True
        )
        assert np.allclose(
            a, rows["adx14"].to_numpy(dtype=float), atol=1e-9, equal_nan=True
        )


def test_label_matches_fixture() -> None:
    """label() on the series cut at each sampled date gives the fixture's
    trend, volatility and sideways."""
    expected = _expected()
    checked = 0
    for symbol, df in _frames().items():
        rows = expected[expected["symbol"] == symbol].reset_index(drop=True)
        valid = rows[rows["trend"].notna() & (rows["trend"] != "")]
        # At least 200 dates spread over the file, including the first and
        # last row with a value.
        picks = np.linspace(0, len(valid) - 1, 250, dtype=int)
        for i in picks:
            pos = valid.index[i]
            row = valid.iloc[i]
            end = pos + 1
            rg = regime.label(
                int(row["rules_positive"]),
                df["high"].iloc[:end],
                df["low"].iloc[:end],
                df["close"].iloc[:end],
            )
            assert rg.trend == row["trend"], (symbol, row["date"])
            expected_vol = row["volatility"] if pd.notna(row["volatility"]) else None
            assert rg.volatility == expected_vol, (symbol, row["date"])
            side = row["sideways"]
            if pd.isna(side) or side == "":
                expected_side = None
            else:
                expected_side = bool(side) if isinstance(side, bool) else side == "true"
            assert rg.sideways == expected_side, (symbol, row["date"])
            checked += 1
    assert checked >= 200


def test_trend_boundaries() -> None:
    assert [regime.trend(k) for k in range(10)] == (
        ["downtrend"] * 3 + ["mixed"] * 4 + ["uptrend"] * 3
    )


def test_text_examples() -> None:
    assert (
        regime.Regime("downtrend", "volatile", False, 0.83, 25.0).text()
        == "Downtrend, volatile"
    )
    assert (
        regime.Regime("mixed", "calm", True, 0.2, 15.0).text()
        == "Mixed, calm, sideways reading"
    )
    assert regime.Regime("uptrend", None, None, None, None).text() == "Uptrend"


def test_details_round_trip() -> None:
    full = regime.Regime("mixed", "calm", True, 0.2, 15.0)
    assert regime.from_details(full.as_details()) == full
    sparse = regime.Regime("uptrend", None, None, None, None)
    assert regime.from_details(sparse.as_details()) == sparse
    assert regime.from_details({}) is None
    assert regime.from_details({"trend": None}) is None
    # No NaN ever reaches details.
    assert all(v is None or v == v for v in sparse.as_details().values())


def test_short_series_unknowns() -> None:
    close = pd.Series(np.arange(100.0) + 100)
    high, low = close + 1, close - 1
    # Under 180 days of volatility: volatility unknown.
    assert regime.label(5, high, low, close).volatility is None
    assert regime.label(5, high, low, close).vol_percentile is None
    # 20 days: ADX not yet defined, sideways unknown.
    short = regime.label(5, high.iloc[:20], low.iloc[:20], close.iloc[:20])
    assert short.sideways is None
    assert short.adx is None
