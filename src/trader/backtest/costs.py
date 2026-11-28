"""Trading cost model for backtests.

Fees and slippage come from SPECS.md section A. The backtest charges
``taker_fee + slippage`` on every trade's notional.
"""

from dataclasses import dataclass
from typing import Literal

ExecutionMode = Literal["maker", "taker", "mixed"]


@dataclass(frozen=True)
class CostModel:
    """Maker/taker fees and slippage as fractions of traded notional."""

    maker_fee: float
    taker_fee: float
    slippage: float


@dataclass(frozen=True)
class FeeTier:
    """A named venue fee tier, in basis points per side."""

    maker_bps: float
    taker_bps: float
    default_slippage_bps: float = 0.0


# Coinbase fee tiers (bps per side). ``legacy_45`` reproduces the Study 1-4
# flat 45bps cost model.
FEE_TIERS: dict[str, FeeTier] = {
    "legacy_45": FeeTier(45.0, 45.0),
    "intro_spot": FeeTier(50.0, 90.0),
    "adv1_spot": FeeTier(35.0, 75.0),
    "adv2_spot": FeeTier(15.0, 30.0),
    "adv3_spot": FeeTier(10.0, 15.0),
    "vip1_spot": FeeTier(6.0, 10.0),
    "adv2_fut": FeeTier(5.5, 7.0),
    "adv3_fut": FeeTier(4.5, 7.0),
}

SPOT_TIERS = ["adv1_spot", "adv2_spot", "adv3_spot"]
FUT_TIERS = ["adv2_fut", "adv3_fut"]


def tier_cost_model(
    fee_tier: str,
    execution_mode: ExecutionMode = "maker",
    slippage_bps: float | None = None,
) -> CostModel:
    """CostModel for a fee tier under an execution mode.

    ``maker`` charges the maker fee with no slippage (resting fills);
    ``taker`` charges the taker fee plus slippage; ``mixed`` charges the
    midpoint of maker/taker plus slippage, the blended rate of strategies
    that rest entries and take exits.
    """
    try:
        tier = FEE_TIERS[fee_tier]
    except KeyError:
        raise ValueError(
            f"unknown fee_tier {fee_tier!r}; expected one of {sorted(FEE_TIERS)}"
        ) from None
    slip = (
        slippage_bps if slippage_bps is not None else tier.default_slippage_bps
    ) / 10_000
    maker = tier.maker_bps / 10_000
    taker = tier.taker_bps / 10_000
    if execution_mode == "maker":
        return CostModel(maker_fee=maker, taker_fee=maker, slippage=0.0)
    if execution_mode == "taker":
        return CostModel(maker_fee=taker, taker_fee=taker, slippage=slip)
    blended = (maker + taker) / 2
    return CostModel(maker_fee=maker, taker_fee=blended, slippage=slip)


BITSTAMP = CostModel(maker_fee=0.003, taker_fee=0.004, slippage=0.0001)
STUDY_040 = CostModel(maker_fee=0.004, taker_fee=0.004, slippage=0.0005)
# Coinbase Advanced fee tiers (spec W1.2a): used for live fee estimates when
# TRADER_VENUE=coinbase.
COINBASE_TIER_1 = CostModel(maker_fee=0.0040, taker_fee=0.0060, slippage=0.0001)
COINBASE_TIER_2 = CostModel(maker_fee=0.0025, taker_fee=0.0040, slippage=0.0001)

PRESETS: dict[str, CostModel] = {
    "bitstamp": BITSTAMP,
    "study_040": STUDY_040,
    "coinbase_tier_1": COINBASE_TIER_1,
    "coinbase_tier_2": COINBASE_TIER_2,
}
