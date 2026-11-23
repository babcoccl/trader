"""The dip-buying shadow sleeve (spec G3).

Buys 25% of its hypothetical book when the trend rules are mixed (3 to 6
of 9 on) and the daily close is 2 standard deviations under its 20-day
average. Sells 5 days later. It logs a book, a mark and a note only —
never an order.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock
from trader.ledger.models import ShadowMark
from trader.shadow import book, data
from trader.strategy import sleeves

NAME = "dip"
SYMBOLS = ("BTC-USD", "ETH-USD")
Z_WINDOW = 20
Z_ENTRY = -2.0
HOLD_DAYS = 5
MIN_SPEND_USD = Decimal("10")
DESCRIPTION = (
    "Buys 25% of its book when the trend rules are mixed (3 to 6 of 9) "
    "and the daily close is 2 standard deviations under its 20-day "
    "average. Sells 5 days later."
)


def zscore(close: pd.Series) -> float:
    """``(close[-1] - mean(last 20)) / std(last 20, ddof=1)``; NaN when
    fewer than ``Z_WINDOW`` closes or the deviation is zero."""
    window = close.iloc[-Z_WINDOW:]
    if len(window) < Z_WINDOW:
        return float("nan")
    sd = float(window.std(ddof=1))
    if sd == 0:
        return float("nan")
    return float((window.iloc[-1] - float(window.mean())) / sd)


def _code(symbol: str) -> str:
    return symbol.split("-")[0]


def run(session: Session, clock: Clock) -> ShadowMark | None:
    """One daily shadow run; ``None`` when the day already has a mark or
    the inputs (today's regime, a fresh price) are not there yet."""
    now = clock.now()
    today = datetime(now.year, now.month, now.day, tzinfo=UTC)
    if book.marked(session, NAME, today.date()):
        return None

    active = data.symbols(SYMBOLS)
    if not active:
        return None
    regimes = {s: data.regime_today(session, s, today) for s in active}
    if any(r is None for r in regimes.values()):
        return None
    price_map = {s: data.price(session, s, now) for s in active}
    if any(p is None for p in price_map.values()):
        return None
    prices = {s: p for s, p in price_map.items() if p is not None}

    notes: list[str] = []
    open_now: dict[str, bool] = {}
    for symbol in active:
        open_trade = next((t for t in book.open_trades(session, NAME, symbol)), None)
        if open_trade is None:
            open_now[symbol] = False
            continue
        sell_day = open_trade.opened_at.date() + timedelta(days=HOLD_DAYS)
        if sell_day <= today.date():
            trade = book.sell(
                session,
                open_trade,
                now,
                prices[symbol],
                BITSTAMP.taker_fee,
                BITSTAMP.slippage,
                f"held {HOLD_DAYS} days",
            )
            open_now[symbol] = False
            pnl = trade.pnl_usd or Decimal("0")
            notes.append(
                f"{_code(symbol)}: sold after {HOLD_DAYS} days, "
                f"result {'-' if pnl < 0 else ''}"
                f"${abs(pnl):,.2f}."
            )
        else:
            open_now[symbol] = True
            notes.append(
                f"{_code(symbol)}: holding since "
                f"{open_trade.opened_at.date()}, sells on {sell_day}."
            )

    for symbol in active:
        if open_now[symbol]:
            continue
        price = prices[symbol]
        regime = regimes[symbol]
        z = zscore(data.daily_closes(session, symbol, today))
        if not sleeves.allowed(sleeves.get(NAME), regime):
            trend = regime.trend if regime is not None else "unknown"
            notes.append(
                f"{_code(symbol)}: {trend}, so no dip-buying (it needs a mixed regime)."
            )
            continue
        if pd.isna(z):
            notes.append(
                f"{_code(symbol)}: mixed, too few days for a 20-day "
                "average; it buys at 2.0."
            )
            continue
        if not (z < Z_ENTRY):
            notes.append(
                f"{_code(symbol)}: mixed, close is {abs(z):.1f} standard "
                f"deviations {'under' if z < 0 else 'over'} its 20-day "
                f"average; it buys at 2.0."
            )
            continue
        summary = book.summary(session, NAME, prices)
        spend = min(Decimal("0.25") * summary.equity, summary.cash)
        if spend < MIN_SPEND_USD:
            notes.append(
                f"{_code(symbol)}: dip signal (z {z:.2f}) but "
                f"${spend:.2f} is under the $10 minimum."
            )
            continue
        trade = book.buy(
            session,
            NAME,
            symbol,
            now,
            price,
            spend,
            BITSTAMP.taker_fee,
            BITSTAMP.slippage,
            details={"zscore": z, "regime": regime.trend if regime else None},
        )
        notes.append(
            f"{_code(symbol)}: bought ${spend:,.2f} at {trade.entry_price:,.2f}."
        )
    return book.mark(session, NAME, today.date(), prices, " ".join(notes))
