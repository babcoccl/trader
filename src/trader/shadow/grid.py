"""The grid shadow sleeve (spec G5).

While a symbol's regime has a sideways reading (Wilder ADX under 20)
the grid buys one lot each time a five-minute bar trades through a
level 5% under the last one, sells each lot one level up, and sells
everything when the sideways reading ends. It logs a book, a mark and
a note only — never an order.

``run`` keeps a cursor in ``shadow_state``; after downtime it replays
every stored candle since that cursor under the *current* reading, not
the reading each candle closed under. Both symbols' candles are merged
in open-time order and each replayed buy is sized at every symbol's
fresh close as of that bar, whether or not that symbol's cursor already
covered it; the daily mark still prices the book at the freshest
closes. A level counts as filled when a bar trades ``THROUGH`` beyond
it, which is more generous than a real order queue.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import Clock
from trader.data.sources.base import period_seconds
from trader.ledger.models import Candle, ShadowState, ShadowTrade
from trader.shadow import book, data
from trader.strategy import sleeves

NAME = "grid"
SYMBOLS = ("BTC-USD", "ETH-USD")
SPACING = 0.05
LOTS = 10  # per symbol
THROUGH = 0.0001  # a level fills only if the bar trades this far beyond it
MIN_LOT_USD = Decimal("10")
DESCRIPTION = (
    "While ADX(14) is under 20: buys one lot each time the price falls "
    "through a level 5% apart, sells that lot 5% higher, and sells "
    "everything when the sideways reading ends."
)


@dataclass
class GridState:
    """The in-memory form of ``shadow_state.state`` for one symbol."""

    on: bool = False
    p0: float | None = None  # level 0: the open of the bar that turned the grid on


@dataclass(frozen=True)
class Bar:
    """One closed five-minute candle, floats."""

    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Lot:
    """An open grid lot: the level it was bought at and its fill price."""

    level: int
    entry_price: float


@dataclass(frozen=True)
class Action:
    """One fill ``step`` decided on; ``run`` books it."""

    kind: Literal["buy", "sell"]
    level: int
    price: float
    fee_rate: float
    slippage: float
    reason: str = ""


def level_price(p0: float, j: int) -> float:
    """``p0 * (1 + SPACING) ** j``."""
    return p0 * (1 + SPACING) ** j


def step(state: GridState, lots: list[Lot], candle: Bar, on: bool) -> list[Action]:
    """Advance the grid by one closed candle and return its fills.

    Pure: no database, floats only. ``lots`` are the lots held before
    this candle; a lot bought in this candle is never sold in it. Sells
    come before buys, and a level a sell just freed may be bought again
    in the same bar.
    """
    if not on:
        state.on = False
        return [
            Action(
                kind="sell",
                level=lot.level,
                price=candle.open,
                fee_rate=BITSTAMP.taker_fee,
                slippage=BITSTAMP.slippage,
                reason="sideways reading ended",
            )
            for lot in lots
        ]
    if not state.on:
        state.on = True
        state.p0 = candle.open
    assert state.p0 is not None  # set the first time the grid turns on
    actions: list[Action] = []
    held_levels = {lot.level for lot in lots}
    n_held = len(lots)
    for lot in lots:
        sell_at = lot.entry_price * (1 + SPACING)
        if candle.high > sell_at * (1 + THROUGH):
            actions.append(
                Action(
                    kind="sell",
                    level=lot.level,
                    price=sell_at,
                    fee_rate=BITSTAMP.maker_fee,
                    slippage=0.0,
                    reason="sold one level up",
                )
            )
            held_levels.discard(lot.level)
            n_held -= 1
    # Level at or under the candle's open; level 0 is p0 itself.
    j = math.floor(math.log(candle.open / state.p0) / math.log(1 + SPACING))
    while candle.low < level_price(state.p0, j) * (1 - THROUGH):
        if n_held >= LOTS:
            break
        if j not in held_levels:
            actions.append(
                Action(
                    kind="buy",
                    level=j,
                    price=level_price(state.p0, j),
                    fee_rate=BITSTAMP.maker_fee,
                    slippage=0.0,
                )
            )
            held_levels.add(j)
            n_held += 1
        j -= 1
    return actions


def _newest_closed(session: Session, symbol: str, now: datetime) -> datetime | None:
    """Open time of the newest 5-minute candle closed at ``now``."""
    return session.scalar(
        select(func.max(Candle.open_time)).where(
            Candle.symbol == symbol,
            Candle.interval == "5m",
            Candle.open_time <= now - timedelta(seconds=period_seconds("5m")),
        )
    )


def _load_row(session: Session, symbol: str) -> ShadowState | None:
    return session.scalars(
        select(ShadowState).where(
            ShadowState.sleeve == NAME, ShadowState.symbol == symbol
        )
    ).first()


def _save_row(
    session: Session,
    row: ShadowState | None,
    symbol: str,
    state: dict[str, object],
    now: datetime,
) -> None:
    if row is None:
        row = ShadowState(sleeve=NAME, symbol=symbol, state=state, updated_at=now)
        session.add(row)
    else:
        row.updated_at = now
        row.state = state
    session.flush()


def _code(symbol: str) -> str:
    return symbol.split("-")[0]


def _note_on(
    symbol: str,
    adx: float | None,
    state: GridState,
    lots: list[Lot],
    reference: float | None,
) -> str:
    """The mark sentence while the reading is sideways."""
    code = _code(symbol)
    a = f"ADX {adx:.1f}" if adx is not None else "ADX not yet defined"
    if state.p0 is None or reference is None:
        return f"{code}: sideways reading ({a}); waiting for the first bar."
    held = {lot.level for lot in lots}
    j = math.floor(math.log(reference / state.p0) / math.log(1 + SPACING))
    while j in held:
        j -= 1
    parts = [
        f"{code}: sideways reading ({a}). "
        f"{len(lots)} lot{'s' if len(lots) != 1 else ''} held; "
        f"next buy at {level_price(state.p0, j):,.0f}"
    ]
    if lots:
        next_sell = min(lot.entry_price for lot in lots) * (1 + SPACING)
        parts.append(f"next sell at {next_sell:,.0f}")
    return ", ".join(parts) + "."


def run(session: Session, clock: Clock) -> None:
    """One intraday shadow run over the closed 5-minute candles."""
    now = clock.now()
    today = datetime(now.year, now.month, now.day, tzinfo=UTC)
    spec = sleeves.get(NAME)
    active = data.symbols(SYMBOLS)
    last_close: dict[str, float] = {}
    open_lots: dict[str, list[Lot]] = {}
    states: dict[str, GridState] = {}

    # Load each symbol's state and its candles to replay. Symbols share
    # one book, so below the candles are merged in open-time order: a
    # replayed buy is then sized from closes as of that bar.
    rows: dict[str, ShadowState | None] = {}
    cursors: dict[str, datetime] = {}
    on_by_symbol: dict[str, bool] = {}
    held: dict[str, dict[int, ShadowTrade]] = {}
    events: list[tuple[datetime, str, Bar]] = []
    for symbol in active:
        row = _load_row(session, symbol)
        stored = dict(row.state) if row is not None else {}
        regime = data.latest_regime(session, symbol)
        on_by_symbol[symbol] = sleeves.allowed(spec, regime)
        raw_p0 = stored.get("p0")
        states[symbol] = GridState(
            on=bool(stored.get("on", False)),
            p0=float(raw_p0) if isinstance(raw_p0, int | float) else None,
        )
        raw_cursor = stored.get("cursor")
        cursor = (
            datetime.fromisoformat(raw_cursor) if isinstance(raw_cursor, str) else None
        )
        if cursor is None:
            # No stored state: mark the newest closed candle as seen and
            # process nothing (spec G5 step 3.2).
            newest = _newest_closed(session, symbol, now)
            _save_row(
                session,
                row,
                symbol,
                {
                    "on": False,
                    "p0": None,
                    "cursor": newest.isoformat() if newest is not None else None,
                },
                now,
            )
            open_lots[symbol] = []
            held[symbol] = {}
            continue
        rows[symbol] = row
        cursors[symbol] = cursor
        open_lots[symbol] = []
        held[symbol] = {}
        for trade in book.open_trades(session, NAME, symbol):
            level = int(trade.details["level"])
            open_lots[symbol].append(
                Lot(level=level, entry_price=float(trade.entry_price))
            )
            held[symbol][level] = trade
        for idx, r in data.bars_after(session, symbol, cursor, now).iterrows():
            open_time = cast(datetime, idx)  # index is open_time (timestamptz)
            events.append(
                (
                    open_time,
                    symbol,
                    Bar(
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                    ),
                )
            )

    # Valuation is independent of the cursors: every symbol's closes over
    # the replay window, plus a MAX_PRICE_AGE lead-in, come from
    # ``data.bars_after`` even where that symbol's cursor already covered
    # them. All closes of a timestamp apply before its actions, and a
    # close counts only while fresh, the rule ``data.price`` uses.
    events.sort(key=lambda event: (event[0], event[1]))
    period = timedelta(seconds=period_seconds("5m"))
    by_time: dict[datetime, list[tuple[str, Bar]]] = {}
    for open_time, symbol, candle in events:
        by_time.setdefault(open_time, []).append((symbol, candle))
    closes: dict[datetime, dict[str, Decimal]] = {}
    if events:
        lead_in = events[0][0] - data.MAX_PRICE_AGE - period
        for s in active:
            frame = data.bars_after(session, s, lead_in, now)
            for idx, close in frame["close"].items():
                closes.setdefault(cast(datetime, idx), {})[s] = Decimal(str(close))
    last_seen: dict[str, tuple[datetime, Decimal]] = {}
    for open_time in sorted(closes.keys() | by_time.keys()):
        for s, close in closes.get(open_time, {}).items():
            last_seen[s] = (open_time, close)
        trade_time = open_time + period
        asof = {
            s: close
            for s, (bar_open, close) in last_seen.items()
            if bar_open + period >= trade_time - data.MAX_PRICE_AGE
        }
        for symbol, candle in by_time.get(open_time, []):
            state = states[symbol]
            lots = open_lots[symbol]
            for action in step(state, lots, candle, on_by_symbol[symbol]):
                if action.kind == "sell":
                    book.sell(
                        session,
                        held[symbol].pop(action.level),
                        trade_time,
                        Decimal(str(action.price)),
                        action.fee_rate,
                        action.slippage,
                        action.reason,
                    )
                    lots = [lot for lot in lots if lot.level != action.level]
                    open_lots[symbol] = lots
                    continue
                summary = book.summary(session, NAME, asof)
                spend = min(summary.equity / (2 * LOTS), summary.cash)
                if spend < MIN_LOT_USD:
                    continue
                trade = book.buy(
                    session,
                    NAME,
                    symbol,
                    trade_time,
                    Decimal(str(action.price)),
                    spend,
                    action.fee_rate,
                    action.slippage,
                    details={"level": action.level},
                )
                lots.append(Lot(level=action.level, entry_price=action.price))
                held[symbol][action.level] = trade
            cursors[symbol] = open_time
            last_close[symbol] = candle.close

    for symbol, row in rows.items():
        st = states[symbol]
        _save_row(
            session,
            row,
            symbol,
            {
                "on": st.on,
                "p0": st.p0,
                "cursor": cursors[symbol].isoformat(),
            },
            now,
        )

    # Once per UTC day: the first run after every active symbol has
    # today's daily decision writes the mark.
    if not active or book.marked(session, NAME, today.date()):
        return
    regimes = {s: data.regime_today(session, s, today) for s in active}
    if any(r is None for r in regimes.values()):
        return
    # The mark always values the book at the freshest closes, kept
    # separate from the as-of replay prices above.
    mark_prices = {
        s: p for s in active if (p := data.price(session, s, now)) is not None
    }
    notes: list[str] = []
    for symbol in active:
        regime = regimes[symbol]
        sideways = regime is not None and regime.sideways is True
        adx = regime.adx if regime is not None else None
        lots = open_lots.get(symbol) or [
            Lot(level=int(t.details["level"]), entry_price=float(t.entry_price))
            for t in book.open_trades(session, NAME, symbol)
        ]
        gstate = states.get(symbol)
        if gstate is None:
            row2 = _load_row(session, symbol)
            stored2 = dict(row2.state) if row2 is not None else {}
            raw_p0 = stored2.get("p0")
            gstate = GridState(
                on=bool(stored2.get("on", False)),
                p0=float(raw_p0) if isinstance(raw_p0, int | float) else None,
            )
        reference = last_close.get(symbol)
        if reference is None:
            p = data.price(session, symbol, now)
            reference = float(p) if p is not None else None
        if not sideways:
            a = f"ADX {adx:.1f}" if adx is not None else "ADX not yet defined"
            notes.append(f"{_code(symbol)}: no sideways reading ({a}), grid is off.")
        else:
            notes.append(_note_on(symbol, adx, gstate, lots, reference))
    book.mark(session, NAME, today.date(), mark_prices, " ".join(notes))
