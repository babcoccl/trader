"""Plan builder tests (spec 2.2): golden files for buy / partial sell /
full exit, worst-case-loss decomposition, tick/lot rounding, and the
``daily_cycle`` integration (build -> risk.check_plan -> save or blocked).
Golden-file and unit tests are pure; integration tests need
TEST_DATABASE_URL (Compose Postgres).
"""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.backtest.costs import BITSTAMP
from trader.core.clock import FrozenClock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS, Candle
from trader.engine import daily_cycle
from trader.ledger.models import Candle as CandleRow
from trader.ledger.models import Decision, Plan, Setting, Signal
from trader.plans import builder
from trader.risk import engine as risk_engine

TEST_URL = os.environ.get("TEST_DATABASE_URL")

NEEDS_DB = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

GOLDEN_DIR = Path(__file__).parent / "golden"
NOW = datetime(2026, 11, 20, 0, 5, tzinfo=UTC)
TICK = Decimal("0.01")
LOT = Decimal("0.00000001")
EQUITY = Decimal("10000")


def _plan_dict(plan) -> dict:
    return {
        "sleeve": plan.sleeve,
        "symbol": plan.symbol,
        "side": plan.side,
        "status": plan.status,
        "reference_price": str(plan.reference_price),
        "target_weight": str(plan.target_weight),
        "entry_orders": plan.entry_orders,
        "stop_price": str(plan.stop_price) if plan.stop_price is not None else None,
        "worst_case_loss": str(plan.worst_case_loss),
        "est_fees": str(plan.est_fees),
        "rationale": plan.rationale,
        "expires_at": plan.expires_at.isoformat(),
    }


def _build(**kw):
    args = dict(
        symbol="BTC-USD",
        target_weight=0.4,
        current_weight=0.0,
        equity_usd=EQUITY,
        price=Decimal("50000.00"),
        annual_vol=0.5,
        cost=BITSTAMP,
        clock=FrozenClock(NOW),
        rules_positive=7,
        tick=TICK,
        lot=LOT,
    )
    args.update(kw)
    return builder.build(**args)


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("buy", {}),
        (
            "partial_sell",
            {
                "symbol": "ETH-USD",
                "target_weight": 0.2,
                "current_weight": 0.5,
                "price": Decimal("3000.00"),
                "annual_vol": 0.9,
                "rules_positive": 4,
            },
        ),
        (
            "full_exit",
            {
                "symbol": "ETH-USD",
                "target_weight": 0.0,
                "current_weight": 0.4,
                "price": Decimal("3000.00"),
                "annual_vol": 0.9,
                "rules_positive": 1,
            },
        ),
    ],
)
def test_golden(name: str, overrides: dict) -> None:
    plan = _build(**overrides)
    assert plan is not None
    expected = json.loads((GOLDEN_DIR / f"{name}.json").read_text())
    assert _plan_dict(plan) == expected


def test_should_trade_false_returns_none() -> None:
    # 0.40 -> 0.38 is inside the 20% band: no plan.
    assert _build(target_weight=0.38, current_weight=0.4) is None


def test_worst_case_loss_is_sum_of_parts() -> None:
    plan = _build()
    assert plan is not None
    notional = Decimal("0.4") * EQUITY
    stop_loss = notional * (1 - plan.stop_price / plan.reference_price)
    assert plan.worst_case_loss == stop_loss + plan.est_fees
    # est_fees = notional * (taker + slippage) * 2, BITSTAMP = 0.004 + 0.0001.
    assert plan.est_fees == notional * Decimal("0.0041") * 2


def test_stop_is_three_ten_day_swings_wide() -> None:
    plan = _build()
    assert plan is not None
    # d = 0.5 / sqrt(365); stop = price * (1 - 3*d*sqrt(10)), floored to tick.
    assert plan.stop_price == Decimal("37585.91")


def test_prices_and_quantities_rounded_down() -> None:
    plan = _build(price=Decimal("50000.009"), target_weight=0.401)
    assert plan is not None
    order = plan.entry_orders[0]
    # Tick rounding is down: 50000.009 -> 50000.00.
    assert order["price"] == "50000.00"
    # Quantity floored to the lot size, never up.
    qty = Decimal(order["quantity"])
    assert qty == (qty / LOT).to_integral_value() * LOT
    notional = Decimal("0.401") * EQUITY
    assert qty * plan.reference_price <= notional


def test_buy_plan_shape() -> None:
    plan = _build()
    assert plan is not None
    assert plan.status == "pending"  # buys need owner approval
    assert plan.side == "buy"
    order = plan.entry_orders[0]
    assert order["type"] == "limit"
    assert order["maker_or_cancel"] is True
    assert order["reprice_seconds"] == 300
    assert order["market_after_seconds"] == 3600
    assert plan.expires_at == NOW + timedelta(hours=6)
    assert "raise BTC-USD from 0% to 40%" in plan.rationale
    assert "7 of 9 trend rules are positive" in plan.rationale
    assert "volatility is 50% a year" in plan.rationale


