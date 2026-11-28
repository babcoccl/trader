"""Daily backtest engine.

Reproduces research/bt.py exactly on the committed fixture: weights decided
at the close of day t are applied at the open of day t+1, subject to the
trading band, with ``taker_fee + slippage`` charged on traded notional.
"""

import math
from dataclasses import dataclass, field
from typing import Literal, TypedDict

import numpy as np
import pandas as pd

from trader.backtest.costs import CostModel
from trader.strategy import ensemble, sizing

ANN = 365


def load_fixture(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the fixture CSV into (opens, closes) indexed by date.

    Columns are ``<symbol>_open`` and ``<symbol>_close``; returned frames have
    one column per symbol (e.g. ``BTC-USD``).
    """
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    symbols = [c[: -len("_open")] for c in d.columns if c.endswith("_open")]
    opens = d[[f"{s}_open" for s in symbols]]
    closes = d[[f"{s}_close" for s in symbols]]
    opens.columns = symbols
    closes.columns = symbols
    return opens, closes


def _target_weights(closes: pd.DataFrame, vol_target: float) -> pd.DataFrame:
    """Target weight per symbol for every date, from that day's close."""
    signals = {s: ensemble.signal(closes[s]) for s in closes.columns}
    vols = {s: sizing.annual_vol(closes[s]) for s in closes.columns}
    w = pd.DataFrame(index=closes.index, columns=closes.columns, dtype=float)
    for day in closes.index:
        w.loc[day] = sizing.target_weights(
            {s: signals[s][day] for s in closes.columns},
            {s: vols[s][day] for s in closes.columns},
            vol_target,
        )
    return w


class TradeFill(TypedDict):
    """One executed fill, in normalized (capital=1) units."""

    date: pd.Timestamp
    symbol: str
    side: Literal["BUY", "SELL"]
    size: float
    price: float
    pnl: float
    fee: float


@dataclass
class BacktestResult:
    """Daily series produced by ``run`` plus summary statistics."""

    equity: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    exposure: pd.Series = field(repr=False)
    # Fills captured at the open: date, symbol, side, size (units), price,
    # realized pnl (avg-cost basis), fee — all in normalized (capital=1) terms.
    trades: list["TradeFill"] = field(default_factory=list, repr=False)

    def stats(
        self, start: str | None = None, end: str | None = None
    ) -> dict[str, float]:
        """CAGR, max drawdown, Sharpe, annual turnover, mean exposure.

        ``years`` is row count / 365, so a sliced equity is measured against
        the slice's own length. Sharpe uses pandas' sample std of daily
        returns.
        """
        e = self.equity.loc[start:end]
        t = self.turnover.loc[start:end]
        x = self.exposure.loc[start:end]
        years = len(e) / ANN
        ret = e.pct_change().dropna()
        return {
            "cagr": (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1,
            "maxdd": (e / e.cummax() - 1).min(),
            "sharpe": ret.mean() / ret.std() * math.sqrt(ANN),
            "turnover_yr": t.sum() / years,
            "exposure": x.mean(),
        }


def run(
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    cost: CostModel,
    vol_target: float = 0.40,
    band: float = 0.20,
    start: str = "2018-04-01",
    end: str = "2026-11-19",
) -> BacktestResult:
    """Run the daily backtest over ``closes.loc[start:end]``.

    Weights are computed for every date in ``closes`` first (weights used on
    day t are those of day t-1, 0 if none), so the start date can sit inside
    a longer history.
    """
    return run_weights(
        opens, closes, _target_weights(closes, vol_target), cost, band, start, end
    )


def run_weights(
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    weights: pd.DataFrame,
    cost: CostModel,
    band: float = 0.20,
    start: str = "2018-04-01",
    end: str = "2026-11-19",
) -> BacktestResult:
    """Run the daily backtest with caller-supplied target weights.

    ``weights`` holds one target-weight row per date of ``closes``; the
    weights used on day t are those of day t-1 (shifted here, 0 if none),
    so the caller passes unshifted same-day weights.
    """
    idx = closes.loc[start:end].index
    tgt = weights.shift(1).reindex(idx).fillna(0.0)

    symbols = list(closes.columns)
    units = {s: 0.0 for s in symbols}
    cash = 1.0
    equity = pd.Series(index=idx, dtype=float)
    turnover = pd.Series(0.0, index=idx)
    exposure = pd.Series(index=idx, dtype=float)
    fee_rate = cost.taker_fee + cost.slippage
    trades: list[TradeFill] = []
    basis = {s: 0.0 for s in symbols}

    for day in idx:
        o = opens.loc[day].astype(float)
        e = cash + sum(units[s] * o[s] for s in symbols if not math.isnan(o[s]))
        traded = 0.0
        new_units = dict(units)
        for s in symbols:
            if math.isnan(o[s]):  # not yet listed: weight 0, never traded
                continue
            cur = units[s] * o[s] / e
            t = float(tgt.loc[day, s])
            if sizing.should_trade(t, cur, band):
                traded += abs(t - cur) * e
                new_units[s] = t * e / o[s]
        if traded > 0:
            fee_paid = traded * fee_rate
            for s in symbols:
                if math.isnan(o[s]):
                    continue
                cur = units[s] * o[s] / e
                t = float(tgt.loc[day, s])
                if not sizing.should_trade(t, cur, band):
                    continue
                notional = abs(t - cur) * e
                qty = notional / o[s]
                prev_units = units[s]
                side: Literal["BUY", "SELL"]
                if t > cur:
                    side = "BUY"
                    if prev_units >= 0:
                        pnl = 0.0
                        basis[s] = (basis[s] * prev_units + notional) / (
                            prev_units + qty
                        )
                    else:
                        # Covering a short: pnl vs the short basis.
                        cover = min(qty, -prev_units)
                        pnl = (basis[s] - o[s]) * cover
                        # Any remainder flips long at today's open.
                        basis[s] = o[s] if qty - cover > 1e-12 else basis[s]
                        if abs(prev_units + qty) <= 1e-12:
                            basis[s] = 0.0
                else:
                    side = "SELL"
                    if prev_units > 0:
                        sold = min(qty, prev_units)
                        pnl = (o[s] - basis[s]) * sold
                        qty = sold
                        # Flipping to a short opens it at today's open.
                        basis[s] = (
                            o[s]
                            if qty < notional / o[s] - 1e-12
                            else (0.0 if prev_units - sold <= 1e-12 else basis[s])
                        )
                    else:
                        # Adding to a short: extend the short basis.
                        pnl = 0.0
                        basis[s] = (basis[s] * -prev_units + notional) / (
                            -prev_units + qty
                        )
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
        c = closes.loc[day].astype(float)
        value = sum(units[s] * c[s] for s in symbols if not math.isnan(c[s]))
        equity[day] = cash + value
        exposure[day] = value / equity[day] if equity[day] != 0 else np.nan

    return BacktestResult(
        equity=equity,
        weights=tgt,
        turnover=turnover,
        exposure=exposure,
        trades=trades,
    )


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR of an intraday OHLC frame."""
    prev_close = bars["close"].shift(1)
    tr = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def run_intraday_flush_mr(
    bars: dict[str, pd.DataFrame],
    cost: CostModel,
    atr_period: int = 14,
    entry_atr_mult: float = 2.5,
    target_atr_mult: float = 1.0,
    max_hold_bars: int = 6,
    crash_veto_24h: float = -0.12,
    max_weight: float = 0.25,
    trade_through_bps: float = 5.0,
    bars_per_day: int = 6,
) -> BacktestResult:
    """Maker-only flush mean-reversion on hourly resampled bars.

    At each bar T the entry limit and crash veto use only bars up to T-1.
    A resting buy sits at ``prev_close - entry_atr_mult * ATR`` unless the
    trailing 24h return is under ``crash_veto_24h``; it fills when the bar's
    low trades through it by ``trade_through_bps``. A maker take-profit sits
    at ``fill + target_atr_mult * ATR``; the position is closed at market
    after ``max_hold_bars``. Maker fills pay ``maker_fee`` with no slippage;
    taker exits pay ``taker_fee + slippage``. Positions cap at
    ``max_weight`` of equity each. ``bars_per_day`` is the resampled
    frequency's bars in 24h (4h -> 6, 1h -> 24).
    """
    tt = trade_through_bps / 10_000
    idx = next(iter(bars.values())).index
    atr = {s: _atr(bars[s], atr_period) for s in bars}
    lookback = bars_per_day  # bars in 24h for the crash veto

    cash = 1.0
    positions: dict[str, dict[str, float]] = {}
    equity = pd.Series(index=idx, dtype=float)
    turnover = pd.Series(0.0, index=idx)
    exposure = pd.Series(index=idx, dtype=float)
    trades: list[TradeFill] = []

    prev: dict[str, pd.Series] = {}
    weights_rows: dict[pd.Timestamp, dict[str, float]] = {}
    for i, ts in enumerate(idx):
        # exits: TP limit, then max-hold taker close
        for s, pos in list(positions.items()):
            bar = bars[s].loc[ts]
            held = i - int(pos["i"])
            if bar["high"] >= pos["tp"] * (1 + tt):
                notional = pos["units"] * pos["tp"]
                fee = notional * cost.maker_fee
                cash += notional - fee
                trades.append(
                    TradeFill(
                        date=ts,
                        symbol=s,
                        side="SELL",
                        size=pos["units"],
                        price=pos["tp"],
                        pnl=(pos["tp"] - pos["basis"]) * pos["units"],
                        fee=fee,
                    )
                )
                turnover[ts] += notional
                del positions[s]
            elif held >= max_hold_bars:
                price = float(bar["close"])
                notional = pos["units"] * price
                fee = notional * (cost.taker_fee + cost.slippage)
                cash += notional - fee
                trades.append(
                    TradeFill(
                        date=ts,
                        symbol=s,
                        side="SELL",
                        size=pos["units"],
                        price=price,
                        pnl=(price - pos["basis"]) * pos["units"],
                        fee=fee,
                    )
                )
                turnover[ts] += notional
                del positions[s]

        e = cash + sum(
            p["units"] * float(bars[s].loc[ts, "close"]) for s, p in positions.items()
        )

        # entries: limit placed from bar T-1 data, filled inside bar T
        for s, frame in bars.items():
            if s in positions or s not in prev:
                continue
            atr_prev = atr[s].iloc[i - 1]
            if pd.isna(atr_prev) or i <= lookback:
                continue
            ret_24h = prev[s]["close"] / frame["close"].iloc[i - 1 - lookback] - 1.0
            if ret_24h < crash_veto_24h:
                continue
            limit = prev[s]["close"] - entry_atr_mult * atr_prev
            bar = bars[s].loc[ts]
            if bar["low"] <= limit * (1 - tt):
                notional = min(max_weight * e, cash)
                if notional <= 0:
                    continue
                fee = notional * cost.maker_fee
                units = (notional - fee) / limit
                cash -= notional
                positions[s] = {
                    "units": units,
                    "basis": limit,
                    "tp": limit + target_atr_mult * atr_prev,
                    "i": float(i),
                }
                trades.append(
                    TradeFill(
                        date=ts,
                        symbol=s,
                        side="BUY",
                        size=units,
                        price=limit,
                        pnl=0.0,
                        fee=fee,
                    )
                )
                turnover[ts] += notional

        equity[ts] = cash + sum(
            p["units"] * float(bars[s].loc[ts, "close"]) for s, p in positions.items()
        )
        exposure[ts] = (equity[ts] - cash) / equity[ts] if equity[ts] else np.nan
        weights_rows[ts] = {
            s: (
                p["units"] * float(bars[s].loc[ts, "close"]) / equity[ts]
                if (p := positions.get(s)) is not None and equity[ts]
                else 0.0
            )
            for s in bars
        }
        prev = {s: bars[s].loc[ts] for s in bars}

    weights = pd.DataFrame.from_dict(weights_rows, orient="index").fillna(0.0)
    return BacktestResult(
        equity=equity,
        weights=weights,
        turnover=turnover / equity,
        exposure=exposure,
        trades=trades,
    )
