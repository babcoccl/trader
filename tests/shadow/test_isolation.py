"""Isolation tests (spec G3): shadow code never imports the trading
modules and never writes a trading table."""

import ast
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.ledger.models import (
    Balance,
    Decision,
    EquitySnapshot,
    Fill,
    Order,
    Plan,
)
from trader.shadow import runner

from .conftest import add_5m, add_daily, add_daily_decision, pytestmark  # noqa: F401
from .test_runner import _factory

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)

SHADOW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
    "trader",
    "shadow",
)
FORBIDDEN_ROOTS = ("plans", "execution", "risk")


def _forbidden_import(module: str | None, names: tuple[str, ...] = ()) -> str | None:
    """The forbidden ``trader`` subtree ``module``/``names`` import, or None."""
    if module is None:
        return None
    parts = module.split(".")
    if parts[0] != "trader":
        return None
    if len(parts) > 1 and parts[1] in FORBIDDEN_ROOTS:
        return module
    if len(parts) == 1:
        hit = next((n for n in names if n in FORBIDDEN_ROOTS), None)
        if hit is not None:
            return f"trader.{hit}"
    return None


TRADING_TABLES = (Plan, Order, Fill, Balance, EquitySnapshot, Decision)


def test_no_forbidden_imports() -> None:
    """Parse every file under ``src/trader/shadow/`` and fail on any import
    from the ``trader.plans``, ``trader.execution`` or ``trader.risk``
    subtrees — including ``from trader import risk`` and submodules."""
    files = [f for f in os.listdir(SHADOW_DIR) if f.endswith(".py")]
    assert files
    for name in files:
        tree = ast.parse(open(os.path.join(SHADOW_DIR, name)).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                bad = _forbidden_import(node.module, tuple(a.name for a in node.names))
                assert bad is None, (name, bad)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bad = _forbidden_import(alias.name)
                    assert bad is None, (name, bad)


def _counts(session: Session) -> dict[str, int]:
    return {
        t.__tablename__: session.scalar(select(func.count()).select_from(t)) or 0
        for t in TRADING_TABLES
    }


def test_shadow_run_writes_no_trading_rows(engine, session) -> None:
    """Around a ``runner.run`` that buys and a later one that sells, the
    row counts of the trading tables do not change."""
    day = TODAY - timedelta(days=20)
    for symbol in ("BTC-USD", "ETH-USD"):
        add_daily(session, symbol, day.date(), [100.0] * 19 + [80.0])
        add_daily_decision(session, symbol, TODAY + timedelta(hours=1), "mixed")
        add_5m(session, symbol, NOW - timedelta(minutes=5), 100.0)
    session.commit()

    before = _counts(session)
    runner.run(_factory(engine), FrozenClock(NOW))
    with Session(engine) as s:
        assert _counts(s) == before

    later = NOW + timedelta(days=5)
    tick = later.replace(hour=0, minute=0, second=0, microsecond=0)
    for symbol in ("BTC-USD", "ETH-USD"):
        add_daily_decision(session, symbol, tick + timedelta(hours=1), "mixed")
        add_5m(session, symbol, later - timedelta(minutes=5), 100.0)
    session.commit()
    before = _counts(session)
    runner.run(_factory(engine), FrozenClock(later))
    with Session(engine) as s:
        assert _counts(s) == before
        # And the sleeve did trade hypothetically.
        from trader.ledger.models import ShadowMark, ShadowTrade

        assert s.scalar(select(func.count()).select_from(ShadowTrade)) == 4
        # Two marks per run day: the dip sleeve plus the funding sleeve
        # (spec G4, in the runner but never trading).
        assert s.scalar(select(func.count()).select_from(ShadowMark)) == 4
