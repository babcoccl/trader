"""Trading cost model for backtests.

Fees and slippage come from SPECS.md section A. The backtest charges
``taker_fee + slippage`` on every trade's notional.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Maker/taker fees and slippage as fractions of traded notional."""

    maker_fee: float
    taker_fee: float
    slippage: float


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
