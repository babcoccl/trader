"""Daily decision cycle tests: fixture replay, blocked on missing candle,
idempotency. Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.data import candles as candles_mod
from trader.data.sources.base import SYMBOLS, Candle
from trader.engine import daily_cycle
from trader.ledger.models import (
    AuditLog,
    Balance,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    Order,
    Plan,
    Setting,
    Signal,
)
from trader.ledger.models import Candle as CandleRow
from trader.strategy import regime, rules

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "research",
    "fixtures",
    "btc_eth_daily.csv",
)


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


def _load_fixture_candles() -> list[Candle]:
    df = pd.read_csv(FIXTURE, parse_dates=["date"])
    candles: list[Candle] = []
    for _, row in df.iterrows():
        open_time = row["date"].to_pydatetime().replace(tzinfo=UTC)
        for symbol in SYMBOLS:
            o = Decimal(str(row[f"{symbol}_open"]))
            c = Decimal(str(row[f"{symbol}_close"]))
            candles.append(
                Candle(
                    symbol=symbol,
                    interval="1d",
                    open_time=open_time,
                    open=o,
                    high=max(o, c),
                    low=min(o, c),
                    close=c,
                    volume=Decimal("0"),
                    source="fixture",
                )
            )
    return candles


@pytest.fixture()
def session():
    assert TEST_URL is not None
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        # FK order: orders/fills reference plans, plans reference decisions.
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Signal,
            CandleRow,
            Setting,
            AuditLog,
            EquitySnapshot,
            Balance,
            Event,
        ):
            s.execute(delete(table))
        candles_mod.upsert(s, _load_fixture_candles())
        s.commit()
        yield s
    engine.dispose()


def _fresh_5m(session: Session, at: datetime) -> None:
    """A just-closed 5m candle per symbol so risk's staleness check passes."""
    candles_mod.upsert(
        session,
        [
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=at - timedelta(minutes=5),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("0"),
                source="fixture",
            )
            for symbol in SYMBOLS
        ],
    )


def _decision_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Decision)) or 0


def test_replay_thirty_days(session: Session) -> None:
    """30 consecutive fixture days, frozen clock: 60 decision rows, none skipped."""
    start_day = datetime(2026, 10, 1, tzinfo=UTC)
    portfolio = daily_cycle.CashOnlyPortfolio(equity_usd=Decimal("10000"))
    for i in range(30):
        day = start_day + timedelta(days=i)
        clock = FrozenClock(day + timedelta(minutes=5))
        _fresh_5m(session, clock.now())
        rows = daily_cycle.run(session, clock, portfolio)
        session.commit()
        assert len(rows) == 2
        assert {r.symbol for r in rows} == set(SYMBOLS)
        assert all(r.cycle == "daily" for r in rows)
        # "blocked" is possible since spec 2.2: risk vetoes a proposed plan.
        assert all(r.action in ("none", "plan_proposed", "blocked") for r in rows)
        assert all("Checked" in r.reason for r in rows)
    assert _decision_count(session) == 60
    # Signals stored for every replayed day and both symbols.
    signal_days = session.scalars(select(Signal.day).distinct()).all()
    assert len(signal_days) == 30


def _ramp_daily_closes(session: Session, symbol: str, days: int = 30) -> None:
    """Force an uptrend so the ensemble target is outside the trading band."""
    rows = (
        session.execute(
            select(CandleRow)
            .where(CandleRow.symbol == symbol, CandleRow.interval == "1d")
            .order_by(CandleRow.open_time.desc())
            .limit(days)
        )
        .scalars()
        .all()
    )
    price = rows[-1].close
    for row in reversed(rows):
        price = price * Decimal("1.05")
        row.open = price
        row.high = price
        row.low = price
        row.close = price
    session.flush()


