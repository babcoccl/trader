"""All ORM models. Alembic migrations (base revision from spec 0.3) create
these tables.

Conventions: every table has `id BIGSERIAL PK` and
`created_at timestamptz default now()` unless the spec states otherwise
(`settings` uses `key` as its primary key). Money and quantities are
NUMERIC(38,18); timestamps are timestamptz. Columns are NOT NULL unless the
spec marks them NULL.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Money = Numeric(38, 18)


def _jsonb() -> Any:
    # Returns JSONB on Postgres, generic JSON elsewhere (keeps models importable
    # without a dialect). The migration is Postgres-only and pins JSONB.
    return JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "interval", "open_time", "source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    symbol: Mapped[str] = mapped_column(String(20))
    interval: Mapped[Literal["1d", "5m"]] = mapped_column(String(4))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[Decimal] = mapped_column(Money)
    high: Mapped[Decimal] = mapped_column(Money)
    low: Mapped[Decimal] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[Decimal] = mapped_column(Money)
    source: Mapped[str] = mapped_column(String(40))


class FundingRate(Base):
    __tablename__ = "funding_rates"
    __table_args__ = (
        UniqueConstraint("symbol", "venue", "time"),
        CheckConstraint("interval_hours > 0", name="funding_interval_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    symbol: Mapped[str] = mapped_column(String(20))
    venue: Mapped[str] = mapped_column(String(40))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Rate paid per settlement period (a fraction, not annualised).
    rate: Mapped[Decimal] = mapped_column(Money)
    # Hours between settlements on this venue (e.g. 8 on Binance, 1 on
    # Hyperliquid); annual rate = rate * 8760 / interval_hours.
    interval_hours: Mapped[int] = mapped_column(Integer)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("symbol", "day", "rule"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    symbol: Mapped[str] = mapped_column(String(20))
    day: Mapped[date] = mapped_column(Date)
    rule: Mapped[str] = mapped_column(String(40))
    value: Mapped[Decimal] = mapped_column(Money)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cycle: Mapped[Literal["daily", "intraday"]] = mapped_column(String(10))
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_weight: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    current_weight: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    action: Mapped[Literal["none", "plan_proposed", "blocked", "error"]] = (
        mapped_column(String(20))
    )
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sleeve: Mapped[Literal["trend", "dip_bid"]] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(10))
    status: Mapped[
        Literal["pending", "approved", "denied", "expired", "voided", "completed"]
    ] = mapped_column(String(20), index=True)
    reference_price: Mapped[Decimal] = mapped_column(Money)
    target_weight: Mapped[Decimal] = mapped_column(Money)
    size_fraction: Mapped[Decimal] = mapped_column(Money, server_default=text("1"))
    entry_orders: Mapped[list[Any]] = mapped_column(
        _jsonb(), server_default=text("'[]'::jsonb")
    )
    stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    worst_case_loss: Mapped[Decimal] = mapped_column(Money)
    est_fees: Mapped[Decimal] = mapped_column(Money)
    rationale: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("decisions.id"))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    plan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("plans.id"))
    venue: Mapped[str] = mapped_column(String(40))
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    venue_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(10))
    type: Mapped[Literal["limit", "market", "stop"]] = mapped_column(String(10))
    price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[
        Literal[
            "new",
            "open",
            "partially_filled",
            "filled",
            "cancelled",
            "rejected",
        ]
    ] = mapped_column(String(20), index=True)
    mode: Mapped[Literal["paper", "live"]] = mapped_column(String(10))
    shadow: Mapped[bool] = mapped_column(server_default=text("false"))


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[Decimal] = mapped_column(Money)
    quantity: Mapped[Decimal] = mapped_column(Money)
    fee: Mapped[Decimal] = mapped_column(Money)
    fee_currency: Mapped[str] = mapped_column(String(10))
    liquidity: Mapped[Literal["maker", "taker"]] = mapped_column(String(10))


class Balance(Base):
    __tablename__ = "balances"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mode: Mapped[Literal["paper", "live"]] = mapped_column(String(10))
    asset: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Money)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mode: Mapped[Literal["paper", "live"]] = mapped_column(String(10))
    equity_usd: Mapped[Decimal] = mapped_column(Money)
    high_water_mark: Mapped[Decimal] = mapped_column(Money)
    drawdown: Mapped[Decimal] = mapped_column(Money)


class ShadowTrade(Base):
    """One hypothetical position of a shadow sleeve (spec G3).

    Records what a sleeve would have bought and sold, with fees. Nothing
    here is a plan, an order or an account balance.
    """

    __tablename__ = "shadow_trades"
    __table_args__ = (Index("ix_shadow_trades_sleeve_closed", "sleeve", "closed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sleeve: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(20))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[Decimal] = mapped_column(Money)
    quantity: Mapped[Decimal] = mapped_column(Money)
    cost_usd: Mapped[Decimal] = mapped_column(Money)  # dollars spent, fee included
    entry_fee_usd: Mapped[Decimal] = mapped_column(Money)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    exit_fee_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    pnl_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )


class ShadowMark(Base):
    """One shadow sleeve's end-of-day book value (spec G3)."""

    __tablename__ = "shadow_marks"
    __table_args__ = (UniqueConstraint("sleeve", "day"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sleeve: Mapped[str] = mapped_column(String(20))
    day: Mapped[date] = mapped_column(Date)
    equity_usd: Mapped[Decimal] = mapped_column(Money)
    cash_usd: Mapped[Decimal] = mapped_column(Money)
    exposure_usd: Mapped[Decimal] = mapped_column(Money)
    open_trades: Mapped[int] = mapped_column(Integer)
    closed_trades: Mapped[int] = mapped_column(Integer)  # running total
    note: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )


class ShadowState(Base):
    """Per-(sleeve, symbol) scratch state for later shadow specs (G5).

    Created with the other shadow tables so G4 and G5 need no migration;
    this spec does not use it.
    """

    __tablename__ = "shadow_state"
    __table_args__ = (UniqueConstraint("sleeve", "symbol"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sleeve: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(20))
    state: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    service: Mapped[str] = mapped_column(String(60))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(_jsonb())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    before: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )
    after: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    day: Mapped[date] = mapped_column(Date, unique=True)
    body_md: Mapped[str] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(
        _jsonb(), server_default=text("'{}'::jsonb")
    )
