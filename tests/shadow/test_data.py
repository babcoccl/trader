"""Shadow data tests (spec G3): configured symbols, regime lookup, price
staleness, candle de-duplication. Needs TEST_DATABASE_URL."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from trader.shadow import data

from .conftest import add_5m, add_daily, add_daily_decision, pytestmark  # noqa: F401

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def test_symbols_drops_unconfigured(session) -> None:
    assert data.symbols(("BTC-USD", "DOGE-USD", "ETH-USD")) == (
        "BTC-USD",
        "ETH-USD",
    )
    assert data.symbols(("DOGE-USD",)) == ()


def test_regime_today_and_latest(session) -> None:
    add_daily_decision(session, "BTC-USD", TODAY - timedelta(days=1), "downtrend")
    add_daily_decision(session, "BTC-USD", TODAY + timedelta(hours=1), "mixed")
    add_daily_decision(session, "ETH-USD", TODAY + timedelta(hours=1), None)
    session.commit()

    regime = data.regime_today(session, "BTC-USD", TODAY)
    assert regime is not None and regime.trend == "mixed"
    # A row without a regime key is ignored.
    assert data.regime_today(session, "ETH-USD", TODAY) is None
    # latest_regime has no ran_at condition.
    assert data.latest_regime(session, "ETH-USD") is None
    older = data.latest_regime(session, "BTC-USD")
    assert older is not None and older.trend == "mixed"


def test_regime_today_ignores_yesterday(session) -> None:
    add_daily_decision(session, "BTC-USD", TODAY - timedelta(days=1), "mixed")
    session.commit()
    assert data.regime_today(session, "BTC-USD", TODAY) is None
    latest = data.latest_regime(session, "BTC-USD")
    assert latest is not None and latest.trend == "mixed"


def test_price_fresh_and_stale(session) -> None:
    add_5m(session, "BTC-USD", NOW - timedelta(minutes=5), 60000)
    add_5m(session, "ETH-USD", NOW - timedelta(minutes=16) - timedelta(minutes=5), 3000)
    session.commit()
    assert data.price(session, "BTC-USD", NOW) == Decimal("60000")
    # The ETH bar closed 16 minutes ago: stale.
    assert data.price(session, "ETH-USD", NOW) is None
    assert data.price(session, "SOL-USD", NOW) is None


def test_daily_closes_dedup(session) -> None:
    day = date(2026, 11, 1)
    add_daily(session, "BTC-USD", day, [100.0, 101.0, 102.0], source="alpha")
    add_daily(session, "BTC-USD", day, [100.5, 101.5, 102.5], source="beta")
    # One more alpha row so alpha is the source covering the most bars.
    add_daily(session, "BTC-USD", day + timedelta(days=3), [103.0], source="alpha")
    session.commit()
    closes = data.daily_closes(session, "BTC-USD", TODAY + timedelta(days=30))
    assert len(closes) == 4
    assert closes.iloc[0] == 100.0  # alpha wins the shared days


def test_bars_after_dedup_and_closed_only(session) -> None:
    t = NOW - timedelta(minutes=20)
    add_5m(session, "BTC-USD", t, 100, source="alpha")
    add_5m(session, "BTC-USD", t, 101, source="beta")
    add_5m(session, "BTC-USD", t + timedelta(minutes=5), 102, source="alpha")
    add_5m(session, "BTC-USD", t - timedelta(minutes=5), 99, source="alpha")
    # A bar still open at `now` is excluded.
    add_5m(session, "BTC-USD", NOW - timedelta(minutes=2), 103, source="alpha")
    session.commit()
    bars = data.bars_after(session, "BTC-USD", t - timedelta(minutes=5), NOW)
    assert list(bars["close"]) == [100, 102]
