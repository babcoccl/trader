"""Contract tests for the dashboard API (spec 3.1). Seeded Postgres via
TEST_DATABASE_URL (Compose ``db`` service)."""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from trader.api.routes import get_session
from trader.core.clock import SystemClock
from trader.core.config import get_settings
from trader.core.db import get_engine
from trader.ledger import accounting
from trader.ledger.models import (
    AuditLog,
    Balance,
    Candle,
    Decision,
    Digest,
    EquitySnapshot,
    Event,
    Fill,
    FundingRate,
    Heartbeat,
    Order,
    Plan,
    Setting,
)
from trader.risk import engine as risk

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="TEST_DATABASE_URL not set (needs a Postgres database)"
)

PASSWORD = "test-dashboard-password"
NOW = datetime.now(UTC)


def _alembic(connection, *args: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    cfg.attributes["connection"] = connection
    getattr(command, args[0])(cfg, *args[1:])


_engine = None


def _shared_engine():
    global _engine
    if _engine is None:
        assert TEST_URL is not None
        _engine = create_engine(TEST_URL)
        with _engine.begin() as connection:
            _alembic(connection, "upgrade", "head")
    return _engine


def _seed(s: Session) -> dict[str, Any]:
    clock = SystemClock()
    for symbol, close in (("BTC-USD", "60000"), ("ETH-USD", "3000")):
        for i in range(30):
            t = NOW - timedelta(days=30 - i)
            s.add(
                Candle(
                    symbol=symbol,
                    interval="1d",
                    open_time=t,
                    open=Decimal(close),
                    high=Decimal(close),
                    low=Decimal(close),
                    close=Decimal(close),
                    volume=Decimal("1"),
                    source="seed",
                )
            )
        s.add(
            Candle(
                symbol=symbol,
                interval="5m",
                open_time=NOW - timedelta(minutes=5),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal("1"),
                source="bitstamp",
            )
        )

    accounting.deposit(s, "paper", Decimal("10000"), clock)
    risk.update_equity(s, Decimal("10000"), clock)

    decision = Decision(
        cycle="intraday",
        ran_at=NOW - timedelta(minutes=1),
        action="none",
        reason="Checked prices. Nothing to do.",
        details={"x": 1},
    )
    s.add(decision)
    s.flush()

    pending = Plan(
        sleeve="trend",
        symbol="BTC-USD",
        side="buy",
        status="pending",
        reference_price=Decimal("60000"),
        target_weight=Decimal("0.2"),
        entry_orders=[
            {"side": "buy", "type": "limit", "price": "60000", "quantity": "0.003"}
        ],
        stop_price=Decimal("45000"),
        worst_case_loss=Decimal("10"),
        est_fees=Decimal("2"),
        rationale="I would like to raise BTC-USD.",
        expires_at=NOW + timedelta(hours=6),
        decision_id=decision.id,
    )
    s.add(pending)

    done = Plan(
        sleeve="trend",
        symbol="BTC-USD",
        side="buy",
        status="completed",
        reference_price=Decimal("60000"),
        target_weight=Decimal("0.06"),
        entry_orders=[],
        stop_price=Decimal("45000"),
        worst_case_loss=Decimal("10"),
        est_fees=Decimal("2"),
        rationale="bought earlier",
        expires_at=NOW - timedelta(hours=1),
        decided_at=NOW - timedelta(hours=2),
        decision_id=decision.id,
    )
    s.add(done)
    s.flush()

    order = Order(
        plan_id=done.id,
        venue="paper",
        client_order_id=f"{done.id}-1",
        venue_order_id="paper-x",
        symbol="BTC-USD",
        side="buy",
        type="limit",
        price=Decimal("60000"),
        quantity=Decimal("0.01"),
        status="filled",
        mode="paper",
        shadow=False,
    )
    s.add(order)
    s.flush()
    fill = Fill(
        order_id=order.id,
        time=NOW - timedelta(hours=2),
        price=Decimal("60000"),
        quantity=Decimal("0.01"),
        fee=Decimal("1.8"),
        fee_currency="USD",
        liquidity="maker",
    )
    s.add(fill)
    accounting.apply_fill(s, fill)

    s.add(
        Heartbeat(
            service="engine", time=NOW - timedelta(seconds=10), status="ok", detail=""
        )
    )
    s.add(
        Event(
            time=NOW - timedelta(minutes=3),
            kind="alarm",
            severity="warn",
            message="collector silent",
            details={},
        )
    )
    s.add(
        FundingRate(
            symbol="BTC-USD",
            venue="binance",
            time=NOW - timedelta(hours=1),
            rate=Decimal("0.0001"),
        )
    )
    s.add(Setting(key="t_bill_rate", value=0.04, updated_at=NOW))
    s.add(Digest(day=date(2026, 11, 20), body_md="# digest", stats={"a": 1}))
    s.commit()
    return {
        "pending_plan_id": pending.id,
        "done_plan_id": done.id,
        "decision": decision,
    }


@pytest.fixture()
def seeded(monkeypatch):
    monkeypatch.setenv("TRADER_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("TRADER_DASHBOARD_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    get_engine.cache_clear()
    engine = _shared_engine()
    with Session(engine) as s:
        for table in (
            Fill,
            Order,
            Plan,
            Decision,
            Balance,
            Event,
            Candle,
            Setting,
            EquitySnapshot,
            Heartbeat,
            FundingRate,
            AuditLog,
            Digest,
        ):
            s.execute(delete(table))
        s.commit()
        ids = _seed(s)
    from trader.api.app import app

    def _override() -> Iterator[Session]:
        with Session(engine) as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    yield client, ids, engine
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_engine.cache_clear()


def _login(client: TestClient) -> None:
    r = client.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 200


def test_routes_require_login(seeded) -> None:
    client, _, _ = seeded
    for path in (
        "/api/status",
        "/api/decisions",
        "/api/plans",
        "/api/plans/1",
        "/api/positions",
        "/api/activity",
        "/api/funding",
        "/api/reports/equity",
        "/api/reports/summary",
        "/api/digests",
        "/api/settings",
        "/api/stream",
    ):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/controls/halt", json={}).status_code == 401
    assert (
        client.post("/api/controls/flatten", json={"confirm": "FLATTEN"}).status_code
        == 401
    )


def test_funding(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    r = client.get("/api/funding")
    assert r.status_code == 200
    body = r.json()
    assert body["t_bill_rate"] == 0.04
    assert body["rates"] == [
        {
            "symbol": "BTC-USD",
            "venue": "binance",
            "time": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "rate": "0.000100000000000000",
        }
    ]


def test_login_wrong_password(seeded) -> None:
    client, _, _ = seeded
    r = client.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401


def test_status(seeded) -> None:
    client, ids, _ = seeded
    _login(client)
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "paper"
    assert body["halted"] is False
    assert float(body["equity_usd"]) == 10000
    assert body["halt_at"] == 0.35
    assert body["pending_plans"] == 1
    assert body["last_check_reason"] == "Checked prices. Nothing to do."
    assert body["last_trade_at"] is not None
    assert {s["name"] for s in body["services"]} == {"engine"}
    # 0.01 BTC at 60000 over equity of 9998.2 (600 spent + 1.8 fee) -> ~0.06
    assert abs(body["invested_fraction"] - 0.06) < 0.001


def test_decisions(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    r = client.get("/api/decisions")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["action"] == "none"
    assert client.get("/api/decisions?cycle=daily").json() == []
    before = rows[0]["ran_at"]
    assert client.get("/api/decisions", params={"before": before}).json() == []


def test_plans(seeded) -> None:
    client, ids, _ = seeded
    _login(client)
    rows = client.get("/api/plans").json()
    assert len(rows) == 2
    pending = client.get("/api/plans?status=pending").json()
    assert [p["id"] for p in pending] == [ids["pending_plan_id"]]
    one = client.get(f"/api/plans/{ids['pending_plan_id']}")
    assert one.status_code == 200
    assert one.json()["rationale"] == "I would like to raise BTC-USD."
    assert client.get("/api/plans/99999").status_code == 404


def test_approve_and_deny(seeded) -> None:
    client, ids, _ = seeded
    _login(client)
    pid = ids["pending_plan_id"]
    r = client.post(f"/api/plans/{pid}/approve", json={"size_fraction": 0.5})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["size_fraction"] == "0.5"
    # Second approve refused (not pending any more).
    r = client.post(f"/api/plans/{pid}/approve", json={"size_fraction": 1.0})
    assert r.status_code == 409
    # Deny a completed plan refused.
    r = client.post(f"/api/plans/{ids['done_plan_id']}/deny")
    assert r.status_code == 409


def test_approve_bad_fraction_422(seeded) -> None:
    client, ids, _ = seeded
    _login(client)
    r = client.post(
        f"/api/plans/{ids['pending_plan_id']}/approve", json={"size_fraction": 2}
    )
    assert r.status_code == 422


def test_deny(seeded) -> None:
    client, ids, engine = seeded
    _login(client)
    r = client.post(f"/api/plans/{ids['pending_plan_id']}/deny")
    assert r.status_code == 200
    assert r.json()["status"] == "denied"
    # Denying writes shadow orders.
    with Session(engine) as s:
        shadows = s.scalars(
            select(Order).where(
                Order.plan_id == ids["pending_plan_id"], Order.shadow.is_(True)
            )
        ).all()
        assert len(shadows) == 1


def test_positions(seeded) -> None:
    client, ids, _ = seeded
    _login(client)
    rows = client.get("/api/positions").json()
    assert len(rows) == 1
    p = rows[0]
    assert p["asset"] == "BTC"
    assert float(p["quantity"]) == 0.01
    assert float(p["stop_price"]) == 45000
    assert p["plan_id"] == ids["done_plan_id"]
    assert p["unrealised_gain_usd"] is not None


def test_activity(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    rows = client.get("/api/activity?limit=100").json()
    kinds = {r["kind"] for r in rows}
    assert {"alarm", "decision", "fill", "heartbeat", "deposit"} <= kinds
    assert [r["kind"] for r in client.get("/api/activity?kind=fill").json()] == ["fill"]
    hit = client.get("/api/activity?q=nothing%20to%20do").json()
    assert len(hit) == 1 and hit[0]["kind"] == "decision"


def test_reports(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    eq = client.get("/api/reports/equity").json()
    assert len(eq) == 1
    assert float(eq[0]["account"]) == 10000
    assert float(eq[0]["btc"]) == 10000
    summary = client.get("/api/reports/summary").json()
    assert float(summary["fees_usd"]) == 1.8
    assert summary["vetoes"]["plans"] == 0
    digests = client.get("/api/digests").json()
    assert digests[0]["day"] == "2026-11-20"


def test_settings(seeded) -> None:
    client, _, engine = seeded
    _login(client)
    rows = client.get("/api/settings").json()
    keys = {r["key"] for r in rows}
    assert {"autonomy", "silence_alarm_minutes", "mode"} <= keys

    r = client.put("/api/settings/silence_alarm_minutes", json={"value": 10})
    assert r.status_code == 200
    assert r.json()["value"] == 10
    with Session(engine) as s:
        audit = s.scalars(
            select(AuditLog).where(AuditLog.action == "set silence_alarm_minutes")
        ).all()
        assert len(audit) == 1
        assert audit[0].before["silence_alarm_minutes"] is None

    assert (
        client.put("/api/settings/silence_alarm_minutes", json={"value": 0}).status_code
        == 422
    )
    assert client.put("/api/settings/bogus", json={"value": 1}).status_code == 404


def test_mode_live_rejected_without_flag(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    r = client.put("/api/settings/mode", json={"value": "live"})
    assert r.status_code == 422
    r = client.put("/api/settings/mode", json={"value": "paper"})
    assert r.status_code == 200


def test_controls(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    assert (
        client.post("/api/controls/halt", json={"reason": "test halt"}).status_code
        == 200
    )
    body = client.get("/api/status").json()
    assert body["halted"] is True and body["halt_reason"] == "test halt"
    assert client.post("/api/controls/resume").status_code == 200
    assert client.get("/api/status").json()["halted"] is False
    assert (
        client.post("/api/controls/flatten", json={"confirm": "FLATTEN"}).status_code
        == 200
    )


def test_flatten_requires_confirm_word(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    assert (
        client.post("/api/controls/flatten", json={"confirm": "yes"}).status_code == 422
    )
    assert client.post("/api/controls/flatten", json={}).status_code == 422


def test_stream_emits_status(seeded) -> None:
    client, _, _ = seeded
    _login(client)
    with client.stream("GET", "/api/stream") as r:
        assert r.status_code == 200
        lines = []
        for line in r.iter_lines():
            if line:
                lines.append(line)
            if len(lines) >= 2:
                break
        assert lines[0] == "event: status"
        assert '"halted":false' in lines[1] or '"halted": false' in lines[1]
