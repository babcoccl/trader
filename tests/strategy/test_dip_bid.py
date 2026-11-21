"""Dip-bid maths tests: sigma and quote against hand-computed numbers."""

import math

import numpy as np
import pandas as pd
import pytest

from trader.strategy import dip_bid


def test_hourly_sigma_constant_returns_is_zero() -> None:
    """Identical log returns have zero dispersion."""
    close = pd.Series(np.exp(np.arange(300) * 0.001))
    assert dip_bid.hourly_sigma(close) == pytest.approx(0.0)


def test_hourly_sigma_alternating_returns() -> None:
    """Returns alternating +a/-a: std = a*sqrt(288/287), sigma = std*sqrt(12)."""
    a = 0.002
    signs = np.where(np.arange(289) % 2 == 0, a, -a)
    close = pd.Series(np.exp(np.cumsum(signs)))
    expected = a * math.sqrt(288 / 287) * math.sqrt(12)
    assert dip_bid.hourly_sigma(close) == pytest.approx(expected)


def test_hourly_sigma_uses_last_288_returns() -> None:
    """Earlier history is ignored: prepend wild bars, sigma is unchanged."""
    tail = pd.Series(np.exp(np.cumsum(np.full(289, 0.001))))
    wild = pd.Series(np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.05, 50))))
    both = pd.concat([wild * tail.iloc[0], tail], ignore_index=True)
    assert dip_bid.hourly_sigma(both) == pytest.approx(dip_bid.hourly_sigma(tail))


def test_hourly_sigma_nan_with_fewer_than_288_returns() -> None:
    assert math.isnan(dip_bid.hourly_sigma(pd.Series(np.linspace(1, 2, 288))))
    assert math.isnan(dip_bid.hourly_sigma(pd.Series(dtype=float)))


def test_quote_hand_computed() -> None:
    q = dip_bid.quote(last_close=100.0, sigma=0.005)
    assert q.bid == pytest.approx(98.0)
    assert q.target == pytest.approx(98.98)
    assert q.stop == pytest.approx(94.08)


def test_quote_multipliers() -> None:
    q = dip_bid.quote(100.0, 0.01, k=2.0, target_mult=1.0, stop_mult=1.0)
    assert q.bid == pytest.approx(98.0)
    assert q.target == pytest.approx(99.96)
    assert q.stop == pytest.approx(96.04)


def test_trend_gate() -> None:
    assert dip_bid.trend_gate(0.5) is True
    assert dip_bid.trend_gate(0.499) is False
    assert dip_bid.trend_gate(0.0) is False
