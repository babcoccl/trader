"""Sleeve selector tests (spec G2): the registry, ``allowed`` and
``combine``; fixture parity of the stored target weights."""

import math
import os

import pandas as pd
import pytest

from trader.strategy import sleeves
from trader.strategy.regime import Regime
from trader.strategy.sleeves import SleeveSpec

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "research",
    "fixtures",
)


def _regime(trend: str, sideways: bool | None = None) -> Regime:
    return Regime(
        trend=trend,  # type: ignore[arg-type]
        volatility=None,
        sideways=sideways,
        vol_percentile=None,
        adx=None,
    )


def test_registry_has_exactly_one_trading_sleeve() -> None:
    """Spec G2 pins the registry: one active sleeve, the trend sleeve,
    allowed in every regime with max_weight 1.0. Changing this needs a new
    spec and an owner decision recorded in docs/DECISIONS.md."""
    act = sleeves.active()
    assert len(act) == 1
    spec = act[0]
    assert spec.name == "trend"
    assert spec.trends == sleeves.ALL_TRENDS
    assert spec.needs_sideways is False
    assert spec.max_weight == 1.0
    assert sleeves.get("trend") is spec


def test_combine_returns_fixture_targets_unchanged() -> None:
    """For every row of expected_signals.csv, combining the trend sleeve's
    proposal returns the stored target_weight exactly."""
    df = pd.read_csv(os.path.join(FIXTURES, "expected_signals.csv"))
    for _, rows in df.groupby("date"):
        proposals = {
            "trend": {
                r["symbol"]: float(r["target_weight"]) for _, r in rows.iterrows()
            }
        }
        regimes = {r["symbol"]: None for _, r in rows.iterrows()}
        combined = sleeves.combine(proposals, regimes)
        for _, r in rows.iterrows():
            assert combined.weights[r["symbol"]] == float(r["target_weight"])
            assert combined.parts[r["symbol"]][0].weight == float(r["target_weight"])


def _patched_registry(monkeypatch: pytest.MonkeyPatch) -> SleeveSpec:
    """Trend sleeve plus a second active sleeve allowed only in `mixed`,
    capped at 0.25 per asset."""
    second = SleeveSpec(
        name="dip",
        title="Dip buys",
        mode="active",
        trends=frozenset({"mixed"}),
        needs_sideways=False,
        max_weight=0.25,
    )
    monkeypatch.setattr(sleeves, "SLEEVES", (sleeves.get("trend"), second))
    return second


def test_combine_second_sleeve_by_regime(monkeypatch: pytest.MonkeyPatch) -> None:
    second = _patched_registry(monkeypatch)
    proposals = {
        "trend": {"BTC-USD": 0.4},
        "dip": {"BTC-USD": 0.5},
    }
    for trend, expected_second in (
        ("mixed", 0.25),  # allowed, capped at max_weight
        ("uptrend", 0.0),
        ("downtrend", 0.0),
    ):
        combined = sleeves.combine(proposals, {"BTC-USD": _regime(trend)})
        parts = {p.name: p for p in combined.parts["BTC-USD"]}
        assert parts["dip"].weight == expected_second
        assert parts["dip"].allowed is (trend == "mixed")
        assert combined.weights["BTC-USD"] == 0.4 + expected_second
        assert parts["dip"].proposed == 0.5
    assert second.max_weight == 0.25


def test_combine_scales_totals_above_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched_registry(monkeypatch)
    combined = sleeves.combine(
        {"trend": {"BTC-USD": 0.9, "ETH-USD": 0.2}, "dip": {"BTC-USD": 0.25}},
        {"BTC-USD": _regime("mixed"), "ETH-USD": _regime("mixed")},
    )
    gross = 0.9 + 0.25 + 0.2
    assert combined.weights["BTC-USD"] == pytest.approx(1.15 / gross)
    assert combined.weights["ETH-USD"] == pytest.approx(0.2 / gross)
    assert sum(combined.weights.values()) == pytest.approx(1.0)
    assert all(w >= 0 for w in combined.weights.values())
    parts = {p.name: p for p in combined.parts["BTC-USD"]}
    assert parts["dip"].weight == pytest.approx(0.25 / gross)
    assert parts["trend"].weight == pytest.approx(0.9 / gross)


def test_combine_nan_and_negative_proposals_give_zero() -> None:
    combined = sleeves.combine(
        {"trend": {"BTC-USD": math.nan, "ETH-USD": -0.3}},
        {"BTC-USD": _regime("uptrend"), "ETH-USD": _regime("mixed")},
    )
    assert combined.weights == {"BTC-USD": 0.0, "ETH-USD": 0.0}
    assert all(p.weight == 0.0 for rows in combined.parts.values() for p in rows)


def test_combine_rejects_bad_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    regimes = {"BTC-USD": None}
    with pytest.raises(ValueError, match="unknown sleeve"):
        sleeves.combine({"trend": {}, "nope": {"BTC-USD": 0.1}}, regimes)
    shadow = SleeveSpec(
        name="grid",
        title="Grid",
        mode="shadow",
        trends=sleeves.ALL_TRENDS,
        needs_sideways=True,
        max_weight=0.25,
    )
    monkeypatch.setattr(sleeves, "SLEEVES", (sleeves.get("trend"), shadow))
    with pytest.raises(ValueError, match="shadow"):
        sleeves.combine({"trend": {"BTC-USD": 0.1}, "grid": {"BTC-USD": 0.1}}, regimes)
    monkeypatch.undo()
    with pytest.raises(ValueError, match="no proposal"):
        sleeves.combine({}, regimes)


def test_allowed_per_trend_and_sideways() -> None:
    spec = sleeves.get("trend")
    assert all(sleeves.allowed(spec, _regime(t)) for t in sleeves.ALL_TRENDS)
    needs = SleeveSpec(
        name="grid",
        title="Grid",
        mode="shadow",
        trends=frozenset({"mixed"}),
        needs_sideways=True,
        max_weight=0.25,
    )
    assert sleeves.allowed(needs, _regime("mixed", sideways=True)) is True
    assert sleeves.allowed(needs, _regime("mixed", sideways=False)) is False
    assert sleeves.allowed(needs, _regime("mixed", sideways=None)) is False
    assert sleeves.allowed(needs, _regime("uptrend", sideways=True)) is False
    # No regime: only a sleeve allowed in every trend with no sideways need.
    assert sleeves.allowed(spec, None) is True
    assert sleeves.allowed(needs, None) is False
    partial = SleeveSpec(
        name="x",
        title="X",
        mode="shadow",
        trends=frozenset({"mixed"}),
        needs_sideways=False,
        max_weight=0.25,
    )
    assert sleeves.allowed(partial, None) is False
