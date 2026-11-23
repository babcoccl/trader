"""The shadow book (spec G3): a hypothetical $10,000 account per sleeve.

``buy`` and ``sell`` record filled-at-once trades with fee and slippage,
all in ``Decimal``. ``summary`` values the book at given prices and
``mark`` stores the day's row in ``shadow_marks``. Nothing in this module
touches plans, orders, fills, balances or equity snapshots.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.ledger.models import ShadowMark, ShadowTrade

START_USD = Decimal("10000")


@dataclass(frozen=True)
class Summary:
    cash: Decimal
    exposure: Decimal
    equity: Decimal
    open_count: int
    closed_count: int
    fees: Decimal  # all entry and exit fees ever paid


def _dec(x: float | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def buy(
    session: Session,
    sleeve: str,
    symbol: str,
    now: datetime,
    price: Decimal,
    spend_usd: Decimal,
    fee_rate: float,
    slippage: float,
    details: dict[str, Any] | None = None,
) -> ShadowTrade:
    """Record a buy of ``spend_usd`` dollars of ``symbol`` at ``price``.

    Fill price is ``price * (1 + slippage)``; the fee is charged on the
    dollars spent, so ``quantity = (spend - fee) / fill price`` and
    ``cost_usd`` is the full ``spend_usd``.
    """
    fill = price * (1 + _dec(slippage))
    fee = spend_usd * _dec(fee_rate)
    trade = ShadowTrade(
        sleeve=sleeve,
        symbol=symbol,
        opened_at=now,
        entry_price=fill,
        quantity=(spend_usd - fee) / fill,
        cost_usd=spend_usd,
        entry_fee_usd=fee,
        details=details or {},
    )
    session.add(trade)
    session.flush()
    return trade


def sell(
    session: Session,
    trade: ShadowTrade,
    now: datetime,
    price: Decimal,
    fee_rate: float,
    slippage: float,
    reason: str,
) -> ShadowTrade:
    """Close ``trade`` at ``price * (1 - slippage)`` and store the result."""
    if trade.closed_at is not None:
        raise ValueError(f"shadow trade {trade.id} is already closed")
    fill = price * (1 - _dec(slippage))
    gross = trade.quantity * fill
    fee = gross * _dec(fee_rate)
    trade.closed_at = now
    trade.exit_price = fill
    trade.exit_fee_usd = fee
    trade.pnl_usd = gross - fee - trade.cost_usd
    trade.exit_reason = reason
    session.flush()
    return trade


def open_trades(
    session: Session, sleeve: str, symbol: str | None = None
) -> list[ShadowTrade]:
    stmt = (
        select(ShadowTrade)
        .where(ShadowTrade.sleeve == sleeve, ShadowTrade.closed_at.is_(None))
        .order_by(ShadowTrade.id)
    )
    if symbol is not None:
        stmt = stmt.where(ShadowTrade.symbol == symbol)
    return list(session.scalars(stmt))


def summary(session: Session, sleeve: str, prices: dict[str, Decimal]) -> Summary:
    """Value the sleeve's book at ``prices`` (used for open positions)."""
    trades = list(
        session.scalars(select(ShadowTrade).where(ShadowTrade.sleeve == sleeve))
    )
    cash = START_USD
    exposure = Decimal("0")
    fees = Decimal("0")
    open_count = 0
    closed_count = 0
    for trade in trades:
        fees += trade.entry_fee_usd
        if trade.closed_at is None:
            open_count += 1
            cash -= trade.cost_usd
            price = prices.get(trade.symbol, trade.entry_price)
            exposure += trade.quantity * price
        else:
            closed_count += 1
            cash += trade.pnl_usd or Decimal("0")
            fees += trade.exit_fee_usd or Decimal("0")
    return Summary(
        cash=cash,
        exposure=exposure,
        equity=cash + exposure,
        open_count=open_count,
        closed_count=closed_count,
        fees=fees,
    )


def mark(
    session: Session,
    sleeve: str,
    day: date,
    prices: dict[str, Decimal],
    note: str,
    details: dict[str, Any] | None = None,
) -> ShadowMark:
    """Store the sleeve's mark for ``day``; an existing row is returned."""
    existing = session.scalars(
        select(ShadowMark).where(ShadowMark.sleeve == sleeve, ShadowMark.day == day)
    ).first()
    if existing is not None:
        return existing
    s = summary(session, sleeve, prices)
    row = ShadowMark(
        sleeve=sleeve,
        day=day,
        equity_usd=s.equity,
        cash_usd=s.cash,
        exposure_usd=s.exposure,
        open_trades=s.open_count,
        closed_trades=s.closed_count,
        note=note,
        details=details or {},
    )
    session.add(row)
    session.flush()
    return row


def marked(session: Session, sleeve: str, day: date) -> bool:
    """True when the sleeve already has a mark for ``day``."""
    return (
        session.scalars(
            select(ShadowMark.id)
            .where(ShadowMark.sleeve == sleeve, ShadowMark.day == day)
            .limit(1)
        ).first()
        is not None
    )
