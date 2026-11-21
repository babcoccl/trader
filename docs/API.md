# Dashboard API (spec 3.1)

Everything the dashboard needs, and nothing that bypasses risk or approval.
All routes are under `/api`, speak JSON, and — except `POST /api/login` —
require the session cookie set by login. `GET /health` (outside `/api`)
stays unauthenticated for container health checks.

Money and quantities are JSON strings (Decimal on the wire, matching
`NUMERIC(38,18)`). Timestamps are ISO 8601 UTC.

## Authentication

`POST /api/login` — body `{"password": "..."}`; the password is the single
`TRADER_DASHBOARD_PASSWORD` value. Sets an `HttpOnly` `trader_session`
cookie (in-memory token; logins do not survive an api restart).
`POST /api/logout` clears it. Every other `/api` route returns `401`
without the cookie.

```json
// POST /api/login -> 200
{"status": "ok", "detail": {}}
```

## Routes

### `GET /api/status` → `StatusOut`

```json
{
  "mode": "paper",
  "halted": false,
  "halt_reason": null,
  "equity_usd": "10000.000000000000000000",
  "invested_fraction": 0.06,
  "drawdown": 0.0,
  "halt_at": 0.35,
  "pending_plans": 1,
  "last_check_at": "2026-11-21T18:29:00Z",
  "last_check_reason": "Checked prices. Nothing to do.",
  "next_daily_decision_at": "2026-11-22T00:05:00Z",
  "last_trade_at": "2026-11-21T16:29:00Z",
  "services": [{"name": "engine", "status": "ok", "last_heartbeat_at": "2026-11-21T18:29:50Z"}]
}
```

### `GET /api/decisions?cycle=&limit=&before=&before_id=` → `DecisionOut[]`

`cycle` is `daily` or `intraday`; `limit` 1–500 (default 50); `before` is
an ISO `ran_at` cursor — pass the oldest `ran_at` of the previous page.

```json
[{
  "id": 61, "cycle": "intraday", "ran_at": "2026-11-21T18:29:00Z",
  "symbol": null, "target_weight": null, "current_weight": null,
  "action": "none", "reason": "Checked prices. Nothing to do.", "details": {}
}]
```

### `GET /api/plans?status=` → `PlanOut[]` · `GET /api/plans/{id}` → `PlanOut`

`status` is `pending|approved|denied|expired|voided|completed`.

```json
{
  "id": 7, "sleeve": "trend", "symbol": "BTC-USD", "side": "buy",
  "status": "pending", "reference_price": "60000", "target_weight": "0.2",
  "size_fraction": "1", "entry_orders": [{"side": "buy", "type": "limit", "price": "60000", "quantity": "0.003"}],
  "stop_price": "45000", "worst_case_loss": "10", "est_fees": "2",
  "rationale": "I would like to raise BTC-USD from 0% to 20% of the account ...",
  "expires_at": "2026-11-22T00:29:00Z", "decided_at": null, "decision_id": 60,
  "created_at": "2026-11-21T18:29:00Z"
}
```

`GET /api/plans/{id}/orders` → `{"orders": [OrderOut], "shadow_orders": [OrderOut]}`.

### `POST /api/plans/{id}/approve` — body `{"size_fraction": 1.0}`

`size_fraction` must be `1.0`, `0.5` or `0.25`; anything else is `422`.
Goes through `plans.approval.approve` (risk re-check, expiry check, audit
row). A refused transition is `409`; the plan stays `pending`.

### `POST /api/plans/{id}/deny` → `PlanOut`

Denied plans get full-size shadow orders so reports can show their
would-have-been result. Refused when not `pending` (`409`).

### `GET /api/positions` → `PositionOut[]`

```json
[{
  "asset": "BTC", "symbol": "BTC-USD", "quantity": "0.01",
  "price": "60000", "value_usd": "600", "share": 0.06,
  "avg_cost": "60180", "unrealised_gain_usd": "-1.8",
  "stop_price": "45000", "plan_id": 5,
  "resting_orders": [{"id": 9, "client_order_id": "5-2", "side": "sell",
    "type": "limit", "price": "61000", "stop_price": null,
    "quantity": "0.01", "status": "open", "created_at": "2026-11-21T18:00:00Z"}]
}]
```

### `GET /api/activity?kind=&q=&limit=&before=&before_kind=&before_id=` → `ActivityItem[]`

