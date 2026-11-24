"""Plan builder (spec 2.2): turn a weight change into an approvable plan.

``build`` is pure: it returns an unsaved ``Plan`` row or ``None`` when the
trading band says do nothing. The caller (``engine.daily_cycle``) sets
``decision_id``, runs ``risk.check_plan`` and persists the plan.

Entry orders live in ``plans.entry_orders`` as JSON the executor (spec 2.4)
turns into venue orders:

- Buy: one maker-or-cancel limit at the reference (best-bid) price,
  re-priced to the best bid every ``reprice_seconds``; anything unfilled
  ``market_after_seconds`` after approval converts to a market order.
- Sell: one limit at ``price``, converted to market after the same limit.

Prices and quantities are rounded *down* to the venue's ``tick`` and ``lot``
passed in by the caller (Bitstamp ``/markets/`` ``counter_decimals`` /
``base_decimals``). The buy's ``stop_price`` is the application-run disaster
stop (Bitstamp's API has no spot stop orders): three ten-day swings wide.
"""

import math
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from trader.backtest.costs import CostModel
from trader.core.clock import Clock
from trader.ledger.models import Plan
from trader.strategy import sizing

Q18 = Decimal("1e-18")

APPROVAL_EXPIRY = timedelta(hours=6)
REPRICE_SECONDS = 300  # re-quote the limit to the best bid every 5 minutes
MARKET_AFTER_SECONDS = 3600  # unfilled 1 hour after approval -> market order
STOP_SWINGS = 3  # three ten-day swings below the reference price
STOP_DAYS = 10


def _round_down(x: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` at or below ``x``."""
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _q(x: Decimal) -> Decimal:
    """Match the NUMERIC(38,18) storage rounding (half away from zero)."""
    return x.quantize(Q18, rounding=ROUND_HALF_UP)


def build(
    symbol: str,
    target_weight: float,
    current_weight: float,
    equity_usd: Decimal,
    price: Decimal,
    annual_vol: float,
    cost: CostModel,
    clock: Clock,
    *,
    rules_positive: int,
    tick: Decimal,
    lot: Decimal,
    band: float = 0.20,
) -> Plan | None:
    """Build the plan that moves ``symbol`` from current to target weight.

    ``price`` is the current best bid for a buy (the limit price), the
    reference market price for a sell. ``rules_positive`` and ``annual_vol``
    go into the rationale. Returns ``None`` when ``sizing.should_trade``
    is false at ``band``.
    """
    if not sizing.should_trade(target_weight, current_weight, band=band):
        return None

    target = Decimal(str(target_weight))
    current = Decimal(str(current_weight))
    side = "buy" if target > current else "sell"
    notional = abs(target - current) * equity_usd

    limit_price = _round_down(price, tick)
    quantity = _round_down(notional / price, lot) if price > 0 else Decimal(0)

    daily_swing = Decimal(str(annual_vol / math.sqrt(365)))
    stop_price = (
        _round_down(
            price * (1 - 3 * daily_swing * Decimal(str(math.sqrt(STOP_DAYS)))),
            tick,
        )
        if side == "buy"
        else None
    )

    # Taker + slippage both ways (in and the eventual exit), per spec.
    est_fees = _q(notional * Decimal(str(cost.taker_fee + cost.slippage)) * 2)
    if stop_price is not None:
        worst_case = _q(notional * (1 - stop_price / price) + est_fees)
    else:
        # An exposure-reducing sell carries no disaster stop; the guaranteed
        # cost is the round-trip fee estimate.
        worst_case = est_fees

    order: dict[str, object] = {
        "side": side,
        "type": "limit",
        "price": str(limit_price),
        "quantity": str(quantity),
        "market_after_seconds": MARKET_AFTER_SECONDS,
    }
    if side == "buy":
        order["maker_or_cancel"] = True
        order["reprice_seconds"] = REPRICE_SECONDS

    direction = "raise" if side == "buy" else "lower"
    rationale = (
        f"I would like to {direction} {symbol} from {current_weight:.0%} to "
        f"{target_weight:.0%} of the account because {rules_positive} of 9 "
        f"trend rules are positive and volatility is {annual_vol:.0%} a year."
    )

    return Plan(
        sleeve="trend",
        symbol=symbol,
        side=side,
        # Exposure-reducing sells are pre-approved by the owner's standing
        # decision on stops and exits.
        status="approved" if side == "sell" else "pending",
        reference_price=_q(price),
        target_weight=_q(target),
        entry_orders=[order],
        stop_price=stop_price,
        worst_case_loss=worst_case,
        est_fees=est_fees,
        rationale=rationale,
        expires_at=clock.now() + APPROVAL_EXPIRY,
    )
