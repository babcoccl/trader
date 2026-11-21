"""Schema tests: alembic upgrade/downgrade on an empty database, and the
candles uniqueness constraint. Requires TEST_DATABASE_URL pointing at an
empty Postgres database (CI provides one; locally use the Compose `db`)."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trader.ledger.models import Candle

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

EXPECTED_TABLES = {
    "candles",
    "funding_rates",
    "signals",
    "decisions",
    "plans",
    "orders",
    "fills",
    "balances",
    "equity_snapshots",
    "events",
    "heartbeats",
    "settings",
    "audit_log",
    "digests",
}


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
    _alembic("downgrade", "base")
    yield eng
    eng.dispose()


def test_upgrade_then_downgrade(engine) -> None:
    _alembic("upgrade", "head")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    _alembic("downgrade", "base")
    tables = set(inspect(engine).get_table_names())
    assert not (EXPECTED_TABLES & tables)


def test_duplicate_candle_rejected(engine) -> None:
    _alembic("upgrade", "head")
    row = dict(
        symbol="BTC-USD",
        interval="1d",
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        source="test",
    )
    with Session(engine) as session:
        session.add(Candle(**row))
        session.commit()
    with Session(engine) as session:
        session.add(Candle(**row))
        with pytest.raises(IntegrityError):
            session.commit()