Newest-first union of `events` (kind = the event kind, e.g. `deposit`,
`alarm`), `decisions` (`decision`), `fills` (`fill`) and `heartbeats`
(`heartbeat`). `kind` filters on that item kind; `q` is a substring match
on the text; `before` is a time cursor — pair it with the oldest item's
`kind` as `before_kind` and `ref_id` as `before_id` so rows sharing a
timestamp are not skipped (same for `before_id` on `/api/decisions`).

```json
[{
  "time": "2026-11-21T18:29:00Z", "kind": "decision", "ref_id": 61,
  "text": "Checked prices. Nothing to do.",
  "details": {"cycle": "intraday", "action": "none", "symbol": null}
}]
```

### `GET /api/reports/equity?from=&to=` → `EquityPoint[]`

Account equity plus baselines scaled to the first account value in the
window: `cash` (flat), `btc` (buy-and-hold BTC), `btc_eth` (50/50
BTC+ETH). Baselines are `null` where no daily candle exists.

```json
[{"time": "2026-11-21T18:29:00Z", "account": "10000", "cash": "10000",
  "btc": "10000", "btc_eth": "10000"}]
```

### `GET /api/reports/summary` → `SummaryOut`

```json
{
  "from_at": "2026-11-21T18:29:00Z", "to_at": "2026-11-21T18:29:00Z",
  "return_pct": null, "worst_drop_pct": 0.0, "fees_usd": "1.8",
  "vetoes": {"plans": 0, "shadow_fills": 0, "result_usd": null},
  "paper_vs_backtest_gap_pp": null
}
```

`vetoes` is the scorecard from shadow orders of plans that were denied,
expired or voided: `plans` counts them, `shadow_fills` the fills, and
`result_usd` the would-have-been cash plus marked value of units still
held. `paper_vs_backtest_gap_pp` is `null` until daily candles cover the
equity window.

### `GET /api/digests?limit=` → `DigestOut[]`

```json
[{"id": 1, "day": "2026-11-20", "body_md": "# digest", "stats": {}}]
```

### `GET /api/settings` → `SettingOut[]` · `PUT /api/settings/{key}`

`GET` returns every `settings` row plus the defaults of editable keys not
yet set (`updated_at: null`). `PUT` body `{"value": ...}`; only these keys
are editable — everything else is `404`, an invalid value `422`. Every
write lands in `audit_log` (actor `owner`). Risk limits and risk state
are not editable here.

| Key | Validation | Default |
|---|---|---|
| `autonomy` | only `"approve_every_plan"` | `"approve_every_plan"` |
| `approval_expiry_hours` | 0.5–72 | `6.0` |
| `silence_alarm_minutes` | 1–1440 | `5` |
| `digest_time_utc` | `HH:MM` (24h) | `"00:30"` |
| `t_bill_rate` | 0–1 | `0.0` |
| `mode` | `paper`/`live`; `live` only when `TRADER_LIVE_ENABLED=true` | `"paper"` |

```json
// PUT /api/settings/silence_alarm_minutes {"value": 10} -> 200
{"key": "silence_alarm_minutes", "value": 10, "updated_at": "2026-11-21T18:30:00Z"}
```

### `POST /api/controls/halt` — body `{"reason": "..."}`

Calls `risk.engine.halt` (audited). `POST /api/controls/resume` clears the
halt and resets the high-water mark.

### `POST /api/controls/flatten` — body `{"confirm": "FLATTEN"}`

Any other `confirm` value is `422`. Raises the `risk.flatten_requested`
flag; the executor sells everything on its next loop.

```json
{"status": "ok", "detail": {"flatten_requested": true}}
```

### `GET /api/stream` — server-sent events

`text/event-stream`. Emits a `status` event on connect, then polls the
database every 2 seconds and emits, as the rows land:

- `decision` — new `decisions` row
- `plan` — new plan, or a plan whose `status`/`size_fraction` changed
- `fill` — new non-shadow `fills` row
- `alarm` — new `events` row of kind `alarm`, `alarm_cleared` or `emergency_stop`
- `status` — full `StatusOut` payload when a new `equity_snapshots` row lands

```
event: decision
data: {"id": 61, "cycle": "intraday", "ran_at": "...", "symbol": null, "action": "none", "reason": "Checked prices. Nothing to do."}
```

## Error codes

`401` not logged in · `404` unknown plan or non-editable setting ·
`409` plan transition refused (`ApprovalError`) · `422` invalid body or
setting value · `503` login when `TRADER_DASHBOARD_PASSWORD` is unset.
