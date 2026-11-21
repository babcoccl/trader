"""Ledger and accounting (spec 2.3).

Positions, cash, equity and the high-water mark are derived from fills only.
The ``balances`` table is a derived store: ``apply_fill`` appends a snapshot
row for every asset a fill touches, ``rebuild`` replays every fill and
deposit and rewrites the mode's rows, so the two paths always agree.

Conventions:

- ``balances.asset`` holds a currency code: ``"USD"`` for cash, ``"BTC"`` or
  ``"ETH"`` for positions (the base of the order's ``"<base>-<quote>"``
  symbol).
- Fills on ``orders.shadow = true`` rows never touch balances.
- Selling more of an asset than is held raises ``ValueError``.
- Stored quantities are rounded to 18 decimal places (NUMERIC(38,18)).
- Deposits are recorded as ``events`` rows (``kind="deposit"``) plus a
  ``balances`` row, so ``rebuild`` can replay them.
- ``prices`` maps a symbol like ``"BTC-USD"`` to its USD price.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trader.core.clock import Clock
from trader.ledger.models import Balance, Event, Fill, Order

Q18 = Decimal("1e-18")
CASH = "USD"


def _q(x: Decimal) -> Decimal:
    """Round to the storage scale: NUMERIC(38,18) rounds half away from
    zero, so matching it here keeps apply_fill and rebuild identical."""
    return x.quantize(Q18, rounding=ROUND_HALF_UP)


@dataclass
class Portfolio:
    """Satisfies ``engine.daily_cycle.PortfolioView``."""

    equity_usd: Decimal
    weights: dict[str, float] = field(default_factory=dict)


def _latest_state(session: Session, mode: str) -> dict[str, Decimal]:
    """Latest balance per asset for ``mode`` (highest row id wins)."""
    rows = (
        session.execute(
            select(Balance).where(Balance.mode == mode).order_by(Balance.id)
        )
        .scalars()
        .all()
    )
    state: dict[str, Decimal] = {}
    for row in rows:
        state[row.asset] = row.quantity
    return state


def _apply(state: dict[str, Decimal], order: Order, fill: Fill) -> None:
    """Apply one fill to ``state`` in place. Raises on an oversell or cash
    shortfall; the caller decides which assets get snapshot rows."""
    base, quote = order.symbol.split("-", 1)
    # Postgres rounds fill columns to 18 dp on insert; use the same rounded
    # inputs so apply_fill and rebuild see identical numbers.
    price = _q(fill.price)
    qty = _q(fill.quantity)
    fee = _q(fill.fee)
    held = state.get(base, Decimal(0))
    cash = state.get(quote, Decimal(0))
    cost = price * qty
    if order.side == "buy":
        state[base] = held + qty
        state[quote] = cash - cost
        if state[quote] < 0:
            raise ValueError(
                f"buy of {qty} {base} at {price} exceeds {quote} cash of {cash}"
            )
    else:
        if held < qty:
            raise ValueError(f"cannot sell {qty} {base}: only {held} held")
        state[base] = held - qty
        state[quote] = cash + cost
    fee_asset = fill.fee_currency
    state[fee_asset] = state.get(fee_asset, Decimal(0)) - fee
    if state[fee_asset] < 0:
        raise ValueError(f"fee of {fee} {fee_asset} exceeds {fee_asset} balance")
    for asset in (base, quote, fee_asset):
        state[asset] = _q(state[asset])


def apply_fill(session: Session, fill: Fill) -> None:
    """Append balance snapshot rows for the assets ``fill`` moves.

    No-op for shadow fills (their order row has ``shadow = true``).
    """
    order = session.get(Order, fill.order_id)
    if order is None:
        raise ValueError(f"fill {fill.id} has no order")
    if order.shadow:
        return
    state = _latest_state(session, order.mode)
    before = dict(state)
    _apply(state, order, fill)
    for asset, qty in state.items():
        if before.get(asset) != qty:
            session.add(
                Balance(time=fill.time, mode=order.mode, asset=asset, quantity=qty)
            )


def positions(session: Session, mode: str) -> dict[str, Decimal]:
    """Current non-cash holdings, keyed by asset code (e.g. ``"BTC"``)."""
    return {
        asset: qty
        for asset, qty in _latest_state(session, mode).items()
        if asset != CASH and qty != 0
    }


def cash(session: Session, mode: str) -> Decimal:
    """Current USD cash for ``mode``."""
    return _latest_state(session, mode).get(CASH, Decimal(0))


def cost_basis(session: Session, mode: str, symbol: str) -> Decimal:
    """Average cost (including fees in the quote currency) per unit of the
    current position in ``symbol``; 0 when nothing is held."""
    base = symbol.split("-", 1)[0]
    rows = session.execute(
        select(Fill, Order)
        .join(Order, Fill.order_id == Order.id)
        .where(
            Order.mode == mode,
            Order.symbol == symbol,
            Order.shadow.is_(False),
        )
        .order_by(Fill.time, Fill.id)
    ).all()
    held = Decimal(0)
    total_cost = Decimal(0)
    for fill, order in rows:
        base_fee = fill.fee if fill.fee_currency == base else Decimal(0)
        quote_fee = fill.fee - base_fee
        if order.side == "buy":
            total_cost += fill.price * fill.quantity + quote_fee
            held += fill.quantity - base_fee
        else:
            if held < fill.quantity:
                raise ValueError(
                    f"cannot sell {fill.quantity} {base}: only {held} held"
                )
            avg = total_cost / held if held else Decimal(0)
            held -= fill.quantity + base_fee
            if held < 0:
                raise ValueError(
                    f"fee of {fill.fee} {base} exceeds {base} balance"
                )
            total_cost = avg * held
    if held == 0:
        return Decimal(0)
    return _q(total_cost / held)


def _price_for(asset: str, prices: Mapping[str, Decimal | float]) -> Decimal:
    """USD price of ``asset``: exact key, else the ``"<asset>-*"`` key."""
    if asset in prices:
        return Decimal(str(prices[asset]))
    for key, value in prices.items():
        if key.startswith(f"{asset}-"):
            return Decimal(str(value))
    raise KeyError(f"no price for {asset}")


def equity(
    session: Session, mode: str, prices: Mapping[str, Decimal | float]
) -> Decimal:
    """Cash plus the marked value of every position."""
    total = cash(session, mode)
    for asset, qty in positions(session, mode).items():
        total += qty * _price_for(asset, prices)
    return total


def portfolio_view(
    session: Session, mode: str, prices: Mapping[str, Decimal | float]
) -> Portfolio:
    """Equity and per-symbol weights, for ``daily_cycle.PortfolioView``."""
    eq = equity(session, mode, prices)
    weights: dict[str, float] = {}
    if eq > 0:
        for asset, qty in positions(session, mode).items():
            for key in prices:
                if key == asset or key.startswith(f"{asset}-"):
                    weights[key] = float(qty * Decimal(str(prices[key])) / eq)
    return Portfolio(equity_usd=eq, weights=weights)


def deposit(session: Session, mode: str, amount: Decimal, clock: Clock) -> None:
    """Credit the account with cash (e.g. the paper starting balance).

    Writes a ``balances`` row and a ``kind="deposit"`` event so ``rebuild``
    can replay it.
    """
    new_total = _q(cash(session, mode) + amount)
    now = clock.now()
    session.add(Balance(time=now, mode=mode, asset=CASH, quantity=new_total))
    session.add(
        Event(
            time=now,
            kind="deposit",
            severity="info",
            message=f"Deposited {amount} USD into the {mode} account.",
            details={"mode": mode, "amount": str(amount)},
        )
    )


def rebuild(session: Session, mode: str) -> None:
    """Recompute all balances for ``mode`` from deposits and fills, in order.

    Deletes the mode's ``balances`` rows and writes the replayed final
    quantities. Positions and cash afterwards equal what the same fills
    applied one by one produced.
    """
    deposits = Decimal(0)
    last_time: datetime | None = None
    events = (
        session.execute(
            select(Event).where(Event.kind == "deposit").order_by(Event.time, Event.id)
        )
        .scalars()
        .all()
    )
    for event in events:
        if event.details.get("mode") == mode:
            deposits = _q(deposits + Decimal(event.details["amount"]))
            last_time = event.time

    rows = session.execute(
        select(Fill, Order)
        .join(Order, Fill.order_id == Order.id)
        .where(Order.mode == mode, Order.shadow.is_(False))
        .order_by(Fill.time, Fill.id)
    ).all()
    state: dict[str, Decimal] = {CASH: deposits}
    for fill, order in rows:
        _apply(state, order, fill)
        last_time = max(last_time, fill.time) if last_time else fill.time

    session.execute(delete(Balance).where(Balance.mode == mode))
    if last_time is None:
        return
    for asset, qty in state.items():
        if qty != 0:
            session.add(
                Balance(time=last_time, mode=mode, asset=asset, quantity=_q(qty))
            )
    session.flush()
