# Database schema

PostgreSQL 16, created by the Alembic revisions in `migrations/versions`
(`alembic upgrade head`). Every table has `id BIGSERIAL PRIMARY KEY` and
`created_at timestamptz NOT NULL DEFAULT now()` unless noted otherwise.
All timestamps are `timestamptz`; all money and quantity columns are
`NUMERIC(38,18)`. Columns are `NOT NULL` unless marked `(null)` below.

## candles

| Column | Type | Notes |
|---|---|---|
| symbol | varchar(20) | `"BTC-USD"` or `"ETH-USD"` |
| interval | varchar(4) | `"1d"` or `"5m"` |
| open_time | timestamptz | indexed; key is the candle's open |
| open, high, low, close | NUMERIC(38,18) | |
| volume | NUMERIC(38,18) | |
| source | varchar(40) | data source name |

UNIQUE (symbol, interval, open_time, source)

## funding_rates

| Column | Type | Notes |
|---|---|---|
| symbol | varchar(20) | |
| venue | varchar(40) | |
| time | timestamptz | indexed |
| rate | NUMERIC(38,18) | rate paid per settlement period, as a fraction (not annualised) |
| interval_hours | integer | hours between settlements on the venue (e.g. 8 Binance, 1 Hyperliquid); CHECK > 0 |

UNIQUE (symbol, venue, time)

## signals

| Column | Type | Notes |
|---|---|---|
| symbol | varchar(20) | |
| day | date | |
| rule | varchar(40) | rule name; `"ensemble"` is one value |
| value | NUMERIC(38,18) | |

UNIQUE (symbol, day, rule)

## decisions

One row per scheduled cycle, including cycles that do nothing.

| Column | Type | Notes |
|---|---|---|
| cycle | varchar(10) | `"daily"` or `"intraday"` |
| ran_at | timestamptz | |
| symbol | varchar(20) (null) | |
| target_weight | NUMERIC(38,18) (null) | |
| current_weight | NUMERIC(38,18) (null) | |
| action | varchar(20) | `"none"`, `"plan_proposed"`, `"blocked"`, `"error"` |
| reason | text | plain-English reason |
| details | jsonb | default `{}` |

## plans

| Column | Type | Notes |
|---|---|---|
| sleeve | varchar(20) | `"trend"` or `"dip_bid"` |
| symbol | varchar(20) | |
| side | varchar(10) | |
| status | varchar(20) | indexed; `"pending"`, `"approved"`, `"denied"`, `"expired"`, `"voided"`, `"completed"` |
| reference_price | NUMERIC(38,18) | |
| target_weight | NUMERIC(38,18) | |
| size_fraction | NUMERIC(38,18) | default `1`; approval can shrink, never enlarge |
| entry_orders | jsonb | default `[]` |
| stop_price | NUMERIC(38,18) (null) | |
| worst_case_loss | NUMERIC(38,18) | |
| est_fees | NUMERIC(38,18) | |
| rationale | text | |
| expires_at | timestamptz | approval expiry |
| decided_at | timestamptz (null) | |
| decision_id | bigint FK -> decisions.id | |

## orders

| Column | Type | Notes |
|---|---|---|
| plan_id | bigint FK -> plans.id | |
| venue | varchar(40) | |
| client_order_id | varchar(64) | UNIQUE |
| venue_order_id | varchar(64) (null) | |
| symbol | varchar(20) | |
| side | varchar(10) | |
| type | varchar(10) | `"limit"`, `"market"`, `"stop"` |
| price | NUMERIC(38,18) (null) | |
| stop_price | NUMERIC(38,18) (null) | |
| quantity | NUMERIC(38,18) | |
| status | varchar(20) | indexed; `"new"`, `"open"`, `"partially_filled"`, `"filled"`, `"cancelled"`, `"rejected"` |
| mode | varchar(10) | `"paper"` or `"live"` |
| shadow | boolean | default `false` |

## fills

