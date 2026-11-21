"""Execution adapter interface (spec 2.4).

The paper and (later, spec 5.2) live adapters share this protocol so the
executor loop never knows which venue it talks to. Orders and fills are the
ORM rows from spec 0.3; adapters own their ``status`` transitions.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from trader.ledger.models import Fill, Order


@dataclass(frozen=True)
class SymbolRules:
    """Venue trading rules for one symbol: price tick, quantity lot and the
    smallest allowed order notional, all in the venue's units."""

    tick: Decimal
    lot: Decimal
    min_notional: Decimal


class ExecutionAdapter(Protocol):
    """One venue's order operations."""

    def place(self, order: Order) -> str:
        """Rest or send ``order``; returns its venue order id."""
        ...

    def cancel(self, venue_order_id: str) -> bool:
        """Cancel a resting order. False when it was not open."""
        ...

    def open_orders(self) -> list[Order]:
        """Every order the venue still considers working."""
        ...

    def poll_fills(self, since: datetime) -> list[Fill]:
        """New fills for orders working against candles after ``since``."""
        ...

    def balances(self) -> dict[str, Decimal]:
        """Asset -> quantity held at the venue."""
        ...

    def symbol_rules(self, symbol: str) -> SymbolRules:
        """Trading rules for ``symbol``."""
        ...