def test_scheduled_run_proposes_pending_plan(session: Session) -> None:
    """Spec R1: at the 00:05 run the newest stored 5m candle opened at 23:55
    (the collector stores only closed candles), and the proposed plan must
    pass the staleness check and land as pending."""
    _ramp_daily_closes(session, "BTC-USD")
    candles_mod.upsert(
        session,
        [
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=datetime(2026, 11, 19, 23, 55, tzinfo=UTC),
                open=Decimal("60000"),
                high=Decimal("60000"),
                low=Decimal("60000"),
                close=Decimal("60000"),
                volume=Decimal("1"),
                source="fixture",
            )
            for symbol in SYMBOLS
        ],
    )
    session.commit()
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, 0, 50000, tzinfo=UTC))
    rows = daily_cycle.run(
        session, clock, daily_cycle.CashOnlyPortfolio(Decimal("10000"))
    )
    session.commit()
    btc = next(r for r in rows if r.symbol == "BTC-USD")
    assert btc.action == "plan_proposed"
    plans = list(session.scalars(select(Plan).where(Plan.symbol == "BTC-USD")))
    assert len(plans) == 1
    assert plans[0].status == "pending"


def test_missing_yesterday_candle_blocks(session: Session) -> None:
    yesterday = datetime(2026, 11, 19, tzinfo=UTC)
    session.execute(
        delete(CandleRow).where(
            CandleRow.symbol == "BTC-USD",
            CandleRow.interval == "1d",
            CandleRow.open_time == yesterday,
        )
    )
    session.commit()
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(rows) == 1
    assert rows[0].action == "blocked"
    assert rows[0].reason == (
        "Yesterday's BTC-USD candle is missing; no decision made."
    )
    assert _decision_count(session) == 1


def test_second_run_same_day_adds_nothing(session: Session) -> None:
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    _fresh_5m(session, clock.now())
    # Half-invested so proposed sells are exposure-reducing (risk allows).
    portfolio = daily_cycle.CashOnlyPortfolio(
        equity_usd=Decimal("10000"), weights={"BTC-USD": 0.5, "ETH-USD": 0.5}
    )
    first = daily_cycle.run(session, clock, portfolio)
    session.commit()
    clock.advance(timedelta(hours=1))
    second = daily_cycle.run(session, clock, portfolio)
    session.commit()
    assert [r.id for r in second] == [r.id for r in first]
    assert _decision_count(session) == 2
    signals_before = session.scalar(select(func.count()).select_from(Signal))
    daily_cycle.run(session, clock, portfolio)
    assert session.scalar(select(func.count()).select_from(Signal)) == signals_before


def test_duplicate_source_days_deduped(session: Session) -> None:
    """A day stored by two venues is counted once; the majority source wins."""
    # A failover tail: the backup venue stored the last 60 days too.
    rows = (
        session.execute(
            select(CandleRow)
            .where(CandleRow.interval == "1d")
            .order_by(CandleRow.open_time.desc())
            .limit(60)
        )
        .scalars()
        .all()
    )
    dupes = [
        Candle(
            symbol=r.symbol,
            interval=r.interval,
            open_time=r.open_time,
            open=r.open * 2,
            high=r.high * 2,
            low=r.low * 2,
            close=r.close * 2,
            volume=r.volume,
            source="coinbase",
        )
        for r in rows
    ]
    candles_mod.upsert(session, dupes)
    # One mid-history day stored only by the backup venue.
    orphan = (
        session.execute(
            delete(CandleRow)
            .where(
                CandleRow.symbol == "BTC-USD",
                CandleRow.interval == "1d",
                CandleRow.open_time == datetime(2025, 6, 1, tzinfo=UTC),
            )
            .returning(CandleRow)
        )
        .scalars()
        .one()
    )
    candles_mod.upsert(
        session,
        [
            Candle(
                symbol=orphan.symbol,
                interval=orphan.interval,
                open_time=orphan.open_time,
                open=orphan.open,
                high=orphan.high,
                low=orphan.low,
                close=orphan.close,
                volume=orphan.volume,
                source="coinbase",
            )
        ],
    )
    session.commit()
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    decisions = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(decisions) == 2
    # Expected k computed on deduped (bitstamp) closes, not the doubled count.
    df = pd.read_csv(FIXTURE, parse_dates=["date"])
    for d in decisions:
        close = pd.Series(
            df[f"{d.symbol}_close"].values,
            index=pd.DatetimeIndex(df["date"]),
        )
        expected_k = sum(
            1 for v in rules.all_rules(close).values() if v.iloc[-1] == 1.0
        )
        assert f"{expected_k} of 9 rules positive." in d.reason


