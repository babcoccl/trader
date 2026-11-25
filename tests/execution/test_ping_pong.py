"""Ping-pong worker tests (spec W1.3c): multi-level state machine, re-quote
timer, paper fills and the halt path. Needs TEST_DATABASE_URL; the live
ticker is injected, so no network is touched."""

import json
import os
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import FrozenClock
from trader.core.config import get_settings
from trader.execution.paper import PaperAdapter
from trader.execution.ping_pong import PingPongWorker, fetch_live_bbo
from trader.ledger import accounting
from trader.ledger.models import (
    Balance,
    Candle,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    Order,
    Plan,
    Setting,
)
from trader.risk import engine as risk
from trader.strategy import subsidy

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 20, 12, 0, 10, tzinfo=UTC)
_OPEN = ("new", "open", "partially_filled")


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture()
def session(monkeypatch):
    assert TEST_URL is not None
    monkeypatch.setenv("TRADER_DATABASE_URL", TEST_URL)
    get_settings.cache_clear()
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Balance,
            Event,
            Candle,
            Setting,
            EquitySnapshot,
        ):
            s.execute(delete(table))
        s.commit()
        yield s
    engine.dispose()
    get_settings.cache_clear()


def _daily_candles(symbol: str, n: int, base: float) -> list[Candle]:
    """``n`` mild sideways daily candles ending yesterday (ADX < 20)."""
    rng = np.random.default_rng(3)
    rets = rng.normal(0, 0.004, n)
    closes = base * np.exp(np.cumsum(rets))
    day0 = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    candles: list[Candle] = []
    for i in range(n):
        open_time = day0 - timedelta(days=n - i)
        c = Decimal(str(round(closes[i], 2)))
        candles.append(
            Candle(
                symbol=symbol,
                interval="1d",
                open_time=open_time,
                open=c,
                high=c * Decimal("1.001"),
                low=c * Decimal("0.999"),
                close=c,
                volume=Decimal("0"),
                source="fixture",
            )
        )
    return candles


def _enable(monkeypatch, **overrides: str) -> None:
    env = {
        "TRADER_SUBSIDY_ENABLED": "true",
        "TRADER_VENUE": "coinbase",
        "TRADER_SUBSIDY_TARGET_VOLUME_USD": "10000",
        "TRADER_SUBSIDY_DAILY_CAP_USD": "2000",
        "TRADER_SUBSIDY_ADX_MAX": "20",
        "TRADER_SUBSIDY_ORDER_NOTIONAL_USD": "250",
        "TRADER_SUBSIDY_ORDER_LEVELS": "2",
        "TRADER_SUBSIDY_SPREAD_BPS": "5",
        "TRADER_SUBSIDY_REFRESH_SECONDS": "20",
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _active(
    session: Session,
    monkeypatch,
    btc: Decimal | None = Decimal("0.05"),
    **overrides: str,
) -> FrozenClock:
    """Active subsidy sleeve: env on, sideways ADX, funded account."""
    _enable(monkeypatch, **overrides)
    for candle in _daily_candles("BTC-USD", 60, 60000):
        session.add(candle)
    # A fresh 5m candle: risk.check_plan vetoes plans priced on stale data.
    session.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=NOW - timedelta(minutes=5),
            open=Decimal("60000"),
            high=Decimal("60000"),
            low=Decimal("60000"),
            close=Decimal("60000"),
            volume=Decimal("0"),
            source="fixture",
        )
    )
    clock = FrozenClock(NOW)
    accounting.deposit(session, "paper", Decimal("10000"), clock)
    # Some BTC so the sell side of the grid can quote.
    if btc is not None:
        session.add(Balance(time=NOW, mode="paper", asset="BTC", quantity=btc))
    risk.update_equity(session, Decimal("10000"), clock)
    session.flush()
    snap = subsidy.compute_status(session, clock, "paper")
    assert snap.status == "active"
    return clock


def _worker(
    bbo: dict[str, tuple[Decimal, Decimal]], clock: FrozenClock
) -> PingPongWorker:
    return PingPongWorker(ticker=lambda _symbol: bbo["quote"], clock=clock)


