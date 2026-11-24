"""Collector loop tests: per-minute storage, heartbeat, and source failover.
Needs TEST_DATABASE_URL (Compose Postgres)."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from trader.core.clock import FrozenClock
from trader.data.collector import Collector
from trader.data.sources.base import Candle
from trader.data.sources.coinbase_funding import VENUE, FundingObservation
from trader.ledger.models import Candle as CandleRow
from trader.ledger.models import (
    Decision,
    Event,
    Fill,
    FundingRate,
    Heartbeat,
    Order,
    Plan,
)

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


FUNDING_TIME = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)


class FakeFunding:
    """Returns a fixed funding observation; counts calls, can be made to
    fail for all symbols or a named subset."""

    def __init__(self, fail: bool = False, fail_for: tuple[str, ...] = ()) -> None:
        self.calls = 0
        self._fail = fail
        self._fail_for = set(fail_for)

    def fetch(self, symbol: str) -> FundingObservation:
        self.calls += 1
        if self._fail or symbol in self._fail_for:
            raise ConnectionError(f"funding down for {symbol}")
        return FundingObservation(
            symbol=symbol,
            venue=VENUE,
            time=FUNDING_TIME,
            rate=Decimal("0.000004"),
            interval_hours=1,
        )


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
        for model in (
            CandleRow,
            Event,
            Heartbeat,
            Fill,
            Order,
            Plan,
            Decision,
            FundingRate,
        ):
            s.execute(delete(model))
        s.commit()
        yield s
        for model in (
            CandleRow,
            Event,
            Heartbeat,
            Fill,
            Order,
            Plan,
            Decision,
            FundingRate,
        ):
            s.execute(delete(model))
        s.commit()
    engine.dispose()


def test_run_once_stores_recent_candles_and_heartbeat(session: Session) -> None:
    collector = Collector(
        sources=[FakeSource()], clock=_Frozen(), funding_source=FakeFunding()
    )
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

    collector = Collector(
        sources=[FakeSource()], clock=_EarlyClock(), funding_source=FakeFunding()
    )
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
        funding_source=FakeFunding(),
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
    collector = Collector(
        sources=[FakeSource("a", fail=True)],
        clock=_Frozen(),
        funding_source=FakeFunding(),
    )
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
    collector = Collector(
        sources=sources, clock=_Frozen(), funding_source=FakeFunding()
    )
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
        sources=[RecordingSource()],
        clock=_Frozen(),
        session_factory=lambda: session,
        funding_source=FakeFunding(),
    )
    collector.refill_gaps(session)
    session.commit()

    first_daily: dict[str, datetime] = {}
    for symbol, interval, start in calls:
        if interval == "1d" and symbol not in first_daily:
            first_daily[symbol] = start
    assert first_daily["SOL-USD"] == datetime(2022, 8, 18, tzinfo=UTC)
    assert first_daily["BTC-USD"] == datetime(2019, 1, 1, tzinfo=UTC)


def test_removed_symbol_with_open_order_still_collected(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review fix: a symbol dropped from TRADER_SYMBOLS keeps its candle
    feed while an open order or approved plan still needs fills."""
    import trader.data.collector as collector_mod

    monkeypatch.setattr(collector_mod, "SYMBOLS", ("BTC-USD", "ETH-USD"))
    decision = Decision(
        cycle="daily", ran_at=NOW, action="none", reason="seed", details={}
    )
    session.add(decision)
    session.flush()
    plan = Plan(
        sleeve="trend",
        symbol="SOL-USD",
        side="buy",
        status="approved",
        reference_price=Decimal("100"),
        target_weight=Decimal("0.1"),
        entry_orders=[],
        worst_case_loss=Decimal("5"),
        est_fees=Decimal("1"),
        rationale="x",
        expires_at=NOW,
        decision_id=decision.id,
    )
    session.add(plan)
    session.flush()
    session.add(
        Order(
            plan_id=plan.id,
            venue="paper",
            client_order_id="o1",
            symbol="SOL-USD",
            side="buy",
            type="market",
            quantity=Decimal("1"),
            status="open",
            mode="paper",
        )
    )
    session.commit()

    collector = Collector(
        sources=[FakeSource()], clock=_Frozen(), funding_source=FakeFunding()
    )
    written = collector.run_once(session)
    session.commit()

    symbols = {r.symbol for r in session.scalars(select(CandleRow))}
    assert "SOL-USD" in symbols
    # 2 symbols x (2 5m + 1 1d) + SOL's share = 9.
    assert written == 9


