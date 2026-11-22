"""Risk engine tests (spec 2.1).

Pure-verdict cases run without a database; state, halt and snapshot cases
need TEST_DATABASE_URL (Compose Postgres).
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger.models import AuditLog, Candle, EquitySnapshot, Setting
from trader.risk import engine as risk
from trader.risk.limits import Limits

TEST_URL = os.environ.get("TEST_DATABASE_URL")

NEEDS_DB = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@dataclass
class FakePlan:
    symbol: str
    side: str
    target_weight: Decimal
    worst_case_loss: Decimal


@dataclass
class FakePortfolio:
    equity_usd: Decimal
    weights: dict[str, float]


def _state(
    *,
    halted: bool = False,
    reason: str | None = None,
    hwm: Decimal | None = Decimal("10000"),
    drawdown: float = 0.0,
    ages: dict[str, float] | None = None,
) -> risk.RiskState:
    return risk.RiskState(
        halted=halted,
        halt_reason=reason,
        high_water_mark=hwm,
        drawdown=drawdown,
        price_age_seconds=ages
        if ages is not None
        else {"BTC-USD": 30.0, "ETH-USD": 30.0},
    )


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture()
def session():
    assert TEST_URL is not None
    eng = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(eng) as s:
        for table in (EquitySnapshot, Setting, AuditLog, Candle):
            s.execute(delete(table))
        s.commit()
        yield s
    eng.dispose()


# --- pure verdict tests (no database) --------------------------------------


@settings(max_examples=300)
@given(
    btc_now=st.floats(0.0, 1.0),
    eth_now=st.floats(0.0, 1.0),
    target=st.floats(-1.0, 2.0),
    side=st.sampled_from(["buy", "sell"]),
    loss_frac=st.floats(0.0, 2.0),
)
def test_verdict_never_allows_gross_breach_or_short(
    btc_now: float,
    eth_now: float,
    target: float,
    side: str,
    loss_frac: float,
) -> None:
    """Any plan the engine approves keeps gross <= 1.0 and no short position."""
    assume(btc_now + eth_now <= 1.0)
    equity = Decimal("10000")
    portfolio = FakePortfolio(equity, {"BTC-USD": btc_now, "ETH-USD": eth_now})
    plan = FakePlan(
        "BTC-USD", side, Decimal(str(target)), equity * Decimal(str(loss_frac))
    )
    verdict = risk.check_plan(plan, portfolio, Limits(), _state())
    if not verdict.ok:
        return
    after = dict(portfolio.weights)
    after["BTC-USD"] = target
    assert sum(w for w in after.values() if w > 0) <= 1.0 + 1e-9
    assert all(w >= 0 for w in after.values())


def test_halt_blocks_buys_allows_sells() -> None:
    portfolio = FakePortfolio(Decimal("10000"), {"BTC-USD": 0.5, "ETH-USD": 0.0})
    halted = _state(halted=True, reason="manual halt")
    buy = FakePlan("ETH-USD", "buy", Decimal("0.2"), Decimal("100"))
    verdict = risk.check_plan(buy, portfolio, Limits(), halted)
    assert not verdict.ok
    assert any("halted" in r for r in verdict.reasons)

    sell = FakePlan("BTC-USD", "sell", Decimal("0.2"), Decimal("0"))
    assert risk.check_plan(sell, portfolio, Limits(), halted).ok
    full_exit = FakePlan("BTC-USD", "sell", Decimal("0"), Decimal("0"))
    assert risk.check_plan(full_exit, portfolio, Limits(), halted).ok


def test_sell_short_rejected() -> None:
    portfolio = FakePortfolio(Decimal("10000"), {"BTC-USD": 0.3, "ETH-USD": 0.0})
    short = FakePlan("BTC-USD", "sell", Decimal("-0.1"), Decimal("0"))
    verdict = risk.check_plan(short, portfolio, Limits(), _state())
    assert not verdict.ok
    assert any("short" in r.lower() for r in verdict.reasons)


def test_stale_prices_reject() -> None:
    portfolio = FakePortfolio(Decimal("10000"), {"BTC-USD": 0.0, "ETH-USD": 0.0})
    buy = FakePlan("BTC-USD", "buy", Decimal("0.5"), Decimal("100"))
    assert (
        risk.check_plan(
            buy, portfolio, Limits(), _state(ages={"BTC-USD": 601, "ETH-USD": 30})
        ).ok
        is False
    )
    # No candles at all for the symbol is also stale.
    assert (
        risk.check_plan(buy, portfolio, Limits(), _state(ages={"ETH-USD": 30})).ok
        is False
    )


def test_gross_and_loss_and_cap_reasons() -> None:
    portfolio = FakePortfolio(Decimal("10000"), {"BTC-USD": 0.6, "ETH-USD": 0.6})
    over_gross = FakePlan("BTC-USD", "buy", Decimal("0.5"), Decimal("100"))
    verdict = risk.check_plan(over_gross, portfolio, Limits(), _state())
    assert not verdict.ok
    assert any("Gross exposure" in r for r in verdict.reasons)

    portfolio2 = FakePortfolio(Decimal("10000"), {"BTC-USD": 0.0, "ETH-USD": 0.0})
    big_loss = FakePlan("BTC-USD", "buy", Decimal("0.5"), Decimal("3000"))
    verdict = risk.check_plan(big_loss, portfolio2, Limits(), _state())
    assert not verdict.ok
    assert any("Worst-case loss" in r for r in verdict.reasons)


# --- state, halt, snapshots (database) --------------------------------------


@NEEDS_DB
def test_drawdown_exactly_35_halts(session: Session) -> None:
    clock = FrozenClock(NOW)
    risk.update_equity(session, Decimal("10000"), clock)
    assert risk.state(session).drawdown == 0.0
    clock.advance(timedelta(hours=1))
    risk.update_equity(session, Decimal("6500"), clock)  # exactly -35%
    s = risk.state(session)
    assert s.halted
    assert s.drawdown == pytest.approx(0.35)
    assert s.halt_reason is not None
    # Just under the limit must not halt: replay on a clean mark.
    session.execute(delete(EquitySnapshot))
    session.execute(delete(Setting))
    session.commit()
    risk.update_equity(session, Decimal("10000"), clock)
    risk.update_equity(session, Decimal("6500.01"), clock)
    assert not risk.state(session).halted


@NEEDS_DB
def test_drawdown_halts_at_exact_custom_limit(session: Session) -> None:
    """Float `1 - 0.8` = 0.1999...96 must still halt at a 0.20 limit."""
    session.add(
        Setting(
            key="risk.limits.drawdown_halt", value=0.20, updated_at=datetime.now(UTC)
        )
    )
    session.commit()
    clock = FrozenClock(NOW)
    risk.update_equity(session, Decimal("10000"), clock)
    risk.update_equity(session, Decimal("8000"), clock)
    assert risk.state(session).halted


@NEEDS_DB
def test_resume_resets_high_water_mark(session: Session) -> None:
    clock = FrozenClock(NOW)
    risk.update_equity(session, Decimal("10000"), clock)
    risk.update_equity(session, Decimal("6000"), clock)
    assert risk.state(session).halted
    risk.resume(session, actor="owner")
    session.commit()
    s = risk.state(session)
    assert not s.halted
    assert s.halt_reason is None
    assert s.high_water_mark == Decimal("6000")
    assert s.drawdown == 0.0
    # A further drawdown from the new mark halts again.
    risk.update_equity(session, Decimal("3899.99"), clock)
    assert risk.state(session).halted


@NEEDS_DB
def test_halt_and_audit_log(session: Session) -> None:
    risk.halt(session, actor="owner", reason="testing the big red button")
    session.commit()
    s = risk.state(session)
    assert s.halted and s.halt_reason == "testing the big red button"
    actions = session.scalars(select(AuditLog.action)).all()
    assert set(actions) == {"set risk.halted", "set risk.halt_reason"}

    risk.flatten_requested(session, actor="owner")
    session.commit()
    assert session.get(Setting, "risk.flatten_requested").value is True

    risk.flatten_done(session, actor="executor")
    session.commit()
    assert session.get(Setting, "risk.flatten_requested").value is False
    assert session.scalars(
        select(AuditLog.actor).where(AuditLog.action == "set risk.flatten_requested")
    ).all() == ["owner", "executor"]


@NEEDS_DB
def test_state_reads_candle_age(session: Session) -> None:
    # The candle closed 120s ago: opened 7 minutes ago, closed 2 minutes ago.
    session.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=NOW - timedelta(minutes=7),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("0"),
            source="test",
        )
    )
    session.commit()
    s = risk.state(session, clock=FrozenClock(NOW))
    assert s.price_age_seconds["BTC-USD"] == pytest.approx(120.0)
    assert "ETH-USD" not in s.price_age_seconds


@NEEDS_DB
def test_price_age_from_candle_close(session: Session) -> None:
    """Spec R1: the collector stores only closed 5m candles, so price age is
    measured from the newest candle's close, not its open."""
    session.add(
        Candle(
            symbol="BTC-USD",
            interval="5m",
            open_time=datetime(2026, 11, 19, 23, 55, tzinfo=UTC),
            open=Decimal("60000"),
            high=Decimal("60000"),
            low=Decimal("60000"),
            close=Decimal("60000"),
            volume=Decimal("1"),
            source="bitstamp",
        )
    )
    session.commit()
    portfolio = FakePortfolio(Decimal("10000"), {"BTC-USD": 0.0, "ETH-USD": 0.0})
    buy = FakePlan("BTC-USD", "buy", Decimal("0.5"), Decimal("100"))

    # At the 00:05 scheduler tick the newest stored candle closed at 00:00.
    s = risk.state(
        session, clock=FrozenClock(datetime(2026, 11, 20, 0, 5, 0, 50000, tzinfo=UTC))
    )
    assert s.price_age_seconds["BTC-USD"] == pytest.approx(300.05)
    assert risk.check_plan(buy, portfolio, Limits(), s).ok

    # Two candles missing: age is over the 600s limit and the buy is vetoed.
    s = risk.state(
        session,
        clock=FrozenClock(datetime(2026, 11, 20, 0, 15, 0, 50000, tzinfo=UTC)),
    )
    assert s.price_age_seconds["BTC-USD"] == pytest.approx(900.05)
    verdict = risk.check_plan(buy, portfolio, Limits(), s)
    assert not verdict.ok
    assert any("stale" in r for r in verdict.reasons)

    # Exactly 600s passes: the comparison is strictly `>`.
    s = risk.state(
        session, clock=FrozenClock(datetime(2026, 11, 20, 0, 10, 0, tzinfo=UTC))
    )
    assert s.price_age_seconds["BTC-USD"] == pytest.approx(600.0)
    assert risk.check_plan(buy, portfolio, Limits(), s).ok


@NEEDS_DB
def test_limits_load_defaults_and_overrides(session: Session) -> None:
    assert Limits.load(session) == Limits()
    session.add(
        Setting(key="risk.limits.max_gross", value=0.5, updated_at=datetime.now(UTC))
    )
    session.commit()
    assert Limits.load(session).max_gross == 0.5
    session.add(Setting(key="risk.limits.bogus", value=1, updated_at=datetime.now(UTC)))
    with pytest.raises(ValueError, match="unknown risk setting"):
        Limits.load(session)
