"""Shared fixtures for the shadow tests (spec G3).

Seeded Postgres via TEST_DATABASE_URL (Compose ``db`` service); helpers
build the candles, decisions and shadow rows the sleeves read. Not in the
spec's file list — the five test modules share this setup.
"""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from trader.ledger.models import (
    AuditLog,
    Balance,
    Candle,
    Decision,
    EquitySnapshot,
    Event,
    Fill,
    FundingRate,
    Heartbeat,
    Order,
    Plan,
    Setting,
    ShadowMark,
    ShadowState,
    ShadowTrade,
    Signal,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

ALL_TABLES = (
    Fill,
    Order,
    Plan,
    Decision,
    Signal,
    Candle,
    FundingRate,
    Setting,
    AuditLog,
    EquitySnapshot,
    Balance,
    Event,
    Heartbeat,
    ShadowTrade,
    ShadowMark,
    ShadowState,
)


def _alembic(*args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture()
def engine():
    assert TEST_URL is not None
    eng = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(eng) as s:
        for table in ALL_TABLES:
            s.execute(delete(table))
        s.commit()
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    """One open session; the test commits when it wants rows visible."""
    with Session(engine) as s:
        yield s


def add_daily(
    session: Session,
    symbol: str,
    start: date,
    closes: list[float],
    source: str = "seed",
) -> None:
    for i, c in enumerate(closes):
        open_time = datetime(
            start.year, start.month, start.day, tzinfo=UTC
        ) + timedelta(days=i)
        session.add(
            Candle(
                symbol=symbol,
                interval="1d",
                open_time=open_time,
                open=Decimal(str(c)),
                high=Decimal(str(c)),
                low=Decimal(str(c)),
                close=Decimal(str(c)),
                volume=Decimal("0"),
                source=source,
            )
        )


def add_5m(
    session: Session,
    symbol: str,
    open_time: datetime,
    close: float,
    source: str = "seed",
) -> None:
    session.add(
        Candle(
            symbol=symbol,
            interval="5m",
            open_time=open_time,
            open=Decimal(str(close)),
            high=Decimal(str(close)),
            low=Decimal(str(close)),
            close=Decimal(str(close)),
            volume=Decimal("0"),
            source=source,
        )
    )


def add_daily_decision(
    session: Session,
    symbol: str,
    ran_at: datetime,
    trend: str | None = "mixed",
) -> Decision:
    details = (
        {
            "regime": {
                "trend": trend,
                "volatility": "calm",
                "sideways": False,
                "vol_percentile": 0.4,
                "adx": 25.0,
            }
        }
        if trend is not None
        else {}
    )
    row = Decision(
        cycle="daily",
        ran_at=ran_at,
        symbol=symbol,
        action="none",
        reason=f"Checked {symbol}.",
        details=details,
    )
    session.add(row)
    return row


def dip_closes(n: int = 20, base: float = 100.0, last: float = 80.0) -> list[float]:
    """Closes whose last 20-day z-score is under -2."""
    return [base] * (n - 1) + [last]