def test_funding_stored_then_throttled(session: Session) -> None:
    """Spec G4: first run fetches per product; a run a minute later does
    not call the source; ten minutes later it fetches and the same
    funding_time stores no new row."""
    clock = FrozenClock(NOW)
    funding = FakeFunding()
    collector = Collector(sources=[FakeSource()], clock=clock, funding_source=funding)
    collector.run_once(session)
    session.commit()

    rows = session.scalars(select(FundingRate)).all()
    assert len(rows) == 2
    assert all(r.venue == "coinbase_perp" for r in rows)
    assert {r.symbol for r in rows} == {"BTC-USD", "ETH-USD"}
    assert funding.calls == 2

    clock.advance(timedelta(minutes=1))
    collector.run_once(session)
    assert funding.calls == 2

    clock.advance(timedelta(minutes=9))
    collector.run_once(session)
    session.commit()
    assert funding.calls == 4
    assert session.scalar(select(func.count()).select_from(FundingRate)) == 2


def test_funding_partial_outage_still_stores_other_symbols(
    session: Session,
) -> None:
    """A failed BTC fetch does not skip ETH: its row still lands and one
    failure event is written."""
    clock = FrozenClock(NOW)
    funding = FakeFunding(fail_for=("BTC-USD",))
    collector = Collector(sources=[FakeSource()], clock=clock, funding_source=funding)
    collector.run_once(session)
    session.commit()

    rows = session.scalars(select(FundingRate)).all()
    assert [r.symbol for r in rows] == ["ETH-USD"]
    failed = session.scalars(
        select(Event).where(Event.kind == "funding_source_failed")
    ).all()
    assert len(failed) == 1
    assert "BTC-USD" in failed[0].message

    # Recovery clears when every product fetches again.
    funding._fail_for.clear()
    clock.advance(timedelta(minutes=11))
    collector.run_once(session)
    session.commit()
    assert (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.kind == "funding_source_recovered")
        )
        == 1
    )


def test_rejected_funding_rows_keep_candles(session: Session) -> None:
    """A funding row Postgres refuses rolls back only the funding insert:
    the loop's candles and the ok heartbeat survive (spec G4 step 1 — a
    funding failure is not a candle outage)."""

    class BadInterval(FakeFunding):
        def fetch(self, symbol: str) -> FundingObservation:
            self.calls += 1
            return FundingObservation(
                symbol=symbol,
                venue=VENUE,
                time=FUNDING_TIME,
                rate=Decimal("0.000004"),
                interval_hours=0,  # violates funding_interval_positive
            )

    collector = Collector(
        sources=[FakeSource()], clock=FrozenClock(NOW), funding_source=BadInterval()
    )
    written = collector.run_once(session)
    session.commit()

    assert written == 6
    assert session.scalar(select(func.count()).select_from(CandleRow)) == 6
    assert session.scalar(select(func.count()).select_from(FundingRate)) == 0
    failed = session.scalars(
        select(Event).where(Event.kind == "funding_source_failed")
    ).all()
    assert len(failed) == 1
    assert all(h.status == "ok" for h in session.scalars(select(Heartbeat)))


def test_funding_failure_events_and_recovery(session: Session) -> None:
    """A failing funding source writes one ``funding_source_failed`` event
    over repeated failing runs, one ``funding_source_recovered`` when a
    fetch works again; the heartbeat stays ``ok`` and candles still land."""
    clock = FrozenClock(NOW)
    funding = FakeFunding(fail=True)
    collector = Collector(sources=[FakeSource()], clock=clock, funding_source=funding)
    for _ in range(3):
        collector.run_once(session)
        clock.advance(timedelta(minutes=11))
    session.commit()

    failed = session.scalars(
        select(Event).where(Event.kind == "funding_source_failed")
    ).all()
    assert len(failed) == 1
    assert failed[0].severity == "warning"
    assert session.scalar(select(func.count()).select_from(Event)) == 1
    assert all(h.status == "ok" for h in session.scalars(select(Heartbeat)))
    assert session.scalar(select(func.count()).select_from(CandleRow)) > 0

    funding._fail = False
    collector.run_once(session)
    session.commit()
    recovered = session.scalars(
        select(Event).where(Event.kind == "funding_source_recovered")
    ).all()
    assert len(recovered) == 1
    assert recovered[0].severity == "info"
    assert session.scalar(select(func.count()).select_from(FundingRate)) == 2
