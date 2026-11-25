"""Paper execution adapter (spec 2.4).

Simulated fills against real 5-minute candles from the ``candles`` table,
through the ``ExecutionAdapter`` interface the live adapter will use.
The adapter is database-backed, so a restart loses nothing: open ``orders``
rows are the book, and a filled order leaves the open set exactly once.

Fill rules, applied to each new closed 5m candle in open-time order:

- buy limit fills at its price when ``candle.low < price * (1 - 0.0001)``;
- sell limit fills at its price when ``candle.high > price * (1 + 0.0001)``;
- market order fills at the candle open with slippage (taker fee);
- stop sell fills at ``min(open, stop) * (1 - slippage)`` when
  ``candle.low <= stop`` (taker fee);
- limit fills pay the maker fee.

A candle only sees orders that existed when it opened
(``open_time >= order.created_at``): nothing fills retroactively inside a
candle that was already trading.

Setting ``paper_assume_taker`` in the ``settings`` table (default false)
makes limit orders never fill as maker; they wait for the executor's time
limit and convert to market, simulating the worst-case cost path.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.backtest.costs import CostModel
from trader.core.clock import Clock, SystemClock
from trader.data.sources.base import ASSETS
from trader.execution.base import LIMIT_TTL, SymbolRules
from trader.ledger import accounting
from trader.ledger.models import Candle, Fill, Order, Plan, Setting

# Price must trade through the limit by this fraction to fill (spec 2.4).
TRADE_THROUGH = Decimal("0.0001")
INTERVAL_SECONDS = 300
_PERIOD = timedelta(seconds=INTERVAL_SECONDS)

# Bitstamp: $10 minimum order; tick and lot come from ASSETS per symbol.
MIN_NOTIONAL = Decimal("10")

_OPEN_STATUSES = ("new", "open", "partially_filled")

# After a collector failover one period can hold candles from both venues;
# the primary (spec 1.1) wins so fills and prices see one candle per period.
PRIMARY_SOURCE = "bitstamp"


def _fill_rank(order: Order) -> int:
    """Within a candle: market sells fill at the open (0), market buys next
    (1); limit and stop fills happen at an unknown intra-candle time (2)."""
    if order.type == "market":
        return 0 if order.side == "sell" else 1
    return 2


class PaperAdapter:
    """Simulated venue driven by stored candles and the ``orders`` table."""

    def __init__(
        self,
        session: Session,
        cost: CostModel,
        clock: Clock | None = None,
        mode: str = "paper",
    ) -> None:
        self._session = session
        self._cost = cost
        self._clock = clock or SystemClock()
        self._mode = mode

    # -- ExecutionAdapter ---------------------------------------------------

    def place(self, order: Order) -> str:
        order.venue = "paper"
        order.venue_order_id = f"paper-{order.client_order_id}"
        order.status = "open"
        self._session.flush()
        return order.venue_order_id

    def cancel(self, venue_order_id: str) -> bool:
        order = self._by_venue_id(venue_order_id)
        if order is None or order.status not in _OPEN_STATUSES:
            return False
        order.status = "cancelled"
        self._session.flush()
        return True

    def open_orders(self) -> list[Order]:
        return list(
            self._session.scalars(
                select(Order)
                .where(Order.venue == "paper", Order.mode == self._mode)
                .where(Order.status.in_(_OPEN_STATUSES))
                .order_by(Order.id)
            )
        )

    def poll_fills(self, since: datetime) -> list[Fill]:
        """Simulate every open order against candles opened after ``since``.

        Returns the new ``Fill`` objects, not yet added to the session.
        Order within a candle: market sells first (they provably fill at
        the open and free the cash), then market buys, then limit/stop
        fills in order-id order — a limit sell cannot fund a same-candle
        buy because its trade-through could print after the open. Only
        candles closed by the Clock count, and a limit earns nothing on a
        candle that closed after its plan's deadline.
        Idempotent: an order that fills leaves the open set, so a
        replayed ``since`` window never fills it twice.
        """
        assume_taker = self._assume_taker()
        symbols = {o.symbol for o in self.open_orders()}
        candles = self._candles_after(symbols, since)
        candidates: list[tuple[datetime, int, Order, Decimal, str]] = []
        for order in self.open_orders():
            deadline = self._limit_deadline(order)
            for candle in candles.get(order.symbol, []):
                if candle.open_time < order.created_at:
                    continue  # order did not exist when this candle opened
                if deadline is not None and candle.open_time + _PERIOD > deadline:
                    break  # closed after the deadline: converts to market
                result = self._fill_price(order, candle, assume_taker)
                if result is not None:
                    price, liquidity = result
                    candidates.append(
                        (candle.open_time, order.id, order, price, liquidity)
                    )
                    break  # full fill only; order is done
        candidates.sort(key=lambda c: (c[0], _fill_rank(c[2]), c[1]))
        new_fills: list[Fill] = []
        for time, _, order, price, liquidity in candidates:
            new_fills.append(self._make_fill(order, time, price, liquidity))
            order.status = "filled"
        self._session.flush()
        return new_fills

    def balances(self) -> dict[str, Decimal]:
        state = accounting.positions(self._session, self._mode)
        state["USD"] = accounting.cash(self._session, self._mode)
        return state

    def symbol_rules(self, symbol: str) -> SymbolRules:
        asset = ASSETS[symbol]
        return SymbolRules(tick=asset.tick, lot=asset.lot, min_notional=MIN_NOTIONAL)

    # -- paper-only helpers ---------------------------------------------------

    def fill_now(
        self, order: Order, price: Decimal, time: datetime, maker: bool = False
    ) -> Fill:
        """Fill an order immediately at ``price`` with taker fee and slippage
        already applied by the caller. Used for emergency-stop and flatten
        market sells, which cannot wait for the next candle. ``maker=True``
        charges the maker fee instead (ping-pong's maker-only quotes). The
        returned fill is transient: the caller adds it to the session."""
        fill = self._make_fill(order, time, price, "maker" if maker else "taker")
        order.status = "filled"
        return fill

    def _limit_deadline(self, order: Order) -> datetime | None:
        """The approval-time + ``LIMIT_TTL`` deadline for a limit order,
        or None for other types. Candles that opened after it belong to
        the market order the expired limit converts into."""
        if order.type != "limit" or order.shadow:
            return None  # shadow orders never convert; they wait for their print
        plan = self._session.get(Plan, order.plan_id)
        if plan is None:
            return None
        approved_at = plan.decided_at or plan.created_at
        return approved_at + LIMIT_TTL

    def market_sell_price(self, price: Decimal) -> Decimal:
        """Market sell fill price: worse than the mark by the slippage."""
        return price * (1 - Decimal(str(self._cost.slippage)))

    # -- internals ----------------------------------------------------------

    def _by_venue_id(self, venue_order_id: str) -> Order | None:
        return self._session.scalars(
            select(Order).where(
                Order.venue == "paper",
                Order.venue_order_id == venue_order_id,
                Order.mode == self._mode,
            )
        ).first()

    def _assume_taker(self) -> bool:
        row = self._session.get(Setting, "paper_assume_taker")
        return bool(row.value) if row is not None else False

    def _candles_after(
        self, symbols: set[str], since: datetime
    ) -> dict[str, list[Candle]]:
        out: dict[str, list[Candle]] = {}
        for symbol in symbols:
            rows = self._session.scalars(
                select(Candle)
                .where(
                    Candle.symbol == symbol,
                    Candle.interval == "5m",
                    Candle.open_time > since,
                    # Closed periods only, by the Clock (spec 1.1 rule).
                    Candle.open_time + _PERIOD <= self._clock.now(),
                )
                .order_by(Candle.open_time, Candle.source != PRIMARY_SOURCE)
            )
            per_period: dict[datetime, Candle] = {}
            for row in rows:
                per_period.setdefault(row.open_time, row)
            out[symbol] = list(per_period.values())
        return out

    def _fill_price(
        self, order: Order, candle: Candle, assume_taker: bool
    ) -> tuple[Decimal, str] | None:
        """(price, liquidity) when ``order`` fills on ``candle``, else None."""
        price: Decimal | None = None
        liquidity = "taker"
        if order.type == "limit" and not assume_taker:
            assert order.price is not None
            if order.side == "buy" and candle.low < order.price * (1 - TRADE_THROUGH):
                price = order.price
            elif order.side == "sell" and candle.high > order.price * (
                1 + TRADE_THROUGH
            ):
                price = order.price
            liquidity = "maker"
        elif order.type == "market":
            slip = Decimal(str(self._cost.slippage))
            if order.side == "buy":
                price = candle.open * (1 + slip)
            else:
                price = candle.open * (1 - slip)
        elif order.type == "stop" and order.side == "sell":
            assert order.stop_price is not None
            if candle.low <= order.stop_price:
                price = min(candle.open, order.stop_price) * (
                    1 - Decimal(str(self._cost.slippage))
                )
        if price is None:
            return None
        return price, liquidity

    def _make_fill(
        self, order: Order, time: datetime, price: Decimal, liquidity: str
    ) -> Fill:
        """A transient Fill row; the caller adds it to the session."""
        fee_rate = (
            self._cost.maker_fee if liquidity == "maker" else self._cost.taker_fee
        )
        fill = Fill(
            order_id=order.id,
            time=time,
            price=price,
            quantity=order.quantity,
            fee=price * order.quantity * Decimal(str(fee_rate)),
            fee_currency=order.symbol.split("-", 1)[1],
            liquidity=liquidity,
        )
        return fill