def test_sell_plans_are_preapproved() -> None:
    plan = _build(target_weight=0.2, current_weight=0.5)
    assert plan is not None
    assert plan.side == "sell"
    assert plan.status == "approved"
    assert plan.stop_price is None
    order = plan.entry_orders[0]
    assert order["type"] == "limit"
    assert "maker_or_cancel" not in order
    assert "lower BTC-USD from 50% to 20%" in plan.rationale


# --- daily_cycle integration (database) --------------------------------------


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


def _seed_uptrend(session: Session, days: int = 250) -> None:
    """Steadily rising daily candles: all 9 rules fire, targets are 50%."""
    candles: list[Candle] = []
    day0 = datetime(NOW.year, NOW.month, NOW.day, tzinfo=UTC) - timedelta(days=days)
    for i in range(days):
        open_time = day0 + timedelta(days=i)
        for symbol in SYMBOLS:
            close = Decimal("100") * (Decimal("1.001") ** i)
            candles.append(
                Candle(
                    symbol=symbol,
                    interval="1d",
                    open_time=open_time,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("0"),
                    source="fixture",
                )
            )
    # Fresh 5m candles so the risk engine's staleness check passes.
    for symbol in SYMBOLS:
        candles.append(
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=NOW - timedelta(seconds=60),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("0"),
                source="fixture",
            )
        )
    candles_mod.upsert(session, candles)


@pytest.fixture()
def session():
    assert TEST_URL is not None
    eng = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(eng) as s:
        for table in (Plan, Decision, Signal, CandleRow, Setting):
            s.execute(delete(table))
        _seed_uptrend(s)
        s.commit()
        yield s
    eng.dispose()


def _fresh_5m(session: Session, at: datetime) -> None:
    candles_mod.upsert(
        session,
        [
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=at - timedelta(minutes=5),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("0"),
                source="fixture",
            )
            for symbol in SYMBOLS
        ],
    )


def _portfolio(equity: Decimal, weights: dict[str, float]):
    return daily_cycle.CashOnlyPortfolio(equity_usd=equity, weights=weights)


@NEEDS_DB
def test_daily_cycle_saves_proposed_plan(session: Session) -> None:
    clock = FrozenClock(NOW)
    rows = daily_cycle.run(session, clock, _portfolio(EQUITY, {}))
    session.commit()
    proposed = [r for r in rows if r.action == "plan_proposed"]
    assert proposed, "rising fixture should propose buys for both assets"
    plans = session.scalars(select(Plan).order_by(Plan.id)).all()
    assert len(plans) == len(proposed)
    decision_ids = {r.id for r in proposed}
    for plan in plans:
        assert plan.decision_id in decision_ids
        assert plan.side == "buy" and plan.status == "pending"
        assert plan.sleeve == "trend"
        assert plan.entry_orders[0]["type"] == "limit"
        # Priced at the freshest close (the seeded 5m candle), not
        # yesterday's daily close — the same price the portfolio weights
        # are marked with.
        assert plan.reference_price == Decimal("100")


@NEEDS_DB
def test_daily_cycle_blocked_when_halted(session: Session) -> None:
    risk_engine.halt(session, actor="test", reason="test halt")
    session.commit()
    clock = FrozenClock(NOW)
    portfolio = _portfolio(EQUITY, {"BTC-USD": 0.5, "ETH-USD": 0.0})
    rows = daily_cycle.run(session, clock, portfolio)
    session.commit()
    blocked = [r for r in rows if r.action == "blocked"]
    assert blocked and all("halted" in r.reason for r in blocked)
    # A vetoed plan is not saved; the exposure-reducing sell still is.
    plans = session.scalars(select(Plan)).all()
    assert all(p.side == "sell" and p.status == "approved" for p in plans)


@NEEDS_DB
def test_blocked_day_retries_after_halt_lifted(session: Session) -> None:
    """An all-blocked day reruns without re-inserting the day's signals."""
    risk_engine.halt(session, actor="test", reason="test halt")
    clock = FrozenClock(NOW)
    portfolio = _portfolio(EQUITY, {})
    first = daily_cycle.run(session, clock, portfolio)
    session.commit()
    assert all(r.action == "blocked" for r in first)

    risk_engine.resume(session, actor="test")
    clock.advance(timedelta(minutes=10))
    _fresh_5m(session, clock.now())
    second = daily_cycle.run(session, clock, portfolio)  # must not IntegrityError
    session.commit()
    assert any(r.action == "plan_proposed" for r in second)
    assert session.scalars(select(Plan)).all()


@NEEDS_DB
def test_daily_cycle_blocked_when_prices_stale(session: Session) -> None:
    session.execute(delete(CandleRow).where(CandleRow.interval == "5m"))
    session.commit()
    clock = FrozenClock(NOW)
    rows = daily_cycle.run(session, clock, _portfolio(EQUITY, {}))
    session.commit()
    assert all(r.action == "blocked" for r in rows)
    assert all("stale" in r.reason for r in rows)
    assert session.scalar(select(func.count()).select_from(Plan)) == 0
