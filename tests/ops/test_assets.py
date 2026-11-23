"""Asset-list change tracking tests (spec 5.3 restart mark).
Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger.models import Event, Setting
from trader.ops import assets as asset_list

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


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
    engine = create_engine(TEST_URL)
    _alembic("upgrade", "head")
    with Session(engine) as s:
        for model in (Event, Setting):
            s.execute(delete(model))
        s.commit()
        yield s
        for model in (Event, Setting):
            s.execute(delete(model))
        s.commit()
    engine.dispose()


def test_first_record_stores_list_no_event(session: Session) -> None:
    asset_list.record_change(session, FrozenClock(NOW))
    session.commit()

    assert session.get(Setting, asset_list.SETTING_KEY).value == [
        "BTC-USD",
        "ETH-USD",
    ]
    assert asset_list.last_change(session) is None


def test_same_list_writes_nothing(session: Session) -> None:
    clock = FrozenClock(NOW)
    asset_list.record_change(session, clock)
    asset_list.record_change(session, clock)
    session.commit()

    assert session.scalars(select(Event)).all() == []


def test_changed_list_writes_one_event(session: Session) -> None:
    session.add(Setting(key=asset_list.SETTING_KEY, value=["BTC-USD"], updated_at=NOW))
    session.commit()

    asset_list.record_change(session, FrozenClock(NOW))
    session.commit()

    (event,) = session.scalars(select(Event)).all()
    assert event.kind == asset_list.KIND
    assert event.details == {"from": ["BTC-USD"], "to": ["BTC-USD", "ETH-USD"]}
    assert asset_list.last_change(session) == NOW
    assert session.get(Setting, asset_list.SETTING_KEY).value == [
        "BTC-USD",
        "ETH-USD",
    ]
