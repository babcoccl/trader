"""Request and response models for the dashboard API (spec 3.1).

All routes sit under ``/api`` and speak JSON. Money and quantities are
``Decimal`` on the wire: pydantic v2 serializes them as strings, so no
precision is lost between Postgres NUMERIC(38,18) and the dashboard.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    password: str


class ServiceStatus(BaseModel):
    name: str
    status: str
    last_heartbeat_at: datetime


class StatusOut(BaseModel):
    mode: str
    halted: bool
    halt_reason: str | None
    equity_usd: Decimal | None
    invested_fraction: float
    drawdown: float
    halt_at: float
    pending_plans: int
    last_check_at: datetime | None
    last_check_reason: str | None
    next_daily_decision_at: datetime
    last_trade_at: datetime | None
    services: list[ServiceStatus]


class DecisionOut(BaseModel):
    id: int
    cycle: str
    ran_at: datetime
    symbol: str | None
    target_weight: Decimal | None
    current_weight: Decimal | None
    action: str
    reason: str
    details: dict[str, Any]


class OrderOut(BaseModel):
    id: int
    client_order_id: str
    side: str
    type: str
    price: Decimal | None
    stop_price: Decimal | None
    quantity: Decimal
    status: str
    created_at: datetime


class PlanOut(BaseModel):
    id: int
    sleeve: str
    symbol: str
    side: str
    status: str
    reference_price: Decimal
    target_weight: Decimal
    size_fraction: Decimal
    entry_orders: list[Any]
    stop_price: Decimal | None
    worst_case_loss: Decimal
    est_fees: Decimal
    rationale: str
    expires_at: datetime
    decided_at: datetime | None
    decision_id: int
    created_at: datetime


class ApproveRequest(BaseModel):
    size_fraction: float = 1.0

    @field_validator("size_fraction")
    @classmethod
    def _allowed(cls, v: float) -> float:
        if v not in (1.0, 0.5, 0.25):
            raise ValueError("size_fraction must be one of 1.0, 0.5, 0.25")
        return v


class OpenOrderOut(OrderOut):
    symbol: str
    plan_id: int | None


class PositionOut(BaseModel):
    asset: str
    symbol: str
    quantity: Decimal
    price: Decimal | None
    value_usd: Decimal | None
    share: float | None
    avg_cost: Decimal
    unrealised_gain_usd: Decimal | None
    stop_price: Decimal | None
    plan_id: int | None
    resting_orders: list[OrderOut]


class ActivityItem(BaseModel):
    time: datetime
    kind: str
    ref_id: int
    text: str
    details: dict[str, Any]


class FundingRateOut(BaseModel):
    symbol: str
    venue: str
    time: datetime
    rate: Decimal
    interval_hours: int
    annual_rate: Decimal


class FundingOut(BaseModel):
    rates: list[FundingRateOut]
    t_bill_rate: float


class EquityPoint(BaseModel):
    time: datetime
    account: Decimal
    cash: Decimal
    btc: Decimal | None
    btc_eth: Decimal | None


class VetoScorecard(BaseModel):
    plans: int
    shadow_fills: int
    result_usd: Decimal | None


class SummaryOut(BaseModel):
    from_at: datetime | None
    to_at: datetime | None
    return_pct: float | None
    worst_drop_pct: float | None
    fees_usd: Decimal
    vetoes: VetoScorecard
    paper_vs_backtest_gap_pp: float | None


class WeeklyReportOut(BaseModel):
    week_start: str
    week_end: str
    message: str
    body_md: str
    tracking_gap_points: float | None
    paper_return: float | None
    backtest_return: float | None
    mean_uptime: float | None


class DigestOut(BaseModel):
    id: int
    day: date
    body_md: str
    stats: dict[str, Any]


class SettingOut(BaseModel):
    key: str
    value: Any
    updated_at: datetime | None


class SettingPut(BaseModel):
    value: Any


class HaltRequest(BaseModel):
    reason: str = "Manual halt from the dashboard."


class FlattenRequest(BaseModel):
    confirm: Literal["FLATTEN"]


class DailyRunOut(BaseModel):
    ran_at: datetime
    action: str
    reason: str


class RuleOut(BaseModel):
    name: str
    label: str
    positive: bool | None
    level: float | None
    distance: float | None


class RegimeOut(BaseModel):
    trend: str
    volatility: str | None
    sideways: bool | None
    vol_percentile: float | None
    adx: float | None
    text: str


class AssetRulesOut(BaseModel):
    symbol: str
    decided_at: datetime | None
    action: str | None
    reason: str | None
    last_close: float | None
    rules_positive: int | None
    rules: list[RuleOut]
    annual_vol: float | None
    raw_weight: float | None
    target_weight: Decimal | None
    current_weight: Decimal | None
    regime: RegimeOut | None


class IntradayAssetOut(BaseModel):
    symbol: str
    last_price: float | None
    nearest_stop_distance: float | None
    drop_bid: float | None
    drop_bid_distance: float | None


class IntradayRulesOut(BaseModel):
    ran_at: datetime
    action: str
    reason: str
    assets: list[IntradayAssetOut]


class ParameterOut(BaseModel):
    name: str
    value: str


class RulesOut(BaseModel):
    next_daily_decision_at: datetime
    latest_daily: DailyRunOut | None
    assets: list[AssetRulesOut]
    intraday: IntradayRulesOut | None
    parameters: list[ParameterOut]


class ShadowTradeOut(BaseModel):
    symbol: str
    opened_at: datetime
    entry_price: Decimal
    cost_usd: Decimal
    closed_at: datetime | None
    exit_price: Decimal | None
    pnl_usd: Decimal | None
    exit_reason: str | None


class ShadowSleeveOut(BaseModel):
    name: str
    title: str
    description: str
    started_on: date | None
    days: int
    equity_usd: Decimal | None
    return_fraction: float | None
    worst_drawdown: float | None
    account_return_fraction: float | None
    closed_trades: int
    winning_trades: int
    fees_usd: Decimal
    last_note: str | None
    last_day: date | None
    open: list[ShadowTradeOut]
    recent_closed: list[ShadowTradeOut]


class ShadowOut(BaseModel):
    sleeves: list[ShadowSleeveOut]


class SubsidyOut(BaseModel):
    mode: str
    status: Literal["active", "throttled", "disabled"]
    reason: str
    trailing_30d_volume_usd: Decimal
    today_booster_volume_usd: Decimal
    booster_trades_30d: int
    booster_volume_30d_usd: Decimal
    booster_fees_30d_usd: Decimal
    tier: str
    maker_bps: float
    taker_bps: float
    target_volume_usd: Decimal
    remaining_to_target_usd: Decimal
    progress_pct: float
    daily_cap_usd: Decimal
    adx: float | None
    adx_max: float


class BacktestParams(BaseModel):
    strategy_sleeve: str = "trend_momentum"
    asset_universe: list[str] = ["BTC-USD", "ETH-USD"]
    start_date: str = "2024-01-01"
    end_date: str = "2024-12-31"
    starting_capital: Decimal = Field(
        default=Decimal("10000.0"), gt=0, allow_inf_nan=False
    )
    assumed_fee_tier: str = "tier_1_maker"
    vol_target: float = 0.40
    drawdown_limit: float = 0.35
    extra_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        date.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _ordered_dates(self) -> "BacktestParams":
        if date.fromisoformat(self.start_date) >= date.fromisoformat(self.end_date):
            raise ValueError("start_date must be before end_date")
        return self


class BacktestSummaryMetrics(BaseModel):
    cagr: float
    benchmark_cagr: float
    max_drawdown: float
    total_trades: int
    win_rate: float
    total_fees_paid: Decimal


class BacktestPoint(BaseModel):
    timestamp: str
    equity: Decimal
    benchmark_equity: Decimal
    drawdown: float
    asset_price: Decimal


class BacktestTrade(BaseModel):
    date: str
    asset: str
    side: Literal["BUY", "SELL"]
    size: Decimal
    price: Decimal
    pnl: Decimal
    fee: Decimal


class BacktestResult(BaseModel):
    summary_metrics: BacktestSummaryMetrics
    timeseries_data: list[BacktestPoint]
    trade_log: list[BacktestTrade]


class OkOut(BaseModel):
    status: str = "ok"
    detail: dict[str, Any] = Field(default_factory=dict)
