"""Dip shadow sleeve tests (spec G3): z-score parity with the fixture and
``dip.run`` on a seeded database. DB tests need TEST_DATABASE_URL."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from trader.core.clock import FrozenClock
from trader.ledger.models import ShadowMark, ShadowTrade
from trader.shadow import book, dip
from trader.strategy import sleeves
from trader.strategy.regime import Regime

from .conftest import (  # noqa: F401
    add_5m,
    add_daily,
    add_daily_decision,
    dip_closes,
    pytestmark,
)

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "research",
    "fixtures",
)

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def test_zscore_matches_fixture() -> None:
    """z20 of expected_regimes.csv for both assets on every date."""
    daily = pd.read_csv(
        os.path.join(FIXTURES, "btc_eth_daily.csv"), parse_dates=["date"]
    )
    expected = pd.read_csv(os.path.join(FIXTURES, "expected_regimes.csv"))
    for symbol in ("BTC-USD", "ETH-USD"):
        close = daily[f"{symbol}_close"]
        got = np.array([dip.zscore(close.iloc[: i + 1]) for i in range(len(close))])
        want = expected.loc[expected["symbol"] == symbol, "z20"].to_numpy(dtype=float)
        assert np.allclose(got, want, rtol=0, atol=1e-9, equal_nan=True)


def test_dip_trigger_matches_fixture() -> None:
    """'allowed and z under -2' equals the fixture's dip_trigger column."""
    expected = pd.read_csv(os.path.join(FIXTURES, "expected_regimes.csv"))
    spec = sleeves.get("dip")
    checked = 0
    for _, row in expected.iterrows():
        trigger = row["dip_trigger"]
        if pd.isna(trigger) or trigger == "":
            continue
        regime = (
            Regime(row["trend"], None, None, None, None)
            if isinstance(row["trend"], str) and row["trend"]
            else None
        )
        z = float(row["z20"])
        want = trigger is True or trigger == "true"
        assert (sleeves.allowed(spec, regime) and z < dip.Z_ENTRY) == want
        checked += 1
    assert checked > 0


def test_zscore_edges() -> None:
    assert np.isnan(dip.zscore(pd.Series([1.0] * 19)))
    assert np.isnan(dip.zscore(pd.Series([5.0] * 20)))  # zero deviation
    assert dip.zscore(pd.Series([100.0] * 19 + [80.0])) < dip.Z_ENTRY


def _seed_day(
    session, trends=("mixed", "mixed"), price=Decimal("100"), z_entry=True
) -> None:
    """Two symbols, mixed (or given) regimes today, fresh 5m prices."""
    for i, symbol in enumerate(dip.SYMBOLS):
        closes = dip_closes(last=80.0) if z_entry else [100.0] * 21
        add_daily(session, symbol, (TODAY - timedelta(days=len(closes))).date(), closes)
        add_daily_decision(session, symbol, TODAY + timedelta(hours=1), trends[i])
        add_5m(session, symbol, NOW - timedelta(minutes=5), float(price))


def test_dip_buys_on_mixed_dip(session) -> None:
    _seed_day(session)
    clock = FrozenClock(NOW)
    mark = dip.run(session, clock)
    assert mark is not None
    trades = book.open_trades(session, "dip")
    assert {t.symbol for t in trades} == {"BTC-USD", "ETH-USD"}
    spend = trades[0].cost_usd
    assert spend == Decimal("0.25") * Decimal("10000")
    assert "BTC: bought $2,500.00" in mark.note
    assert mark.open_trades == 2
    session.commit()


def test_dip_buys_nothing_off_regime(session) -> None:
    _seed_day(session, trends=("downtrend", "uptrend"))
    mark = dip.run(session, FrozenClock(NOW))
    assert mark is not None
    assert book.open_trades(session, "dip") == []
    assert "downtrend, so no dip-buying" in mark.note
    assert "uptrend, so no dip-buying" in mark.note
    session.commit()


def test_dip_mixed_without_dip_signal(session) -> None:
    _seed_day(session, z_entry=False)
    mark = dip.run(session, FrozenClock(NOW))
    assert mark is not None
    assert book.open_trades(session, "dip") == []
    assert "average; it buys at 2.0." in mark.note
    session.commit()


def test_second_run_same_day_returns_none(session) -> None:
    _seed_day(session)
    clock = FrozenClock(NOW)
    assert dip.run(session, clock) is not None
    session.commit()
    assert dip.run(session, clock) is None


def _tick(session, when: datetime, trend: str = "mixed") -> None:
    """Regime decisions and a fresh 5m price as of ``when``."""
    today = when.replace(hour=0, minute=0, second=0, microsecond=0)
    for symbol in dip.SYMBOLS:
        add_daily_decision(session, symbol, today + timedelta(hours=1), trend)
        add_5m(session, symbol, when - timedelta(minutes=5), 100.0)


def test_sell_after_hold_days(session) -> None:
    _seed_day(session)
    clock = FrozenClock(NOW)
    dip.run(session, clock)
    session.commit()

    # Four days later: still held, no sale.
    clock.advance(timedelta(days=4))
    _tick(session, clock.now())
    mark = dip.run(session, clock)
    assert mark is not None
    trades = book.open_trades(session, "dip")
    assert len(trades) == 2
    assert "holding since" in mark.note
    session.commit()

    # Five days later: sold with the hold-days reason.
    clock.advance(timedelta(days=1))
    _tick(session, clock.now())
    mark = dip.run(session, clock)
    assert mark is not None
    assert book.open_trades(session, "dip") == []
    assert "sold after 5 days, result" in mark.note
    sold = session.query(ShadowTrade).all()
    assert all(t.exit_reason == "held 5 days" for t in sold)
    assert all(t.pnl_usd is not None for t in sold)
    assert mark.closed_trades == 2
    session.commit()


def test_no_daily_decision_returns_none(session) -> None:
    assert dip.run(session, FrozenClock(NOW)) is None
    assert session.query(ShadowMark).count() == 0


def test_stale_price_returns_none(session) -> None:
    _seed_day(session)
    session.commit()
    later = NOW + timedelta(days=2)
    _tick(session, later)
    # 30 minutes after the freshest bar closed (> MAX_PRICE_AGE): stale.
    clock = FrozenClock(later + timedelta(minutes=30))
    assert dip.run(session, clock) is None
    assert session.query(ShadowMark).count() == 0