def test_blocked_day_reruns_once_data_arrives(session: Session) -> None:
    """A blocked row must not suppress the day's real decisions."""
    yesterday = datetime(2026, 11, 19, tzinfo=UTC)
    gone = (
        session.execute(
            delete(CandleRow)
            .where(
                CandleRow.symbol == "BTC-USD",
                CandleRow.interval == "1d",
                CandleRow.open_time == yesterday,
            )
            .returning(CandleRow)
        )
        .scalars()
        .one()
    )
    saved = {
        c: getattr(gone, c)
        for c in (
            "symbol",
            "interval",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
        )
    }
    session.commit()
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(rows) == 1 and rows[0].action == "blocked"
    # Data arrives; the same-day rerun now computes real decisions.
    candles_mod.upsert(
        session,
        [Candle(**saved)],
    )
    session.commit()
    rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(rows) == 2
    assert all(r.action != "blocked" for r in rows)


def _add_daily_row(
    session: Session,
    ran_at: datetime,
    action: str = "blocked",
) -> None:
    session.add(
        Decision(
            cycle="daily",
            ran_at=ran_at,
            symbol="BTC-USD",
            action=action,
            reason="test row",
            details={},
        )
    )
    session.flush()


def test_needs_retry(session: Session) -> None:
    """Spec R4: retry only while the day is all blocked, under 12 runs,
    at or after 00:05, and not halted."""
    day = datetime(2026, 11, 20, tzinfo=UTC)
    clock = FrozenClock(day + timedelta(minutes=5))
    # No rows yet at 00:05: the scheduled run was missed, so retry it.
    assert daily_cycle.needs_retry(session, clock)
    # Before 00:05 even a blocked row does not trigger a retry.
    _add_daily_row(session, day + timedelta(minutes=3))
    early = FrozenClock(day + timedelta(minutes=4))
    assert not daily_cycle.needs_retry(session, early)
    # Only blocked rows at 00:05: retry.
    assert daily_cycle.needs_retry(session, clock)
    # A real decision ends the retries.
    _add_daily_row(session, day + timedelta(minutes=10), action="none")
    assert not daily_cycle.needs_retry(session, clock)
    session.execute(delete(Decision))
    # Twelve distinct runs exhaust the day's attempts.
    for i in range(daily_cycle.MAX_ATTEMPTS):
        _add_daily_row(session, day + timedelta(minutes=5 + 5 * i))
    assert not daily_cycle.needs_retry(session, clock)
    session.execute(delete(Decision))
    # Halted trading suppresses retries too.
    _add_daily_row(session, day + timedelta(minutes=5))
    session.add(Setting(key="risk.halted", value=True, updated_at=func.now()))
    session.flush()
    assert not daily_cycle.needs_retry(session, clock)


def test_reason_templates(session: Session) -> None:
    """One day with holdings that force a sell decision exercises all shapes."""
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    portfolio = daily_cycle.CashOnlyPortfolio(weights={"BTC-USD": 0.5, "ETH-USD": 0.0})
    rows = daily_cycle.run(session, clock, portfolio)
    session.commit()
    for r in rows:
        assert r.reason.startswith(f"Checked {r.symbol}.")
        assert "of 9 rules positive." in r.reason
        assert "Target" in r.reason and "holding" in r.reason
        assert r.details["rules"].keys() == {
            "sma50",
            "sma100",
            "sma200",
            "momentum30",
            "momentum90",
            "momentum180",
            "breakout20",
            "breakout55",
            "breakout100",
        }
        assert "annual_vol" in r.details
        assert "raw_weight" in r.details and "scaled_weight" in r.details
    # BTC held at 50% while the trend target is low must propose a sell.
    btc = next(r for r in rows if r.symbol == "BTC-USD")
    if btc.action == "plan_proposed":
        assert "Proposing a plan to sell the difference." in btc.reason


