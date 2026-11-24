"""Sleeve selector (spec G2).

The daily cycle gets its target from a selector that adds up the sleeves
allowed to trade in each asset's regime. The registry holds exactly one
sleeve that trades — the trend sleeve, allowed in every regime — so every
target is the same number as before. Later specs add sleeves to the
registry in shadow mode; no setting, variable or route can change it.
"""

import math
from dataclasses import dataclass
from typing import Literal

from trader.strategy.regime import Regime

ALL_TRENDS: frozenset[str] = frozenset({"uptrend", "mixed", "downtrend"})


@dataclass(frozen=True)
class SleeveSpec:
    name: str  # "trend"
    title: str  # "Trend rules"
    mode: Literal["active", "shadow"]
    trends: frozenset[str]  # trend states in which it may hold a position
    needs_sideways: bool  # True: also needs regime.sideways is True
    max_weight: float  # most it may hold in one asset, fraction of the account


SLEEVES: tuple[SleeveSpec, ...] = (
    SleeveSpec("trend", "Trend rules", "active", ALL_TRENDS, False, 1.0),
    # Spec G3: shadow mode — records a hypothetical book, never trades.
    SleeveSpec(
        "dip",
        "Dip-buying in a mixed regime",
        "shadow",
        frozenset({"mixed"}),
        False,
        0.25,
    ),
    # Spec G4: shadow mode — records a hypothetical book, never trades.
    SleeveSpec(
        "funding",
        "Negative funding while the trend is off",
        "shadow",
        frozenset({"downtrend"}),
        False,
        0.25,
    ),
    # Spec G5: shadow mode — records a hypothetical book, never trades.
    SleeveSpec(
        "grid",
        "Grid while the market reads sideways",
        "shadow",
        ALL_TRENDS,
        True,
        0.5,
    ),
)


@dataclass(frozen=True)
class SleevePart:
    name: str
    allowed: bool
    proposed: float
    weight: float


@dataclass(frozen=True)
class Combined:
    weights: dict[str, float]  # symbol -> target weight
    parts: dict[str, list[SleevePart]]  # symbol -> one entry per active sleeve


def get(name: str) -> SleeveSpec:
    """The registry entry named ``name``; ``KeyError`` when absent."""
    for spec in SLEEVES:
        if spec.name == name:
            return spec
    raise KeyError(name)


def active() -> tuple[SleeveSpec, ...]:
    """The sleeves that trade (mode ``active``), in registry order."""
    return tuple(spec for spec in SLEEVES if spec.mode == "active")


def allowed(spec: SleeveSpec, regime: Regime | None) -> bool:
    """May ``spec`` hold a position in ``regime``?

    With no regime, True only for a sleeve allowed in every trend with no
    sideways requirement: an unknown regime never blocks the trend sleeve.
    """
    if regime is None:
        return spec.trends == ALL_TRENDS and not spec.needs_sideways
    if spec.needs_sideways and regime.sideways is not True:
        return False
    return regime.trend in spec.trends


def combine(
    proposals: dict[str, dict[str, float]],  # sleeve name -> symbol -> weight
    regimes: dict[str, Regime | None],  # symbol -> regime
) -> Combined:
    """Sum the active sleeves' proposals into per-symbol target weights.

    ``ValueError`` when ``proposals`` names a sleeve not in ``SLEEVES`` or
    a shadow sleeve, or when an active sleeve is missing. A symbol's
    weight is the sum of its sleeves' allowed, capped weights; totals
    above 1.0 are scaled down so nothing is short or leveraged.
    """
    known = {spec.name for spec in SLEEVES}
    for name in proposals:
        if name not in known:
            raise ValueError(f"unknown sleeve {name!r}")
        if get(name).mode != "active":
            raise ValueError(f"sleeve {name!r} does not trade (mode shadow)")
    missing = [spec.name for spec in active() if spec.name not in proposals]
    if missing:
        raise ValueError(f"no proposal for active sleeve(s): {', '.join(missing)}")

    weights: dict[str, float] = {}
    parts: dict[str, list[SleevePart]] = {}
    for symbol, regime in regimes.items():
        total = 0.0
        rows: list[SleevePart] = []
        for spec in active():
            proposed = proposals[spec.name].get(symbol, 0.0)
            ok = allowed(spec, regime)
            if not ok or math.isnan(proposed) or proposed < 0:
                weight = 0.0
            else:
                weight = min(proposed, spec.max_weight)
            rows.append(
                SleevePart(name=spec.name, allowed=ok, proposed=proposed, weight=weight)
            )
            total += weight
        weights[symbol] = total
        parts[symbol] = rows

    gross = sum(weights.values())
    if gross > 1.0 + 1e-12:
        weights = {symbol: w / gross for symbol, w in weights.items()}
        parts = {
            symbol: [
                SleevePart(
                    name=p.name,
                    allowed=p.allowed,
                    proposed=p.proposed,
                    weight=p.weight / gross,
                )
                for p in rows
            ]
            for symbol, rows in parts.items()
        }
    return Combined(weights=weights, parts=parts)
