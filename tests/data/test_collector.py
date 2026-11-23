"""Collector loop tests: per-minute storage, heartbeat, and source failover.
Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.data.collector import Collector
from trader.data.sources.base import Candle
from trader.ledger.models import Candle as CandleRow
from trader.ledger.models import Event, Heartbeat

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

NOW = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)


class FakeSource:
    """Returns synthetic candles for any requested range."""

    def __init__(self, name: str = "fake", fail: bool = False) -> None:
        self.name = name
        self._fail = fail

    def fetch(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        if self._fail:
            raise ConnectionError(f"{self.name} down")
        step = 86400 if interval == "1d" else 300
        out = []
        t = int(start.timestamp())
        while t + step <= int(end.timestamp()):
            out.append(
                Candle(
                    symbol=symbol,
                    interval=interval,
                    open_time=datetime.fromtimestamp(t, UTC),
                    open=Decimal("1"),
                    high=Decimal("1"),
                    low=Decimal("1"),
                    close=Decimal("1"),
                    volume=Decimal("1"),
                    source=self.name,
                )
            )
            t += step
        return out


class _Frozen:
    def now(self) -> datetime:
        return NOW


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
        for model in (CandleRow, Event, Heartbeat):
            s.execute(delete(model))
        s.commit()
        yield s
        for model in (CandleRow, Event, Heartbeat):
            s.execute(delete(model))
        s.commit()
    engine.dispose()


def test_run_once_stores_recent_candles_and_heartbeat(session: Session) -> None:
    collector = Collector(sources=[FakeSource()], clock=_Frozen())
    written = collector.run_once(session)
    session.commit()

    # 2 closed 5m candles + 1 daily candle per symbol = 6.
    assert written == 6
    five_m = session.scalars(select(CandleRow).where(CandleRow.interval == "5m")).all()
    assert len(five_m) == 4
    daily = session.scalars(select(CandleRow).where(CandleRow.interval == "1d")).all()
    assert {r.open_time for r in daily} == {datetime(2026, 11, 18, tzinfo=UTC)}
    hb = session.scalars(select(Heartbeat)).all()
    assert len(hb) == 1
    assert hb[0].service == "collector"
    assert hb[0].status == "ok"


def test_no_daily_before_00_01(session: Session) -> None:
    early = datetime(2026, 11, 19, 0, 0, 30, tzinfo=UTC)

    class _EarlyClock:
        def now(self) -> datetime:
            return early

    collector = Collector(sources=[FakeSource()], clock=_EarlyClock())
    collector.run_once(session)
    session.commit()
    assert (
        session.scalar(
            select(func.count())
            .select_from(CandleRow)
            .where(CandleRow.interval == "1d")
        )
        == 0
    )


def test_failover_writes_event_and_uses_backup(session: Session) -> None:
    collector = Collector(
        sources=[FakeSource("down", fail=True), FakeSource("backup")],
        clock=_Frozen(),
    )
    written = collector.run_once(session)
    session.commit()

    assert written == 6
    assert {r.source for r in session.scalars(select(CandleRow))} == {"backup"}
    events = session.scalars(select(Event)).all()
    assert any("collector_source_failover" == e.kind for e in events)
    # The backup is now the current source.
    assert collector._source_idx == 1


def test_all_sources_down_writes_error_no_candles(session: Session) -> None:
    collector = Collector(sources=[FakeSource("a", fail=True)], clock=_Frozen())
    written = collector.run_once(session)
    session.commit()

    assert written == 0
    events = session.scalars(select(Event)).all()
    assert events and all(e.severity == "error" for e in events)
    # The heartbeat reports the outage, not ok.
    hb = session.scalars(select(Heartbeat)).all()
    assert hb and all(h.status == "error" for h in hb)


def test_heartbeat_recovers_after_outage(session: Session) -> None:
    sources = [FakeSource("a", fail=True), FakeSource("b", fail=True)]
    collector = Collector(sources=sources, clock=_Frozen())
    collector.run_once(session)
    # Source a recovers; rotation put it first again.
    sources[0]._fail = False
    collector.run_once(session)
    session.commit()
    statuses = [
        h.status for h in session.scalars(select(Heartbeat).order_by(Heartbeat.id))
    ]
    assert statuses == ["error", "ok"]


def test_daily_backfill_starts_at_each_assets_daily_start(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec D2: SOL's first daily request starts 2022-08-18, BTC's 2019-01-01 —
    a window before the first candle returns short pages the fetch loop reads
    as the end of history."""
    import trader.data.collector as collector_mod

    calls: list[tuple[str, str, datetime]] = []

    class RecordingSource:
        name = "record"

        def fetch(
            self, symbol: str, interval: str, start: datetime, end: datetime
        ) -> list[Candle]:
            calls.append((symbol, interval, start))
            return []

    four = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
    monkeypatch.setattr(collector_mod, "SYMBOLS", four)
    collector = Collector(
        sources=[RecordingSource()], clock=_Frozen(), session_factory=lambda: session
    )
    collector.refill_gaps(session)
    session.commit()

    first_daily: dict[str, datetime] = {}
    for symbol, interval, start in calls:
        if interval == "1d" and symbol not in first_daily:
            first_daily[symbol] = start
    assert first_daily["SOL-USD"] == datetime(2022, 8, 18, tzinfo=UTC)
    assert first_daily["BTC-USD"] == datetime(2019, 1, 1, tzinfo=UTC)