| Column | Type | Notes |
|---|---|---|
| order_id | bigint FK -> orders.id | |
| time | timestamptz | indexed |
| price, quantity, fee | NUMERIC(38,18) | |
| fee_currency | varchar(10) | |
| liquidity | varchar(10) | `"maker"` or `"taker"` |

## balances

| Column | Type | Notes |
|---|---|---|
| time | timestamptz | indexed |
| mode | varchar(10) | `"paper"` or `"live"` |
| asset | varchar(20) | |
| quantity | NUMERIC(38,18) | |

## equity_snapshots

| Column | Type | Notes |
|---|---|---|
| time | timestamptz | indexed |
| mode | varchar(10) | `"paper"` or `"live"` |
| equity_usd | NUMERIC(38,18) | |
| high_water_mark | NUMERIC(38,18) | |
| drawdown | NUMERIC(38,18) | |

## events

| Column | Type | Notes |
|---|---|---|
| time | timestamptz | indexed |
| kind | varchar(60) | |
| severity | varchar(20) | |
| message | text | |
| details | jsonb | default `{}` |

## heartbeats

| Column | Type | Notes |
|---|---|---|
| service | varchar(60) | |
| time | timestamptz | indexed |
| status | varchar(20) | |
| detail | text | |

## settings

| Column | Type | Notes |
|---|---|---|
| key | varchar(100) | PRIMARY KEY (no `id`/`created_at`) |
| value | jsonb | |
| updated_at | timestamptz | |

## audit_log

| Column | Type | Notes |
|---|---|---|
| time | timestamptz | indexed |
| actor | varchar(60) | |
| action | varchar(60) | |
| before | jsonb | default `{}` |
| after | jsonb | default `{}` |

## digests

| Column | Type | Notes |
|---|---|---|
| day | date | UNIQUE |
| body_md | text | |
| stats | jsonb | default `{}` |

## shadow_trades

One hypothetical trade of a shadow sleeve (spec G3). Nothing here is a
plan, order or fill.

| Column | Type | Notes |
|---|---|---|
| sleeve | varchar(20) | `"dip"` |
| symbol | varchar(20) | |
| opened_at | timestamptz | |
| entry_price | NUMERIC(38,18) | fill price (price + slippage) |
| quantity | NUMERIC(38,18) | |
| cost_usd | NUMERIC(38,18) | dollars spent, fee included |
| entry_fee_usd | NUMERIC(38,18) | |
| closed_at | timestamptz (null) | null while open |
| exit_price | NUMERIC(38,18) (null) | fill price (price − slippage) |
| exit_fee_usd | NUMERIC(38,18) (null) | |
| pnl_usd | NUMERIC(38,18) (null) | proceeds after fee − cost_usd |
| exit_reason | varchar(60) (null) | e.g. `"held 5 days"` |
| details | jsonb | default `{}` |

INDEX (sleeve, closed_at)

## shadow_marks

A shadow sleeve's end-of-day book value (spec G3).

| Column | Type | Notes |
|---|---|---|
| sleeve | varchar(20) | |
| day | date | |
| equity_usd | NUMERIC(38,18) | cash + exposure |
| cash_usd | NUMERIC(38,18) | |
| exposure_usd | NUMERIC(38,18) | open positions at the day's price |
| open_trades | integer | |
| closed_trades | integer | running total |
| note | text | one sentence per symbol |
| details | jsonb | default `{}` |

UNIQUE (sleeve, day)

## shadow_state

Per-(sleeve, symbol) scratch state for later shadow specs (G5); created
here, unused by G3. `updated_at` instead of `created_at`.

| Column | Type | Notes |
|---|---|---|
| updated_at | timestamptz | |
| sleeve | varchar(20) | |
| symbol | varchar(20) | |
| state | jsonb | default `{}` |

UNIQUE (sleeve, symbol)

## Indexes

Every `time`/`open_time` column is indexed (`candles.open_time`,
`funding_rates.time`, `fills.time`, `balances.time`,
`equity_snapshots.time`, `events.time`, `heartbeats.time`,
`audit_log.time`), plus `plans.status` and `orders.status`.
