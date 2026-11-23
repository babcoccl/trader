"""Shared helpers for the ``/api`` routes (spec 3.1)."""

from collections.abc import Iterable, Iterator
from decimal import Decimal

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.api.schemas import OrderOut
from trader.core.db import session_scope
from trader.data.sources.base import SYMBOLS
from trader.execution.paper import PRIMARY_SOURCE
from trader.ledger.models import Candle, Order


def get_session() -> Iterator[Session]:
    """Request-scoped session; commits on success, rolls back on error."""
    with session_scope() as session:
        yield session


# Module-level singleton so route defaults don't call Depends() inline (B008).
SessionDep = Depends(get_session)


def latest_prices(
    session: Session, symbols: Iterable[str] = SYMBOLS
) -> dict[str, Decimal]:
    """Newest stored 5m close per symbol (primary source first). Pass
    ``symbols`` to mark a symbol no longer in ``TRADER_SYMBOLS``."""
    prices: dict[str, Decimal] = {}
    for symbol in symbols:
        close = session.scalar(
            select(Candle.close)
            .where(Candle.symbol == symbol, Candle.interval == "5m")
            .order_by(Candle.open_time.desc(), Candle.source != PRIMARY_SOURCE)
            .limit(1)
        )
        if close is not None:
            prices[symbol] = close
    return prices


def order_out(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id,
        client_order_id=order.client_order_id,
        side=order.side,
        type=order.type,
        price=order.price,
        stop_price=order.stop_price,
        quantity=order.quantity,
        status=order.status,
        created_at=order.created_at,
    )