def _adapter(session: Session, clock: FrozenClock) -> PaperAdapter:
    return PaperAdapter(session, BITSTAMP, clock, "paper")


def _open(session: Session) -> list[Order]:
    return list(
        session.scalars(
            select(Order)
            .join(Plan, Order.plan_id == Plan.id)
            .where(Plan.sleeve == "ping_pong", Order.status.in_(_OPEN))
            .order_by(Order.id)
        )
    )


def test_fetch_live_bbo_sends_headers() -> None:
    """Offline: urlopen mocked, no network. fetch_live_bbo must send a
    Request carrying User-Agent and Accept headers (Coinbase 403s
    headerless urllib calls) and return (Decimal, Decimal)."""
    payload = json.dumps({"bid": "59990.50", "ask": "60010.25"}).encode()
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__.return_value = resp
    with patch(
        "trader.execution.ping_pong.urllib.request.urlopen", return_value=resp
    ) as mock_open:
        bbo = fetch_live_bbo("BTC-USD")
    req = mock_open.call_args[0][0]
    assert isinstance(req, urllib.request.Request)
    assert req.get_header("User-agent") == "trader/1.0"
    assert req.get_header("Accept") == "application/json"
    assert bbo == (Decimal("59990.50"), Decimal("60010.25"))


def test_multi_level_progression(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (2, 2)
    orders = _open(session)
    assert len(orders) == 4
    buys = sorted(o.price for o in orders if o.side == "buy")
    sells = sorted(o.price for o in orders if o.side == "sell")
    assert buys == [Decimal("59930.01"), Decimal("59960.00")]
    assert sells == [Decimal("60040.00"), Decimal("60070.01")]
    plan = session.get(Plan, orders[0].plan_id)
    assert plan is not None and plan.status == "approved"
    assert all(e["maker_or_cancel"] for e in plan.entry_orders)

    # Ask dips under the top buy level only: one buy fills -> (1, 2).
    bbo["quote"] = (Decimal("59950"), Decimal("59958"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 2)
    assert len(_open(session)) == 3

    # Bid lifts through the top sell level only: one sell fills -> (1, 1).
    bbo["quote"] = (Decimal("59990"), Decimal("59958"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 1)
    assert len(_open(session)) == 2

    # Bid stays through the sell level; the ask stays above the buy -> (1, 0).
    bbo["quote"] = (Decimal("59990"), Decimal("59965"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 0)
    assert [o.side for o in _open(session)] == ["buy"]

    # Ask drops through the buy: (0, 0) resets the grid to (2, 2).
    bbo["quote"] = (Decimal("59950"), Decimal("59955"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (2, 2)
    assert len(_open(session)) == 4


def test_requotes_after_refresh_seconds(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    first = _open(session)
    assert len(first) == 4

    # Same tick again, timer not elapsed: orders untouched.
    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == first

    clock.advance(timedelta(seconds=21))
    bbo["quote"] = (Decimal("59980"), Decimal("59990"))
    worker.tick(session, adapter, clock, "paper")

    for order in first:
        session.refresh(order)
        assert order.status == "cancelled"
    new = _open(session)
    assert len(new) == 4
    new_buys = sorted(o.price for o in new if o.side == "buy")
    assert new_buys == [Decimal("59920.02"), Decimal("59950.01")]


def test_paper_fill_books_to_ledger(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    order = _open(session)[0]

    bbo["quote"] = (Decimal("59950"), Decimal("59955"))
    worker.tick(session, adapter, clock, "paper")

    fills = list(session.scalars(select(Fill)))
    assert len(fills) == 1
    assert fills[0].order_id == order.id
    assert fills[0].price == order.price
    assert accounting.positions(session, "paper")["BTC"] == (
        Decimal("0.05") + order.quantity
    )

    snap = subsidy.compute_status(session, clock, "paper")
    assert snap.today_booster_volume_usd == fills[0].price * fills[0].quantity
    assert snap.booster_fees_30d_usd == fills[0].fee


def test_buy_veto_still_places_sells(session: Session, monkeypatch) -> None:
    """A mixed grid vetoed on buy exposure must still place its eligible
    sell quotes so inventory can unwind."""
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    # Cap BTC weight just above the current ~0.23 weight: the buy side's
    # ~0.27 target vetoes the mixed batch while sells stay exempt.
    session.add(Setting(key="risk.limits.max_asset_weight", value=0.25, updated_at=NOW))
    session.flush()
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")

    orders = _open(session)
    assert len(orders) == 2
    assert all(o.side == "sell" for o in orders)
    plan = session.get(Plan, orders[0].plan_id)
    assert plan is not None and plan.status == "approved"
    # The vetoed mixed attempt left no orphaned decision row.
    assert len(list(session.scalars(select(Decision)))) == 1


def test_paper_fill_charges_maker_fee(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    bbo["quote"] = (Decimal("59950"), Decimal("59955"))
    worker.tick(session, adapter, clock, "paper")

    fill = session.scalars(select(Fill)).one()
    assert fill.liquidity == "maker"
    assert fill.fee == fill.price * fill.quantity * Decimal(str(BITSTAMP.maker_fee))


def test_partial_fill_keeps_level(session: Session, monkeypatch) -> None:
    """Levels are consumed per completed order, not per execution."""
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    order = next(o for o in _open(session) if o.side == "buy")

    # First execution leaves the order partially filled: no level consumed.
    order.status = "partially_filled"
    session.add(
        Fill(
            order_id=order.id,
            time=NOW,
            price=order.price,
            quantity=order.quantity / 2,
            fee=Decimal("0"),
            fee_currency="USD",
            liquidity="maker",
        )
    )
    session.flush()
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (2, 2)

    # The completing execution consumes the level.
    order.status = "filled"
    session.add(
        Fill(
            order_id=order.id,
            time=NOW,
            price=order.price,
            quantity=order.quantity / 2,
            fee=Decimal("0"),
            fee_currency="USD",
            liquidity="maker",
        )
    )
    session.flush()
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 2)


def test_batched_fills_on_one_order_count_once(session: Session, monkeypatch) -> None:
    """poll_fills can return several executions of one order together;
    they consume a single level."""
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    order = next(o for o in _open(session) if o.side == "buy")

    # Two executions on the same completed order in one pass: one level.
    order.status = "filled"
    for _ in range(2):
        session.add(
            Fill(
                order_id=order.id,
                time=NOW,
                price=order.price,
                quantity=order.quantity / 2,
                fee=Decimal("0"),
                fee_currency="USD",
                liquidity="maker",
            )
        )
    session.flush()
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 2)


def test_level_change_reseats_counters(session: Session, monkeypatch) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (2, 2)

    # Drop one level below the top: (1, 2) before the setting change.
    bbo["quote"] = (Decimal("59950"), Decimal("59958"))
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (1, 2)

    # Editing the level count reseats the counters immediately; the grid
    # itself re-quotes on the next refresh.
    session.add(Setting(key="subsidy_order_levels", value=3, updated_at=NOW))
    session.flush()
    worker.tick(session, adapter, clock, "paper")
    assert (worker.buys_remaining, worker.sells_remaining) == (3, 3)

    clock.advance(timedelta(seconds=21))
    worker.tick(session, adapter, clock, "paper")
    assert len(_open(session)) == 6


def test_halt_cancels_and_disabled_places_nothing(
    session: Session, monkeypatch
) -> None:
    clock = _active(session, monkeypatch)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert len(_open(session)) == 4

    session.add(Setting(key="risk.halted", value=True, updated_at=NOW))
    session.flush()
    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == []
    for order in session.scalars(select(Order)).all():
        assert order.status == "cancelled"


def test_auto_seed_buys_inventory_for_fresh_ladder(
    session: Session, monkeypatch
) -> None:
    """Spec W1.3g: auto-seed on + zero BTC + fresh ladder -> one maker-only
    buy at the best bid sized to order_levels * order_notional_usd."""
    clock = _active(session, monkeypatch, btc=None, TRADER_SUBSIDY_AUTO_SEED="true")
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")

    orders = _open(session)
    assert len(orders) == 1
    seed = orders[0]
    assert seed.side == "buy"
    assert seed.price == Decimal("59990.00")
    # 2 levels * $250 = $500 -> 500 / 59990 rounded down to the lot.
    assert seed.quantity == Decimal("0.00833472")
    plan = session.get(Plan, seed.plan_id)
    assert plan is not None and plan.status == "approved"
    assert len(plan.entry_orders) == 1
    assert plan.entry_orders[0]["maker_or_cancel"] is True


def test_auto_seed_fill_then_normal_ladder(session: Session, monkeypatch) -> None:
    """Once the seed buy fills and BTC covers a sell level, the next
    refresh quotes buys and sells normally."""
    clock = _active(session, monkeypatch, btc=None, TRADER_SUBSIDY_AUTO_SEED="true")
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert len(_open(session)) == 1

    # Ask drops under the resting seed bid: the seed fills, one buy level
    # is consumed, and the re-quote runs the normal ladder.
    bbo["quote"] = (Decimal("59980"), Decimal("59985"))
    worker.tick(session, adapter, clock, "paper")

    assert (worker.buys_remaining, worker.sells_remaining) == (1, 2)
    assert accounting.positions(session, "paper")["BTC"] > Decimal("0")
    orders = _open(session)
    assert [o.side for o in orders] == ["buy", "sell", "sell"]
    buys = sorted(o.price for o in orders if o.side == "buy")
    sells = sorted(o.price for o in orders if o.side == "sell")
    assert buys == [Decimal("59950.01")]
    assert sells == [Decimal("60014.99"), Decimal("60044.98")]


def test_no_seed_when_auto_seed_off(session: Session, monkeypatch) -> None:
    """Default off: an under-funded fresh ladder quotes buys only; no
    order rests at the best bid."""
    clock = _active(session, monkeypatch, btc=None)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")

    orders = _open(session)
    assert len(orders) == 2
    assert all(o.side == "buy" for o in orders)
    assert all(o.price < Decimal("59990") for o in orders)


def test_no_seed_on_stale_close(session: Session, monkeypatch) -> None:
    """A failed ticker leaves bid == ask at the last close; a seed placed
    there would fill against itself next pass, so it must not fire."""
    clock = _active(session, monkeypatch, btc=None, TRADER_SUBSIDY_AUTO_SEED="true")
    worker = PingPongWorker(ticker=lambda _symbol: None, clock=clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")

    orders = _open(session)
    assert orders
    # Normal ladder quotes at the close-derived spread; nothing rests at
    # the close itself.
    assert all(o.price != Decimal("60000") for o in orders)


def _sleeve_fill(
    session: Session,
    *,
    sleeve: str,
    side: str,
    notional: Decimal,
    at: datetime,
    price: Decimal = Decimal("50000"),
) -> None:
    """Book one applied fill under ``sleeve`` (boosts counters and, for
    ping_pong buys, builds unwindable inventory)."""
    decision = Decision(
        cycle="intraday",
        ran_at=at,
        action="plan_proposed",
        reason="seed",
        details={},
    )
    session.add(decision)
    session.flush()
    plan = Plan(
        sleeve=sleeve,
        symbol="BTC-USD",
        side=side,
        status="completed",
        reference_price=price,
        target_weight=Decimal("0"),
        entry_orders=[],
        worst_case_loss=Decimal("0"),
        est_fees=Decimal("0"),
        rationale="seed",
        expires_at=at,
        decision_id=decision.id,
    )
    session.add(plan)
    session.flush()
    order = Order(
        plan_id=plan.id,
        venue="paper",
        client_order_id=f"{plan.id}-1",
        symbol="BTC-USD",
        side=side,
        type="limit",
        price=price,
        quantity=notional / price,
        status="filled",
        mode="paper",
    )
    session.add(order)
    session.flush()
    fill = Fill(
        order_id=order.id,
        time=at,
        price=price,
        quantity=notional / price,
        fee=notional * Decimal("0.004"),
        fee_currency="USD",
        liquidity="maker",
    )
    session.add(fill)
    session.flush()
    accounting.apply_fill(session, fill)


def test_unwind_sells_only_ping_pong_inventory_past_daily_cap(
    session: Session, monkeypatch
) -> None:
    """Spec W1.3h: $4.85 of cap room left while holding ping-pong BTC ->
    sell-only unwind quotes, no buys, no auto-seed, and quoting stops
    once the ping-pong inventory is back to USD."""
    clock = _active(session, monkeypatch)
    _sleeve_fill(
        session,
        sleeve="ping_pong",
        side="buy",
        notional=Decimal("1995.15"),
        at=NOW - timedelta(hours=1),
    )
    session.flush()
    snap = subsidy.compute_status(session, clock, "paper")
    assert snap.status == "throttled"
    assert snap.reason == "Daily booster cap reached."

    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")

    orders = _open(session)
    assert orders
    assert all(o.side == "sell" for o in orders)
    # No auto-seed: nothing rests at or under the best bid.
    assert all(o.price > Decimal("60010") for o in orders)
    pp_inventory = subsidy._ping_pong_inventory(session, "paper")
    assert pp_inventory > 0
    assert sum(o.quantity for o in orders) <= pp_inventory
    plan = session.get(Plan, orders[0].plan_id)
    assert plan is not None and plan.status == "approved"

    # Each refresh sells at most 2 levels x $250; a far-higher bid lifts
    # every level until the inventory is back to USD, then quoting stops.
    bbo["quote"] = (Decimal("65100"), Decimal("65010"))
    for _ in range(15):
        worker.tick(session, adapter, clock, "paper")
        if not _open(session):
            break
    assert _open(session) == []
    assert (
        subsidy._ping_pong_inventory(session, "paper") * Decimal("60110")
        < (subsidy.MIN_ORDER_NOTIONAL["paper"])
    )


def test_unwind_never_sells_other_sleeve_btc(session: Session, monkeypatch) -> None:
    """Past the daily cap with zero ping-pong inventory, the BTC other
    sleeves own is untouchable: no unwind quotes at all."""
    clock = _active(session, monkeypatch)
    # Matched ping-pong round trip spends the cap but leaves no BTC.
    _sleeve_fill(
        session,
        sleeve="ping_pong",
        side="buy",
        notional=Decimal("1995.15"),
        at=NOW - timedelta(hours=1),
    )
    _sleeve_fill(
        session,
        sleeve="ping_pong",
        side="sell",
        notional=Decimal("1995.15"),
        at=NOW - timedelta(minutes=30),
    )
    # BTC bought by a different sleeve must never be unwound.
    _sleeve_fill(
        session,
        sleeve="trend",
        side="buy",
        notional=Decimal("600"),
        at=NOW - timedelta(hours=2),
    )
    session.flush()
    snap = subsidy.compute_status(session, clock, "paper")
    assert snap.status == "throttled"
    assert snap.reason == "Daily booster cap reached."
    btc_before = accounting.positions(session, "paper")["BTC"]

    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")

    assert _open(session) == []
    assert accounting.positions(session, "paper")["BTC"] == btc_before
    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == []
    assert accounting.positions(session, "paper")["BTC"] == btc_before


def test_no_seed_mid_cycle(session: Session, monkeypatch) -> None:
    """Auto-seed never fires once sell levels have been consumed."""
    clock = _active(session, monkeypatch, btc=None, TRADER_SUBSIDY_AUTO_SEED="true")
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert len(_open(session)) == 1  # the seed order

    # Mid-cycle: a sell level is already consumed, so even though BTC is
    # still zero the refresh must quote the normal ladder, not re-seed.
    worker.sells_remaining = 1
    clock.advance(timedelta(seconds=21))
    worker.tick(session, adapter, clock, "paper")

    orders = _open(session)
    assert len(orders) == 2
    assert all(o.side == "buy" for o in orders)
    assert all(o.price < Decimal("59990") for o in orders)


def test_disabled_places_nothing(session: Session, monkeypatch) -> None:
    _enable(monkeypatch, TRADER_SUBSIDY_ENABLED="false")
    clock = FrozenClock(NOW)
    bbo: dict[str, tuple[Decimal, Decimal]] = {
        "quote": (Decimal("59990"), Decimal("60010"))
    }
    worker = _worker(bbo, clock)
    adapter = _adapter(session, clock)

    worker.tick(session, adapter, clock, "paper")
    assert _open(session) == []
    # Counters stay uninitialized while the sleeve is disabled.
    assert worker.buys_remaining is None
    assert worker.sells_remaining is None
