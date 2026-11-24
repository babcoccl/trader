"""Ping-pong subsidy sleeve: capped maker-only sideways volume booster.

Wraps the daily ``engine.run_weights`` loop. On top of the alpha sleeve's
rebalances it simulates, on low-ADX days, a maker-only ping-pong strategy
that adds traded volume toward Coinbase Advanced fee tiers. Fees are looked
up each day from the rolling 30-day USD notional (alpha + booster); the
booster runs only while the rolling volume is below the tier target and
pays ``maker_fee`` against a ``spread_capture_bps`` edge.
"""

import math
from collections import deque
from typing import Literal

import numpy as np
import pandas as pd

from trader.backtest import engine
from trader.strategy import regime, sizing

ADX_SIDEWAYS = 20.0
ROLLING_WINDOW_DAYS = 30


def coinbase_fee_rate(trailing_30d_vol_usd: float) -> tuple[float, float]:
    """Coinbase Advanced (maker, taker) fee for a trailing 30d USD volume."""
    if trailing_30d_vol_usd < 10_000:
        return 0.0040, 0.0060
    if trailing_30d_vol_usd < 50_000:
        return 0.0025, 0.0040
    return 0.0015, 0.0025


def run_with_ping_pong_subsidy(
    opens: pd.DataFrame,
    highs: pd.DataFrame,
    lows: pd.DataFrame,
    closes: pd.DataFrame,
    target_weights: pd.DataFrame,
    starting_capital: float,
    start: str,
    end: str,
    *,
    band: float = 0.20,
    slippage: float = 0.0001,
    target_tier_volume: float = 10_000.0,
    daily_booster_cap_usd: float = 2_000.0,
    spread_capture_bps: float = 10.0,
) -> engine.BacktestResult:
    """Run the daily backtest with a sideways maker-volume booster.

    Same loop as ``engine.run_weights``: ``target_weights`` holds unshifted
    same-day weights, used at the next day's open. Each day the fee tier is
    looked up from the trailing 30-day USD notional (alpha rebalance volume
    plus booster volume). When the primary asset's ADX (shifted one day, no
    look-ahead) is below 20 and the rolling volume is under
    ``target_tier_volume``, a capped maker-only boost ``min(
    daily_booster_cap_usd, target_tier_volume - rolling_vol)`` earns
    ``spread_capture_bps`` and pays ``maker_fee``; its net P&L and a summary
    trade entry are recorded.
    """
    idx = closes.loc[start:end].index
    tgt = target_weights.shift(1).reindex(idx).fillna(0.0)

    primary = closes.columns[0]
    # ADX of day t-1 decides day t's boost: no look-ahead.
    adx = regime.adx(highs[primary], lows[primary], closes[primary]).shift(1)

    symbols = list(closes.columns)
    units = {s: 0.0 for s in symbols}
    cash = 1.0
    equity = pd.Series(index=idx, dtype=float)
    turnover = pd.Series(0.0, index=idx)
    exposure = pd.Series(index=idx, dtype=float)
    trades: list[engine.TradeFill] = []
    basis = {s: 0.0 for s in symbols}
    recent_vol: deque[float] = deque(maxlen=ROLLING_WINDOW_DAYS)

    for day in idx:
        rolling_vol = sum(recent_vol)
        maker_fee, taker_fee = coinbase_fee_rate(rolling_vol)
        fee_rate = taker_fee + slippage
        day_vol_usd = 0.0

        o = opens.loc[day].astype(float)
        e = cash + sum(units[s] * o[s] for s in symbols if not math.isnan(o[s]))
        traded = 0.0
        new_units = dict(units)
        for s in symbols:
            if math.isnan(o[s]):
                continue
            cur = units[s] * o[s] / e
            t = float(tgt.loc[day, s])
            if sizing.should_trade(t, cur, band):
                traded += abs(t - cur) * e
                new_units[s] = t * e / o[s]
        if traded > 0:
            fee_paid = traded * fee_rate
            day_vol_usd += traded * starting_capital
            for s in symbols:
                if math.isnan(o[s]):
                    continue
                cur = units[s] * o[s] / e
                t = float(tgt.loc[day, s])
                if not sizing.should_trade(t, cur, band):
                    continue
                notional = abs(t - cur) * e
                qty = notional / o[s]
                side: Literal["BUY", "SELL"]
                if t > cur:
                    side, pnl = "BUY", 0.0
                    prev_units = units[s]
                    basis[s] = (basis[s] * prev_units + notional) / (prev_units + qty)
                else:
                    side = "SELL"
                    qty = min(qty, units[s])
                    pnl = (o[s] - basis[s]) * qty
                    if units[s] - qty <= 1e-12:
                        basis[s] = 0.0
                trades.append(
                    {
                        "date": day,
                        "symbol": s,
                        "side": side,
                        "size": qty,
                        "price": o[s],
                        "pnl": pnl,
                        "fee": fee_paid * notional / traded,
                    }
                )
            units = new_units
            cash = (
                e
                - fee_paid
                - sum(units[s] * o[s] for s in symbols if not math.isnan(o[s]))
            )
            turnover[day] = traded / e

        # Sideways booster: maker-only ping-pong volume while below the tier.
        adx_prev = adx.get(day)
        if (
            adx_prev is not None
            and not pd.isna(adx_prev)
            and adx_prev < ADX_SIDEWAYS
            and rolling_vol < target_tier_volume
        ):
            boost_usd = min(daily_booster_cap_usd, target_tier_volume - rolling_vol)
            # A negative-edge booster burns cash to buy the tier: cap the
            # day's boost at what remaining cash can pay for, so the
            # portfolio cannot be driven below zero.
            edge = (spread_capture_bps / 10_000) - maker_fee
            if edge < 0:
                boost_usd = min(boost_usd, cash * starting_capital / -edge)
            if boost_usd > 0:
                net_booster_pnl = boost_usd * edge
                cash += net_booster_pnl / starting_capital
                day_vol_usd += boost_usd
                trades.append(
                    {
                        "date": day,
                        "symbol": primary,
                        "side": "SELL",
                        "size": 0.0,
                        "price": float(closes.loc[day, primary]),
                        "pnl": net_booster_pnl / starting_capital,
                        "fee": (boost_usd * maker_fee) / starting_capital,
                    }
                )
        recent_vol.append(day_vol_usd)

        c = closes.loc[day].astype(float)
        value = sum(units[s] * c[s] for s in symbols if not math.isnan(c[s]))
        equity[day] = cash + value
        exposure[day] = value / equity[day] if equity[day] != 0 else np.nan

    return engine.BacktestResult(
        equity=equity,
        weights=tgt,
        turnover=turnover,
        exposure=exposure,
        trades=trades,
    )
