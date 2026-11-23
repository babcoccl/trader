"""Shadow book tests (spec G3): buy/sell arithmetic, summary, mark
idempotency. Needs TEST_DATABASE_URL (Compose Postgres)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trader.ledger.models import ShadowMark
from trader.shadow import book

from .conftest import pytestmark  # noqa: F401

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def test_buy_and_sell_arithmetic(session) -> None:
    trade = book.buy(
        session,
        "dip",
        "BTC-USD",
        NOW,
        Decimal("100"),
        Decimal("2500"),
        fee_rate=0.004,
        slippage=0.0001,
    )
    assert trade.entry_price == Decimal("100.01")
    assert trade.entry_fee_usd == Decimal("10")
    assert trade.quantity == Decimal("2490") / Decimal("100.01")
    assert trade.cost_usd == Decimal("2500")

    before = book.summary(session, "dip", {"BTC-USD": Decimal("100")})
    assert before.cash == Decimal("7500")
    assert before.exposure == trade.quantity * Decimal("100")
    assert before.equity == before.cash + before.exposure
    assert before.open_count == 1
    assert before.closed_count == 0
    assert before.fees == Decimal("10")

    sold = book.sell(
        session,
        trade,
        NOW,
        Decimal("110"),
        fee_rate=0.004,
        slippage=0.0001,
        reason="held 5 days",
    )
    assert sold.exit_price == Decimal("109.989")
    expected_pnl = trade.quantity * Decimal("109.989") * Decimal("0.996") - Decimal(
        "2500"
    )
    assert sold.pnl_usd == expected_pnl
    assert sold.exit_reason == "held 5 days"

    after = book.summary(session, "dip", {"BTC-USD": Decimal("110")})
    assert after.open_count == 0
    assert after.closed_count == 1
    assert after.exposure == 0
    assert after.cash == book.START_USD + expected_pnl
    assert after.equity == after.cash
    assert after.fees == Decimal("10") + sold.exit_fee_usd
    session.commit()


def test_selling_a_closed_trade_raises(session) -> None:
    trade = book.buy(
        session, "dip", "BTC-USD", NOW, Decimal("100"), Decimal("100"), 0.004, 0.0
    )
    book.sell(session, trade, NOW, Decimal("100"), 0.004, 0.0, "x")
    with pytest.raises(ValueError, match="already closed"):
        book.sell(session, trade, NOW, Decimal("100"), 0.004, 0.0, "x")


def test_open_trades_filters(session) -> None:
    book.buy(session, "dip", "BTC-USD", NOW, Decimal("100"), Decimal("100"), 0.0, 0.0)
    book.buy(session, "dip", "ETH-USD", NOW, Decimal("10"), Decimal("100"), 0.0, 0.0)
    book.buy(session, "grid", "BTC-USD", NOW, Decimal("100"), Decimal("100"), 0.0, 0.0)
    assert len(book.open_trades(session, "dip")) == 2
    assert [t.symbol for t in book.open_trades(session, "dip", "BTC-USD")] == [
        "BTC-USD"
    ]


def test_mark_is_idempotent(session) -> None:
    day = date(2026, 11, 23)
    first = book.mark(session, "dip", day, {}, "first note")
    assert first.equity_usd == book.START_USD
    second = book.mark(session, "dip", day, {}, "other note")
    assert second.id == first.id
    assert second.note == "first note"
    session.commit()
    assert book.marked(session, "dip", day) is True
    assert book.marked(session, "dip", date(2026, 11, 24)) is False
    assert session.query(ShadowMark).count() == 1