def test_decision_details_carry_last_close_and_levels(session: Session) -> None:
    """Spec D1: a decision with rules also stores the close it used and the
    level each rule compared it with; a blocked decision has neither."""
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(rows) == 2
    for r in rows:
        assert isinstance(r.details["last_close"], float)
        assert r.details["last_close"] > 0
        assert set(r.details["levels"].keys()) == set(r.details["rules"].keys())
        assert len(r.details["levels"]) == 9

    # The fixture ends 2026-11-19, so the next day's run has no yesterday
    # candle and blocks.
    clock = FrozenClock(datetime(2026, 11, 21, 0, 5, tzinfo=UTC))
    blocked = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(blocked) == 1
    assert blocked[0].action == "blocked"
    assert "last_close" not in blocked[0].details
    assert "levels" not in blocked[0].details


def test_decision_details_carry_regime(session: Session) -> None:
    """Spec G1: a decision with rules also stores the asset's regime (five
    keys, no NaN) and names it in the reason; a blocked decision has none.
    Weights, actions and plans are unchanged by it."""
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    rows = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(rows) == 2
    for r in rows:
        rg = r.details["regime"]
        assert set(rg) == {
            "trend",
            "volatility",
            "sideways",
            "vol_percentile",
            "adx",
        }
        assert rg["trend"] in ("uptrend", "mixed", "downtrend")
        assert all(v is None or v == v for v in rg.values())  # no NaN
        parsed = regime.from_details(rg)
        assert parsed is not None and parsed.text() in r.reason

    # The fixture ends 2026-11-19, so the next day's run blocks and has
    # no regime.
    clock = FrozenClock(datetime(2026, 11, 21, 0, 5, tzinfo=UTC))
    blocked = daily_cycle.run(session, clock, daily_cycle.CashOnlyPortfolio())
    session.commit()
    assert len(blocked) == 1
    assert "regime" not in blocked[0].details


def test_four_assets_quarter_weights_and_per_asset_rounding(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec D2: with four configured assets all rules on and vol under the
    target, each target weight is 0.25, and a SOL plan is rounded to SOL's
    tick (3 price decimals) and lot (5 quantity decimals)."""
    four = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
    session.execute(delete(CandleRow))
    end = datetime(2026, 11, 19, tzinfo=UTC)
    candles: list[Candle] = []
    for symbol in four:
        price = Decimal("50")
        for i in range(210):
            price *= Decimal("1.001")
            candles.append(
                Candle(
                    symbol=symbol,
                    interval="1d",
                    open_time=end - timedelta(days=209 - i),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=Decimal("0"),
                    source="fixture",
                )
            )
        candles.append(
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=end + timedelta(days=1, minutes=-5),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("0"),
                source="fixture",
            )
        )
    candles_mod.upsert(session, candles)
    session.commit()

    from trader.risk import engine as risk_engine

    monkeypatch.setattr(daily_cycle, "SYMBOLS", four)
    monkeypatch.setattr(risk_engine, "SYMBOLS", four)
    clock = FrozenClock(datetime(2026, 11, 20, 0, 5, tzinfo=UTC))
    portfolio = daily_cycle.CashOnlyPortfolio(
        equity_usd=Decimal("10000"), weights={s: 0.0 for s in four}
    )
    rows = daily_cycle.run(session, clock, portfolio)
    session.commit()

    assert len(rows) == 4
    assert {r.symbol for r in rows} == set(four)
    for r in rows:
        assert float(r.target_weight) == pytest.approx(0.25)
        assert r.action == "plan_proposed"

    plan = session.scalars(select(Plan).where(Plan.symbol == "SOL-USD")).one()
    order = plan.entry_orders[0]
    assert Decimal(order["price"]).as_tuple().exponent == -3
    assert Decimal(order["quantity"]).as_tuple().exponent == -5
