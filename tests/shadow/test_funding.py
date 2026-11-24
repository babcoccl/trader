"""Funding shadow sleeve tests (spec G4): the 7-day average over stored
rates, and ``funding.run`` on a seeded database. Needs TEST_DATABASE_URL."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.data.sources.coinbase_funding import VENUE
from trader.ledger.models import FundingRate, ShadowTrade
from trader.shadow import book, funding

from .conftest import add_5m, add_daily_decision, pytestmark  # noqa: F401
from .test_isolation import _counts

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def add_funding(
    session: Session,
    symbol: str,
    start: datetime,
    hours: int,
    rate: str,
    venue: str = VENUE,
) -> None:
    """Hourly funding rows starting at ``start``."""
    for i in range(hours):
        session.add(
            FundingRate(
                symbol=symbol,
                venue=venue,
                time=start + timedelta(hours=i),
                rate=Decimal(rate),
                interval_hours=1,
            )
        )


def _seed_day(
    session: Session,
    trends: tuple[str, ...] = ("downtrend", "downtrend"),
    rate: str = "-0.00001",
    hours: int = 168,
) -> None:
    """Two symbols, today's regime and a fresh 5m price; ``hours`` of
    funding readings ending just before today."""
    for i, symbol in enumerate(funding.SYMBOLS):
        add_daily_decision(session, symbol, TODAY + timedelta(hours=1), trends[i])
        add_5m(session, symbol, NOW - timedelta(minutes=5), 100.0)
        add_funding(session, symbol, TODAY - timedelta(days=7), hours, rate)


def _tick(session: Session, when: datetime, trend: str) -> None:
    today = when.replace(hour=0, minute=0, second=0, microsecond=0)
    for symbol in funding.SYMBOLS:
        add_daily_decision(session, symbol, today + timedelta(hours=1), trend)
        add_5m(session, symbol, when - timedelta(minutes=5), 100.0)


def test_average_under_threshold_is_none(session: Session) -> None:
    add_funding(session, "BTC-USD", TODAY - timedelta(days=7), 125, "-0.00001")
    session.commit()
    avg, count = funding.average(session, "BTC-USD", TODAY)
    assert avg is None
    assert count == 125


def test_average_at_threshold_and_window(session: Session) -> None:
    add_funding(session, "BTC-USD", TODAY - timedelta(days=7), 126, "-0.00001")
    # Rows at or after today and rows of another venue are ignored.
    session.add(
        FundingRate(
            symbol="BTC-USD",
            venue=VENUE,
            time=TODAY,
            rate=Decimal("9"),
            interval_hours=1,
        )
    )
    session.add(
        FundingRate(
            symbol="BTC-USD",
            venue="binance",
            time=TODAY - timedelta(hours=1),
            rate=Decimal("9"),
            interval_hours=8,
        )
    )
    session.commit()
    avg, count = funding.average(session, "BTC-USD", TODAY)
    assert count == 126
    assert avg == Decimal("-0.00001")


def test_downtrend_negative_buys(session: Session) -> None:
    _seed_day(session)
    mark = funding.run(session, FrozenClock(NOW))
    assert mark is not None
    trades = book.open_trades(session, funding.NAME)
    assert {t.symbol for t in trades} == {"BTC-USD", "ETH-USD"}
    assert trades[0].cost_usd == Decimal("0.25") * Decimal("10000")
    assert "BTC: downtrend and 7-day funding is -0.0010% an hour; bought" in mark.note
    session.commit()


def test_positive_average_buys_nothing(session: Session) -> None:
    _seed_day(session, rate="0.000004")
    mark = funding.run(session, FrozenClock(NOW))
    assert mark is not None
    assert book.open_trades(session, funding.NAME) == []
    assert "downtrend but 7-day funding is +0.0004% an hour; no position" in mark.note
    session.commit()


def test_off_regime_buys_nothing(session: Session) -> None:
    _seed_day(session, trends=("mixed", "uptrend"))
    mark = funding.run(session, FrozenClock(NOW))
    assert mark is not None
    assert book.open_trades(session, funding.NAME) == []
    assert "mixed, so it holds nothing" in mark.note
    assert "uptrend, so it holds nothing" in mark.note
    session.commit()


def test_sold_when_regime_leaves_downtrend(session: Session) -> None:
    _seed_day(session)
    clock = FrozenClock(NOW)
    funding.run(session, clock)
    session.commit()

    clock.advance(timedelta(days=1))
    _tick(session, clock.now(), "uptrend")
    mark = funding.run(session, clock)
    assert mark is not None
    assert book.open_trades(session, funding.NAME) == []
    sold = session.scalars(
        select(ShadowTrade).where(ShadowTrade.sleeve == funding.NAME)
    ).all()
    assert all(t.exit_reason == "trend no longer off" for t in sold)
    session.commit()


def test_sold_when_average_turns_positive(session: Session) -> None:
    _seed_day(session)
    clock = FrozenClock(NOW)
    funding.run(session, clock)
    session.commit()

    clock.advance(timedelta(days=1))
    later_today = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    session.execute(delete(FundingRate))
    for symbol in funding.SYMBOLS:
        add_funding(session, symbol, later_today - timedelta(days=7), 168, "0.00001")
    _tick(session, clock.now(), "downtrend")
    mark = funding.run(session, clock)
    assert mark is not None
    assert book.open_trades(session, funding.NAME) == []
    sold = session.scalars(
        select(ShadowTrade).where(ShadowTrade.sleeve == funding.NAME)
    ).all()
    assert all(t.exit_reason == "funding no longer negative" for t in sold)
    session.commit()


def test_funding_run_writes_no_trading_rows(session: Session) -> None:
    """Section E rule 2: a funding buy and sell touch only shadow tables."""
    _seed_day(session)
    session.commit()
    before = _counts(session)
    funding.run(session, FrozenClock(NOW))
    assert _counts(session) == before  # buys

    later = NOW + timedelta(days=1)
    session.execute(delete(FundingRate))
    today = later.replace(hour=0, minute=0, second=0, microsecond=0)
    for symbol in funding.SYMBOLS:
        add_funding(session, symbol, today - timedelta(days=7), 168, "0.00001")
    _tick(session, later, "downtrend")
    session.commit()
    before = _counts(session)
    funding.run(session, FrozenClock(later))
    assert _counts(session) == before  # sells
    session.commit()


def test_too_few_readings_changes_nothing(session: Session) -> None:
    """With under MIN_READINGS rows nothing is bought and an open trade is
    kept (spec G4 test 4)."""
    _seed_day(session, hours=0)
    mark = funding.run(session, FrozenClock(NOW))
    assert mark is not None
    assert book.open_trades(session, funding.NAME) == []
    assert "collecting funding rates, 0 of 126 hourly readings" in mark.note
    session.commit()

    # Now buy, then drain the rows: the open trade survives a None average.
    session.execute(delete(FundingRate))
    for symbol in funding.SYMBOLS:
        add_funding(session, symbol, TODAY - timedelta(days=7), 168, "-0.00001")
    session.commit()

    later = NOW + timedelta(days=1)
    _tick(session, later, "downtrend")
    mark2 = funding.run(session, FrozenClock(later))
    assert mark2 is not None
    session.commit()

    session.execute(delete(FundingRate))
    later2 = later + timedelta(days=1)
    _tick(session, later2, "downtrend")
    mark3 = funding.run(session, FrozenClock(later2))
    assert mark3 is not None
    assert len(book.open_trades(session, funding.NAME)) == 2
    assert "collecting funding rates" in mark3.note
    session.commit()
