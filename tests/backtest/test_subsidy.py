"""Unit tests for the ping_pong_subsidy sleeve."""

import math

import pandas as pd

from trader.backtest.subsidy import coinbase_fee_rate, run_with_ping_pong_subsidy

SYM = "BTC-USD"
CAPITAL = 10_000.0


def _frames(closes: pd.Series) -> tuple[pd.DataFrame, ...]:
    opens = closes.to_frame(SYM)
    highs = (closes * 1.005).to_frame(SYM)
    lows = (closes * 0.995).to_frame(SYM)
    return opens, highs, lows, closes.to_frame(SYM)


def _sideways(days: int = 120) -> tuple[pd.DataFrame, ...]:
    idx = pd.date_range("2024-01-01", periods=days)
    # Alternating +/-2% moves: choppy, low ADX.
    close = pd.Series(
        [100.0 * (1.02 if i % 2 else 0.98) for i in range(days)], index=idx
    )
    return _frames(close)


def _trending(days: int = 120) -> tuple[pd.DataFrame, ...]:
    idx = pd.date_range("2024-01-01", periods=days)
    close = pd.Series([100.0 + 2.0 * i for i in range(days)], index=idx)
    return _frames(close)


def _zero_weights(closes: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(0.0, index=closes.index, columns=closes.columns)


def _booster_trades(result) -> list:
    return [t for t in result.trades if t["size"] == 0.0]


def test_coinbase_fee_rate_tiers() -> None:
    assert coinbase_fee_rate(0.0) == (0.0040, 0.0060)
    assert coinbase_fee_rate(9_999.99) == (0.0040, 0.0060)
    assert coinbase_fee_rate(10_000.0) == (0.0025, 0.0040)
    assert coinbase_fee_rate(49_999.99) == (0.0025, 0.0040)
    assert coinbase_fee_rate(50_000.0) == (0.0015, 0.0025)


def test_booster_fires_when_sideways_and_below_tier() -> None:
    opens, highs, lows, closes = _sideways(days=50)
    result = run_with_ping_pong_subsidy(
        opens,
        highs,
        lows,
        closes,
        _zero_weights(closes),
        CAPITAL,
        "2024-01-01",
        "2024-02-19",
        target_tier_volume=10_000.0,
        daily_booster_cap_usd=2_000.0,
        spread_capture_bps=10.0,
    )
    booster = _booster_trades(result)
    assert booster
    # 10bps spread capture < 40bps tier-1 maker fee: net pnl is the subsidy
    # cost, and the maker fee is recorded against the trade.
    t = booster[0]
    boost = min(2_000.0, 10_000.0)
    assert t["pnl"] == (boost * (0.0010 - 0.0040)) / CAPITAL
    assert t["fee"] == (boost * 0.0040) / CAPITAL


def test_booster_skipped_when_trending() -> None:
    opens, highs, lows, closes = _trending()
    result = run_with_ping_pong_subsidy(
        opens,
        highs,
        lows,
        closes,
        _zero_weights(closes),
        CAPITAL,
        "2024-01-01",
        "2024-05-01",
        target_tier_volume=10_000.0,
    )
    assert _booster_trades(result) == []


def test_booster_skipped_when_volume_target_met() -> None:
    opens, highs, lows, closes = _sideways(days=50)
    result = run_with_ping_pong_subsidy(
        opens,
        highs,
        lows,
        closes,
        _zero_weights(closes),
        CAPITAL,
        "2024-01-01",
        "2024-02-19",
        target_tier_volume=0.0,  # rolling volume (0) is never below target
    )
    assert _booster_trades(result) == []


def test_booster_stops_once_tier_reached() -> None:
    opens, highs, lows, closes = _sideways(days=50)
    result = run_with_ping_pong_subsidy(
        opens,
        highs,
        lows,
        closes,
        _zero_weights(closes),
        CAPITAL,
        "2024-01-01",
        "2024-02-19",
        target_tier_volume=1_500.0,
        daily_booster_cap_usd=2_000.0,
    )
    booster = _booster_trades(result)
    # One boost of 1500 fills the tier; the rolling window still holds it for
    # the rest of the 50-day run, so no further boosts fire.
    assert len(booster) == 1
    t = booster[0]
    assert t["fee"] == (1_500.0 * 0.0040) / CAPITAL
    assert t["pnl"] == (1_500.0 * (0.0010 - 0.0040)) / CAPITAL


def test_result_is_normalized() -> None:
    opens, highs, lows, closes = _sideways()
    result = run_with_ping_pong_subsidy(
        opens,
        highs,
        lows,
        closes,
        _zero_weights(closes),
        CAPITAL,
        "2024-01-01",
        "2024-04-30",
    )
    assert math.isfinite(result.equity.iloc[-1])
    assert 0.5 < result.equity.iloc[-1] < 1.5
