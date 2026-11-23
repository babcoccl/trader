"""The funding shadow sleeve (spec G4).

Holds a quarter of its hypothetical book while the regime is a downtrend
and the 7-day average of Coinbase's hourly perp funding rate is negative.
Sells when the trend leaves downtrend or the average turns non-negative.
It logs a book, a mark and a note only — never an order.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock
from trader.data.sources.coinbase_funding import VENUE
from trader.ledger.models import FundingRate, ShadowMark
from trader.shadow import book, data
from trader.strategy import sleeves

NAME = "funding"
SYMBOLS = ("BTC-USD", "ETH-USD")
WINDOW_DAYS = 7
MIN_READINGS = 126  # three quarters of the 168 hours in a week
DESCRIPTION = (
    "Holds 25% of its book while the regime is a downtrend and the "
    "7-day average funding rate is negative. Sells when the trend "
    "leaves downtrend or funding turns non-negative."
)


def average(
    session: Session, symbol: str, today: datetime
) -> tuple[Decimal | None, int]:
    """Mean ``rate`` and row count for ``symbol`` on ``VENUE`` over the
    ``WINDOW_DAYS`` days before ``today``. The mean is ``None`` when the
    count is under ``MIN_READINGS``."""
    result = session.execute(
        select(func.count(), func.sum(FundingRate.rate)).where(
            FundingRate.symbol == symbol,
            FundingRate.venue == VENUE,
            FundingRate.time >= today - timedelta(days=WINDOW_DAYS),
            FundingRate.time < today,
        )
    ).one()
    count = result[0]
    if count < MIN_READINGS:
        return None, count
    return result[1] / count, count


def _code(symbol: str) -> str:
    return symbol.split("-")[0]


def _pct(rate: Decimal) -> str:
    """A per-hour rate as a signed percent, e.g. ``-0.0011%``."""
    return f"{rate * 100:+.4f}%"


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

    spec = sleeves.get(NAME)
    note: dict[str, str] = {}
    for symbol in active:
        code = _code(symbol)
        avg, count = average(session, symbol, today)
        regime = regimes[symbol]
        open_trade = next((t for t in book.open_trades(session, NAME, symbol)), None)
        on = sleeves.allowed(spec, regime) and avg is not None and avg < 0
        if avg is None:
            note[symbol] = (
                f"{code}: collecting funding rates, {count} of "
                f"{MIN_READINGS} hourly readings."
            )
            continue
        if open_trade is not None and not on:
            reason = (
                "trend no longer off"
                if not sleeves.allowed(spec, regime)
                else "funding no longer negative"
            )
            trade = book.sell(
                session,
                open_trade,
                now,
                prices[symbol],
                BITSTAMP.taker_fee,
                BITSTAMP.slippage,
                reason,
            )
            pnl = trade.pnl_usd or Decimal("0")
            note[symbol] = (
                f"{code}: sold, {reason}, result "
                f"{'-' if pnl < 0 else ''}${abs(pnl):,.2f}."
            )
            continue
        if open_trade is not None:
            note[symbol] = (
                f"{code}: downtrend and 7-day funding is {_pct(avg)} an hour; holding."
            )
            continue
        if not sleeves.allowed(spec, regime):
            trend = regime.trend if regime is not None else "unknown"
            note[symbol] = f"{code}: {trend}, so it holds nothing."
            continue
        if not on:
            note[symbol] = (
                f"{code}: downtrend but 7-day funding is {_pct(avg)} an "
                "hour; no position."
            )
            continue
        summary = book.summary(session, NAME, prices)
        spend = min(Decimal("0.25") * summary.equity, summary.cash)
        trade = book.buy(
            session,
            NAME,
            symbol,
            now,
            prices[symbol],
            spend,
            BITSTAMP.taker_fee,
            BITSTAMP.slippage,
            details={
                "avg_rate_7d": str(avg),
                "readings": count,
                "regime": regime.trend if regime else None,
            },
        )
        note[symbol] = (
            f"{code}: downtrend and 7-day funding is {_pct(avg)} an "
            f"hour; bought ${spend:,.2f} at {trade.entry_price:,.2f}."
        )
    return book.mark(
        session, NAME, today.date(), prices, " ".join(note[s] for s in active)
    )
