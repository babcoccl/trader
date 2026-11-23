# Autonomous trader: per-session specifications

Written so that a session on a lightweight model can execute one spec without making design choices. Read section A once, then only your own spec. Companion files: `docs/PLAN.md` (phases, order), `docs/DESIGN.md` (reasons), `docs/mockup.html` (screens).

## A. Conventions for every session

**How to work**

1. Do exactly one spec. Branch `devin/<spec id>-<slug>`, one pull request to `main` of `babcoccl/trader`.
2. Create only the files listed under "Files". If you believe another file is needed, add it and say why in the PR description.
3. If something in the spec is ambiguous or contradicts the code you find, stop and ask. Do not guess, and do not change another module's public interface.
4. Never use real API keys, never place a real order, never weaken a limit or skip a test to get green CI.
5. Finish when every "Done when" line is true and CI is green. PR description: what was built, how each "Done when" line was checked, what is not done.

**Stack (fixed)**

| Area | Choice |
|---|---|
| Language | Python 3.12, type hints everywhere, `mypy --strict` on `src/trader` |
| Packaging | `pyproject.toml`, installed with `pip install -e ".[dev]"` |
| Lint and format | `ruff check` and `ruff format` |
| Tests | `pytest`; database tests use the Compose Postgres through the `TEST_DATABASE_URL` variable |
| Database | PostgreSQL 16, SQLAlchemy 2.x (typed ORM), Alembic migrations |
| API | FastAPI, Pydantic v2, server-sent events for live updates |
| Scheduler | APScheduler inside each long-running service |
| Web | React 18, TypeScript, Vite, TanStack Query, Recharts; tests with Vitest and Testing Library |
| Runtime | Docker Compose; one image for Python services, one for web |

**Layout (fixed)**

```
src/trader/
  core/        config.py, clock.py, logging.py, types.py, db.py
  data/        sources/, collector.py, candles.py
  strategy/    rules.py, ensemble.py, sizing.py, dip_bid.py
  risk/        engine.py, limits.py
  plans/       builder.py, approval.py
  ledger/      models.py, accounting.py
  execution/   base.py, paper.py, bitstamp.py, reconcile.py
  engine/      daily_cycle.py, intraday_cycle.py
  ops/         heartbeat.py, watchdog.py, digest.py
  llm/         (phase 6)
  api/         app.py, routes/, schemas.py, sse.py, auth.py
  backtest/    engine.py, costs.py
web/           React app
migrations/    Alembic
research/      study scripts and fixtures (read-only reference; excluded from lint, types and the image)
tests/         mirrors src/trader
docs/          DESIGN.md, DECISIONS.md, PLAN.md, SPECS.md, RUNBOOK.md
```

**Rules of the domain (fixed)**

- All timestamps are UTC, timezone-aware, stored as `timestamptz`. A "day" is 00:00 to 24:00 UTC. A daily candle is keyed by its open time.
- Money and quantities are `decimal.Decimal` in code and `NUMERIC(38,18)` in the database. Floats are allowed only inside `strategy/` and `backtest/` maths.
- Time is read only through `core.clock.Clock` (`now() -> datetime`), so tests can freeze it.
- Symbols are `"BTC-USD"` and `"ETH-USD"` inside the application; adapters translate to venue names.
- `mode` is `"paper"` or `"live"`, default `"paper"`. Live order placement additionally requires `TRADER_LIVE_ENABLED=true`.
- Every scheduled cycle writes exactly one row to `decisions` with a plain-English `reason`, including when it does nothing.
- Every service writes a row to `heartbeats` at least every 60 seconds.
- Settings live in the `settings` table with defaults in `core/config.py`; each change writes an `audit_log` row.

**Strategy constants (do not change; they come from the studies)**

| Name | Value |
|---|---|
| Assets | BTC-USD, ETH-USD |
| Rules | SMA 50, 100, 200; momentum 30, 90, 180; breakout 20, 55, 100 (definitions in spec 1.2) |
| Signal | Plain average of the nine rules, 0 to 1 per asset |
| Sizing | Volatility target 0.40 a year, per-asset weight = signal / 2, scaled by `min(1, 0.40 / vol)`, total at most 1.0, no leverage, no shorts |
| Trading band | Trade an asset only if `abs(target - current) > 0.20 * max(target, current)`, or target is 0 and current is not |
| Decision time | 00:05 UTC daily, using candles up to the day that just closed |
| Drawdown halt | 35% below the account high-water mark |
| Approval expiry | 6 hours; void if price moves more than 2% from the plan's reference price |
| Venue | Bitstamp (Bitstamp USA; its legal page lists a Maine licence). Markets `btcusd`, `ethusd`. Minimum order $10. Spot order types by API: limit (with maker-or-cancel, IOC, FOK, good-till-date flags), market, instant. No stop orders for spot by API |
| Backtest costs | Bitstamp, under $10,000 a month: maker 0.30%, taker 0.40%, taker slippage 0.01% (measured: a $10,000 market order cost under 0.003% beyond the mid price) |
| Emergency stop | Run by the application, not by the exchange: the executor checks the price every 30 seconds and sells at market when it is at or under a plan's `stop_price`. Positions are unprotected while the home box is offline (accepted by the owner on 2026-11-20) |

**Commands (give both forms in any docs you write)**

| Purpose | Linux / macOS | Windows PowerShell |
|---|---|---|
| Start stack | `docker compose up -d` | `docker compose up -d` |
| Install | `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"` | `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -e ".[dev]"` |
| Checks | `ruff check . && ruff format --check . && mypy src/trader && pytest` | `ruff check .; ruff format --check .; mypy src/trader; pytest` |
| Migrate | `alembic upgrade head` | `alembic upgrade head` |

## B. Specs

Each spec lists: Goal, Depends on, Files, Interfaces, Steps, Tests, Done when, Out of scope.

### 0.1 Repository skeleton

- **Goal:** an empty but runnable project with CI.
- **Depends on:** nothing. The repo already holds `docs/`, `research/`, `README.md`, `AGENTS.md` and `.gitignore` from the first commit; keep them.
- **Files:** `pyproject.toml`, `.pre-commit-config.yaml`, `.env.example`, `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`, additions to the existing `README.md` (a "Development" section with both command forms) and `.gitignore`, `src/trader/__init__.py`, `src/trader/core/{config,clock,logging,db}.py`, `src/trader/api/app.py`, `tests/core/test_config.py`, `tests/api/test_health.py`, empty `__init__.py` in every package in the layout.
- **Interfaces:**
  - `core.config.Settings` (pydantic-settings): `database_url: str`, `mode: Literal["paper","live"] = "paper"`, `live_enabled: bool = False`, `log_level: str = "INFO"`; env prefix `TRADER_`.
  - `core.clock.Clock` protocol with `now() -> datetime`; `SystemClock`; `FrozenClock(at)` with `advance(timedelta)`.
  - `core.logging.setup()` emits one JSON object per line with `ts`, `level`, `service`, `msg`.
  - `core.db.get_engine()`, `core.db.session_scope()` context manager.
  - `GET /health` returns `{"status":"ok","mode":"paper"}`.
- **Steps:** create the layout; Compose services `db` (postgres:16, volume, healthcheck) and `api` (uvicorn on 8000); CI job runs the Checks command against a Postgres service container; pre-commit runs ruff. Exclude `research/` from ruff, mypy, pytest collection and the Docker image.
- **Tests:** settings default to paper; `FrozenClock.advance` works; `/health` returns 200.
- **Done when:** `docker compose up -d` then `curl localhost:8000/health` returns ok; CI green; README has both command forms.
- **Out of scope:** any table, any trading logic.

### 0.2 Research reproducibility check (optional, not a dependency of any other spec)

- **Goal:** confirm that the full study can be rebuilt from public data by following `research/README.md`.
- **Depends on:** nothing. Already in the repo: all documents, the study scripts, and `research/fixtures/` (the BTC and ETH daily prices and the expected signal and backtest values that specs 1.2 and 1.3 test against).
- **Files:** corrections to `research/README.md` only, plus path fixes in `research/*.py` if a step fails.
- **Steps:** in a clean checkout follow `research/README.md` from the first step (about 200 MB of downloads); run `python make_fixtures.py` and confirm `git diff --stat research/fixtures` shows no change in `expected_backtest.json` beyond the last few digits; run `python check_spec.py`.
- **Done when:** the PR description quotes the `check_spec.py` output and lists any README step that had to be corrected.
- **Out of scope:** changing study logic or the committed fixtures.

### 0.3 Database schema

- **Goal:** all tables, created by one Alembic migration.
- **Depends on:** 0.1.
- **Files:** `src/trader/ledger/models.py` (all ORM models), `migrations/` (env and first revision), `alembic.ini`, `docs/SCHEMA.md`, `tests/ledger/test_schema.py`.
- **Interfaces (tables; every table has `id BIGSERIAL PK` and `created_at timestamptz default now()` unless stated):**
  - `candles(symbol, interval, open_time, open, high, low, close, volume, source)`; unique `(symbol, interval, open_time, source)`; `interval` is `"1d"` or `"5m"`.
  - `funding_rates(symbol, venue, time, rate)`; unique `(symbol, venue, time)`.
  - `signals(symbol, day, rule, value)`; unique `(symbol, day, rule)`; `rule` includes `"ensemble"`.
  - `decisions(cycle, ran_at, symbol NULL, target_weight NULL, current_weight NULL, action, reason, details JSONB)`; `cycle` is `"daily"` or `"intraday"`; `action` is `"none"`, `"plan_proposed"`, `"blocked"`, `"error"`.
  - `plans(sleeve, symbol, side, status, reference_price, target_weight, size_fraction default 1, entry_orders JSONB, stop_price NULL, worst_case_loss, est_fees, rationale, expires_at, decided_at NULL, decision_id FK)`; `status` is `"pending"`, `"approved"`, `"denied"`, `"expired"`, `"voided"`, `"completed"`; `sleeve` is `"trend"` or `"dip_bid"`.
  - `orders(plan_id FK, venue, client_order_id UNIQUE, venue_order_id NULL, symbol, side, type, price NULL, stop_price NULL, quantity, status, mode, shadow bool default false)`; `type` is `"limit"`, `"market"`, `"stop"`; `status` is `"new"`, `"open"`, `"partially_filled"`, `"filled"`, `"cancelled"`, `"rejected"`.
  - `fills(order_id FK, time, price, quantity, fee, fee_currency, liquidity)`; `liquidity` is `"maker"` or `"taker"`.
  - `balances(time, mode, asset, quantity)`; `equity_snapshots(time, mode, equity_usd, high_water_mark, drawdown)`.
  - `events(time, kind, severity, message, details JSONB)`; `heartbeats(service, time, status, detail)`; `settings(key PK, value JSONB, updated_at)`; `audit_log(time, actor, action, before JSONB, after JSONB)`; `digests(day UNIQUE, body_md, stats JSONB)`.
- **Steps:** write models; autogenerate the revision and review it by hand; add indexes on every `time`/`open_time` column and on `plans.status`, `orders.status`.
- **Tests:** upgrade to head then downgrade to base on an empty database; inserting a duplicate candle raises `IntegrityError`.
- **Done when:** tests pass; `docs/SCHEMA.md` lists every table and column.
- **Out of scope:** repositories or business logic.

### 1.1 Market-data collector

- **Goal:** daily and 5-minute candles for both assets, gap-free, restartable.
- **Depends on:** 0.3.
- **Files:** `data/sources/base.py`, `data/sources/bitstamp.py`, `data/sources/coinbase.py`, `data/candles.py`, `data/collector.py`, `tests/data/*`, Compose service `collector`.
- **Interfaces:**
  - `class CandleSource(Protocol): name: str; def fetch(symbol: str, interval: str, start: datetime, end: datetime) -> list[Candle]`.
  - `candles.upsert(session, candles) -> int`; `candles.load(session, symbol, interval, start, end, source=None) -> pandas.DataFrame` indexed by `open_time`; `candles.find_gaps(session, symbol, interval, start, end) -> list[tuple[datetime, datetime]]`.
  - Primary source: Bitstamp public REST, base `https://www.bitstamp.net/api/v2`, no key needed: `GET /ohlc/{market}/?step=300|86400&limit=<=1000&start=<unix seconds>` returns `data.ohlc[]` with `timestamp`, `open`, `high`, `low`, `close`, `volume` as strings; `GET /ticker/{market}/` returns `bid`, `ask`, `last`; `GET /markets/` returns `minimum_order_value`, `base_decimals`, `counter_decimals`. Symbol map `BTC-USD -> btcusd`, `ETH-USD -> ethusd`. Documented limit: 10,000 requests per 10 minutes. Backup source: Coinbase public candles; read its documentation for the endpoint and limits before coding and cite the page in the PR.
- **Steps:** backfill daily candles from 2019-01-01 and 5-minute candles for the last 30 days (earlier history is not needed live; the backtest uses `research/` data); every minute fetch the last two 5m candles and, after 00:01 UTC, yesterday's 1d candle; never store a candle whose period has not ended; on start, run `find_gaps` and refill; write a heartbeat each loop; on source failure switch to the backup and write an `events` row.
- **Tests:** HTTP mocked with recorded JSON fixtures; upsert twice gives the same row count; a deleted day is found by `find_gaps` and refilled; an unfinished candle is rejected.
- **Done when:** after `docker compose up -d collector`, the PR description shows row counts per symbol and interval and zero gaps.
- **Out of scope:** order books, private endpoints.

### 1.2 Strategy library

- **Goal:** the nine rules, ensemble and sizing as pure functions, numerically identical to `research/bt.py`.
- **Depends on:** 0.1.
- **Files:** `strategy/rules.py`, `strategy/ensemble.py`, `strategy/sizing.py`, `tests/strategy/*`. Test data is already committed: `research/fixtures/btc_eth_daily.csv` (columns `date`, `BTC-USD_open`, `BTC-USD_close`, `ETH-USD_open`, `ETH-USD_close`) and `research/fixtures/expected_signals.csv` (columns `date`, `symbol`, `sma50`, `sma100`, `sma200`, `momentum30`, `momentum90`, `momentum180`, `breakout20`, `breakout55`, `breakout100`, `ensemble`, `annual_vol`, `target_weight`; empty cell means NaN). `research/check_spec.py` is a working reference implementation of every function below; read it, but write your own typed code.
- **Interfaces (input `close` is a `pandas.Series` of daily closes, oldest first; outputs are Series aligned to it):**
  - `rules.sma(close, n)`: 1.0 where `close > close.rolling(n, min_periods=n).mean()`, else 0.0; NaN where the mean is NaN. n in 50, 100, 200.
  - `rules.momentum(close, n)`: 1.0 where `close > close.shift(n)`, else 0.0; NaN where the shifted value is NaN. n in 30, 90, 180.
  - `rules.breakout(close, n)`: `hi = close.rolling(n, min_periods=n).max()`, `lo = close.rolling(n // 2, min_periods=n // 2).min()`; state becomes 1.0 on a day where `close >= hi`, 0.0 where `close <= lo`, otherwise keeps the previous state; NaN where `hi` is NaN. n in 20, 55, 100.
  - `ensemble.signal(close) -> Series`: sum of the nine rules with NaN treated as 0, divided by 9; NaN where `sma(close, 200)` is NaN.
  - `sizing.annual_vol(close) -> Series`: `close.pct_change().ewm(span=30, min_periods=20).std() * sqrt(365)`.
  - `sizing.target_weights(signals: dict[str, float], vols: dict[str, float], vol_target=0.40) -> dict[str, float]`: `w = signal / n_assets * min(1, vol_target / vol)`; if the weights sum above 1.0, scale all down to sum 1.0; NaN signal or vol gives 0.
  - `sizing.should_trade(target, current, band=0.20) -> bool`: the trading-band rule from section A.
- **Tests:** for both symbols and every date, each rule, `ensemble.signal`, `sizing.annual_vol` and `sizing.target_weights` equal the matching column of `expected_signals.csv` within 1e-9, with NaN in the same places; hand-made cases for each rule's edge (exactly equal to the average, first valid day, state held between breakouts).
- **Done when:** tests pass; no function reads the clock, the database or the network.
- **Out of scope:** anything intraday.

### 1.3 Backtest engine

- **Goal:** an in-repo backtest that uses `strategy/` and reproduces the study exactly.
- **Depends on:** 1.2.
- **Files:** `backtest/costs.py`, `backtest/engine.py`, `backtest/__main__.py`, `tests/backtest/*`.
- **Interfaces:**
  - `costs.CostModel(maker_fee: float, taker_fee: float, slippage: float)`; presets `BITSTAMP = CostModel(0.003, 0.004, 0.0001)`, `STUDY_040 = CostModel(0.004, 0.004, 0.0005)`. The backtest charges `taker_fee + slippage` on every trade.
  - `engine.load_fixture(path) -> tuple[DataFrame, DataFrame]` returns `(opens, closes)` indexed by date with columns `BTC-USD`, `ETH-USD`.
  - `engine.run(opens, closes, cost: CostModel, vol_target=0.40, band=0.20, start="2018-04-01", end="2026-11-19") -> BacktestResult` with fields `equity: Series`, `weights: DataFrame`, `turnover: Series`, and `stats(start=None, end=None) -> dict` giving `cagr`, `maxdd`, `sharpe`, `turnover_yr`, `exposure`.
  - Exact procedure (matches `research/bt.py` and `research/check_spec.py`): compute target weights for every date in the whole file from that day's close (spec 1.2); the weights used on day t are those of day t-1 (0 if none). Start with cash 1.0 and no units. For each day t from `start` to `end`: (1) `e = cash + sum(units * open)`; (2) for each asset, `cur = units * open / e`; trade it if `abs(tgt - cur) > band * max(tgt, cur)` or (`tgt == 0` and `cur != 0`); (3) if any asset trades: `traded = sum(abs(tgt - cur)) * e` over the traded assets, set their `units = tgt * e / open`, then `cash = e - traded * (taker_fee + slippage) - sum(units * open)` over all assets, and record `turnover[t] = traded / e`; (4) `equity[t] = cash + sum(units * close)`, `exposure[t] = sum(units * close) / equity[t]`.
  - Stats over a date slice of `equity`: `years = number_of_rows / 365`; `cagr = (last / first) ** (1 / years) - 1`; `maxdd = min(equity / running_max - 1)`; `sharpe = mean(daily_returns) / std(daily_returns) * sqrt(365)` with pandas' default sample standard deviation; `turnover_yr = sum(turnover) / years`; `exposure = mean(exposure)`.
  - CLI: `python -m trader.backtest --data research/fixtures/btc_eth_daily.csv --cost study_040` prints full-period and 2022-onward stats at bands 0, 0.10, 0.20, 0.30.
- **Tests:** for every entry in `research/fixtures/expected_backtest.json` (`runs.<name>` has `taker_fee`, `slippage`, `band`, `full`, `from_2022`, `final_equity`), `run` reproduces `full.cagr`, `full.maxdd`, `full.sharpe`, `full.turnover_yr`, `full.exposure`, `from_2022.cagr`, `from_2022.maxdd`, `from_2022.sharpe` within 1e-6; the equity series for `study_040`, band 0.20 matches `expected_equity_study_040_band_0.2.csv` within 1e-6; look-ahead test: changing prices after day t never changes weights decided at or before day t; zero-cost, always-1 signal on one asset equals buy and hold.
- **Done when:** tests pass and the PR shows the CLI output (headline: 24.5% a year full period, 11.4% a year from 2022, at `study_040`, band 0.20).
- **Out of scope:** intraday backtests (they stay in `research/`).

### 1.4 Daily decision cycle

- **Goal:** each day at 00:05 UTC compute target weights and record a decision with a reason.
- **Depends on:** 1.1, 1.2.
- **Files:** `engine/daily_cycle.py`, `engine/__main__.py` (service entry with APScheduler), `tests/engine/test_daily_cycle.py`, Compose service `engine`.
- **Interfaces:**
  - `daily_cycle.run(session, clock, portfolio: PortfolioView) -> list[Decision]` where `PortfolioView` has `equity_usd: Decimal` and `weights: dict[str, float]`. Until spec 2.3 exists use a stub returning 100% cash.
  - Steps inside `run`: load 1d candles up to yesterday; if yesterday's candle is missing for any asset write one decision `action="blocked"`, `reason="Yesterday's BTC-USD candle is missing; no decision made."` and stop; compute rule values and store them in `signals`; compute target weights; for each asset write one decision row.
  - Reason templates (use exactly these shapes): `"Checked {symbol}. {k} of 9 rules positive. Target {t:.0%}, holding {c:.0%}. Gap is under the 20% trading band, so no trade."`; `"... Target {t:.0%}, holding {c:.0%}. Proposing a plan to {buy|sell} the difference."`; `"... Target {t:.0%}, holding {c:.0%}. No change."`.
  - `details` JSON holds each rule's value, the volatility, and the raw and scaled weights.
- **Steps:** make `run` idempotent per day (a second run on the same day updates nothing and returns the existing rows); schedule at 00:05 UTC with a catch-up run at start if today's rows are missing.
- **Tests:** replay 30 consecutive fixture days with a frozen clock and get 60 decision rows, none skipped; missing candle gives a `blocked` row; second run the same day adds no rows.
- **Done when:** tests pass; with the stack running, the PR shows today's two decision rows.
- **Out of scope:** creating plans (2.2) and orders.

### 1.5 Five-minute evaluation cycle

- **Goal:** the system visibly evaluates the market every 5 minutes, even when it does nothing.
- **Depends on:** 1.1, 1.4.
- **Files:** `strategy/dip_bid.py`, `engine/intraday_cycle.py`, `tests/strategy/test_dip_bid.py`, `tests/engine/test_intraday_cycle.py`.
- **Interfaces:**
  - `dip_bid.hourly_sigma(close_5m: Series) -> float`: standard deviation of the last 288 five-minute log returns, times `sqrt(12)`; NaN if fewer than 288.
  - `dip_bid.quote(last_close: float, sigma: float, k=4.0, target_mult=0.5, stop_mult=2.0) -> DipQuote(bid, target, stop)`: `bid = last_close * (1 - k*sigma)`, `target = bid * (1 + target_mult*k*sigma)`, `stop = bid * (1 - stop_mult*k*sigma)`.
  - `dip_bid.trend_gate(yesterday_ensemble: float) -> bool`: `>= 0.5`.
  - `intraday_cycle.run(session, clock) -> Decision`: every 5 minutes at second 10, write one `decisions` row with `cycle="intraday"` whose `details` holds, per asset: last price, age of the last candle in seconds, hourly sigma, trend gate, the dip-bid quote, distance from price to the nearest stop of any open plan (null until 2.x). `reason` example: `"Checked prices. BTC 61,240, ETH 1,785. No stop is near. Sudden-drop bid would sit 1.8% under BTC and 2.3% under ETH. Nothing to do."` If the newest candle is older than 10 minutes: `action="blocked"`, reason says data is stale.
- **Steps:** this spec only observes and records. It places nothing.
- **Tests:** sigma and quote against hand-computed numbers; stale data gives `blocked`; one row per run.
- **Done when:** tests pass; with the stack up for 30 minutes the PR shows 6 intraday rows.
- **Out of scope:** placing dip bids (parked, see 5.5).

### 2.1 Risk engine

- **Goal:** one place that can veto anything.
- **Depends on:** 1.4.
- **Files:** `risk/limits.py`, `risk/engine.py`, `tests/risk/*` (use `hypothesis`).
- **Interfaces:**
  - `limits.Limits`: `max_gross=1.0`, `max_asset_weight=1.0`, `drawdown_halt=0.35`, `max_plan_loss_fraction=0.25`, `stale_candle_seconds=600`, `price_move_void=0.02`, read from `settings` with these defaults.
  - `engine.state(session) -> RiskState` with `halted: bool`, `halt_reason: str | None`, `high_water_mark`, `drawdown`.
  - `engine.check_plan(plan, portfolio, limits, state) -> RiskVerdict(ok: bool, reasons: list[str])`: reject if halted; if the plan would raise gross above `max_gross` or an asset above its cap; if `worst_case_loss / equity > max_plan_loss_fraction`; if the side is sell-short; if price data is stale. Sells that reduce exposure are always allowed, including while halted.
  - `engine.update_equity(session, equity_usd, clock)`: writes `equity_snapshots`; sets `halted=True` with reason when `drawdown >= drawdown_halt`.
  - `engine.halt(session, actor, reason)`, `engine.resume(session, actor)` (resume resets the high-water mark to current equity and writes `audit_log`), `engine.flatten_requested(session, actor)`, `engine.flatten_done(session, actor)` (the executor lowers the flag after selling).
- **Tests:** property test: for random portfolios and plans, an approved plan never results in gross above 1.0 or a short position; halt blocks buys and allows sells; drawdown of exactly 35% halts; resume resets the mark.
- **Done when:** tests pass; no code path outside `risk/` writes the halted flag.
- **Out of scope:** UI buttons (3.5).

### 2.2 Plan builder

- **Goal:** turn a weight change into an approvable plan.
- **Depends on:** 2.1.
- **Files:** `plans/builder.py`, `tests/plans/test_builder.py`, golden files under `tests/plans/golden/`.
- **Interfaces:**
  - `builder.build(symbol, target_weight, current_weight, equity_usd, price, annual_vol, cost: CostModel, clock) -> Plan | None` (None when `should_trade` is false).
  - Buy plan: notional `= (target - current) * equity`; one maker-or-cancel limit order at the current best bid, re-priced to the best bid every 5 minutes; any part unfilled 1 hour after approval is bought at market. Do not ladder orders below the price: study 3 found that waiting for dips on these entries costs more than it saves. `d = annual_vol / sqrt(365)` is one daily swing, used below.
  - Stop: the plan's `stop_price` is a disaster stop at `price * (1 - 3*d*sqrt(10))` (three ten-day swings, roughly 20% to 30% below), run by the application (see 2.4), because Bitstamp's API has no stop orders for spot. It is deliberately wide: the backtests had no stops, so a tight stop would make live results differ from them. The normal exit is a later sell plan from the daily cycle.
  - `worst_case_loss = notional * (1 - stop_price/price) + est_fees`; `est_fees = notional * (taker_fee + slippage) * 2`.
  - Sell plan: one limit order at `price`, converted to market after 1 hour; sell plans that only reduce exposure are created with `status="approved"` (pre-approved by the owner's decision on stops and exits).
  - `rationale`: `"I would like to raise {symbol} from {c:.0%} to {t:.0%} of the account because {k} of 9 trend rules are positive and volatility is {v:.0%} a year."` (and the matching sentence for lowering).
  - `expires_at = now + 6h`; `reference_price = price`.
- **Tests:** golden-file tests for buy, partial sell and full exit; `worst_case_loss` equals the sum of its parts; prices and quantities are rounded down to the venue's tick and lot size passed in as parameters.
- **Done when:** tests pass; `daily_cycle` calls `build`, runs `risk.check_plan`, saves the plan and sets `decisions.action="plan_proposed"` or `"blocked"` with the risk reasons.
- **Out of scope:** order placement.

### 2.3 Ledger and accounting

- **Goal:** positions, cash, equity and high-water mark derived from fills only.
- **Depends on:** 0.3, 1.4.
- **Files:** `ledger/accounting.py`, `tests/ledger/test_accounting.py`.
- **Interfaces:** `accounting.apply_fill(session, fill)`; `accounting.positions(session, mode) -> dict[str, Decimal]`; `accounting.cash(session, mode) -> Decimal`; `accounting.cost_basis(session, mode, symbol) -> Decimal` (average cost); `accounting.equity(session, mode, prices) -> Decimal`; `accounting.portfolio_view(session, mode, prices) -> PortfolioView`; `accounting.rebuild(session, mode)` recomputes balances from all fills; `accounting.deposit(session, mode, amount, clock)` for the paper starting balance.
- **Tests:** `hypothesis` generates random fill sequences; balances after `apply_fill` equal balances after `rebuild`; fees reduce cash; selling more than held raises.
- **Done when:** tests pass; `daily_cycle` uses `portfolio_view` in place of the stub.
- **Out of scope:** tax lots, multiple venues.

### 2.4 Paper execution adapter

- **Goal:** simulated orders against real prices through the same interface live will use.
- **Depends on:** 2.3.
- **Files:** `execution/base.py`, `execution/paper.py`, `execution/__main__.py` (service `executor`), `tests/execution/test_paper.py`.
- **Interfaces:**
  - `class ExecutionAdapter(Protocol)`: `place(order) -> str`, `cancel(venue_order_id)`, `open_orders() -> list`, `poll_fills(since) -> list[Fill]`, `balances() -> dict[str, Decimal]`, `symbol_rules(symbol) -> SymbolRules(tick, lot, min_notional)`.
  - `PaperAdapter(cost: CostModel)`: on each new 5m candle, a buy limit fills at its price if `candle.low < price * (1 - 0.0001)`; a sell limit if `candle.high > price * (1 + 0.0001)`; a market order fills at the candle open with slippage and the taker fee; a stop sell fills at `min(open, stop) * (1 - slippage)` when `low <= stop`. Limit fills pay the maker fee. Setting `paper_assume_taker` (default false): when true, limit orders are never filled as maker; they wait and convert to market at their time limit. Use it to see the worst-case cost.
  - Executor loop every 30 seconds: first the emergency-stop check (for every open position with a plan `stop_price`, if the latest price is at or under it, cancel that asset's open orders, sell the position at market, write an `events` row `kind="emergency_stop"`; this needs no approval and also runs once immediately at start-up); then place orders of approved plans, apply the plan's time rules (convert to market, cancel), poll fills, call `accounting.apply_fill`, call `risk.update_equity`, mark plans `completed`, write `events`.
- **Tests:** a scripted candle path yields the expected fills and fees; a price at or under `stop_price` sells the position within one loop, and also on the first loop after a restart; a limit exactly touched but not traded through does not fill; conversion to market after the time limit; restart in the middle neither loses nor duplicates orders (`client_order_id` is deterministic: `"{plan_id}-{n}"`).
- **Done when:** tests pass; an approved fixture plan in the running stack ends `completed` with fills in the ledger.
- **Out of scope:** real venues.

### 2.5 Approval state machine

- **Goal:** every plan passes through the owner or dies.
- **Depends on:** 2.2, 2.4.
- **Files:** `plans/approval.py`, `tests/plans/test_approval.py`.
- **Interfaces:** `approval.approve(session, plan_id, size_fraction, actor, clock)` with `size_fraction` in `{1.0, 0.5, 0.25}`; `approval.deny(session, plan_id, actor, clock)`; `approval.sweep(session, prices, clock)` run every minute: `pending` past `expires_at` becomes `expired`; `pending` whose price moved more than 2% from `reference_price` becomes `voided`. Approving scales every order quantity and `worst_case_loss` by `size_fraction`, re-runs `risk.check_plan`, and refuses if it now fails. Denied, expired and voided plans get shadow orders (`orders.shadow=true`) that the paper adapter fills at full size, so their would-have-been result can be reported.
- **Tests:** every transition, including approve-after-expiry refused and approve twice refused; size above 1.0 refused; shadow fills never touch balances.
- **Done when:** tests pass; all transitions write `audit_log`.
- **Out of scope:** the UI.

### 3.1 API

- **Goal:** everything the dashboard needs, and nothing that bypasses risk or approval.
- **Depends on:** 2.5.
- **Files:** `api/schemas.py`, `api/auth.py`, `api/sse.py`, `api/routes/{status,decisions,plans,positions,activity,reports,settings,controls}.py`, `tests/api/*`, `docs/API.md` (generated OpenAPI summary).
- **Interfaces (all under `/api`, JSON, session cookie from `POST /api/login` with a single password from `TRADER_DASHBOARD_PASSWORD`; every other route returns 401 without it):**
  - `GET /status`: `mode`, `halted`, `halt_reason`, `equity_usd`, `invested_fraction`, `drawdown`, `halt_at`, `pending_plans`, `last_check_at`, `last_check_reason`, `next_daily_decision_at`, `last_trade_at`, `services: [{name, status, last_heartbeat_at}]`.
  - `GET /decisions?cycle=&limit=&before=`; `GET /plans?status=`; `GET /plans/{id}`; `POST /plans/{id}/approve {size_fraction}`; `POST /plans/{id}/deny`.
  - `GET /positions` (holdings, cost basis, unrealised gain, stop, resting orders, plan id); `GET /activity?kind=&q=&limit=&before=` (union of events, decisions, fills, heartbeat summaries, newest first).
  - `GET /reports/equity?from=&to=` (account and baselines: cash, buy-and-hold BTC, buy-and-hold BTC+ETH); `GET /reports/summary` (return, worst drop, fees, veto scorecard from shadow orders, paper-versus-backtest gap when available); `GET /digests?limit=`.
  - `GET /settings`, `PUT /settings/{key}` (validated, audited); `POST /controls/halt`, `POST /controls/resume`, `POST /controls/flatten {confirm: "FLATTEN"}`.
  - `GET /stream`: server-sent events `status`, `decision`, `plan`, `fill`, `alarm`, sent when the rows are written (poll the database every 2 seconds).
- **Tests:** contract test per route against a seeded database; 401 without login; approve with `size_fraction=2` returns 422; flatten without the confirm word returns 422; live mode cannot be set through the API unless `TRADER_LIVE_ENABLED=true`.
- **Done when:** tests pass; `docs/API.md` lists every route with an example response.
- **Out of scope:** remote access, multi-user.

### 3.2 Web shell and Overview

- **Goal:** the frame, login, status strip and Overview tab as in the mockup.
- **Depends on:** 3.1.
- **Files:** `web/` Vite project, `web/src/api/client.ts` (typed from `docs/API.md`), `web/src/components/StatusStrip.tsx`, `web/src/pages/Overview.tsx`, `web/src/App.tsx` (tabs: Overview, Approvals, Positions, Activity, Reports, Controls; the other five render "Coming in spec 3.x"), tests, `web/Dockerfile`, Compose service `web` on port 3000.
- **Interfaces:** StatusStrip (on every page) shows a Running/Halted/Problem badge, `last_check_reason`, time since `last_check_at`, countdown to `next_daily_decision_at`, time since `last_trade_at`; it turns red and names the service when any service heartbeat is older than 5 minutes or an `alarm` event arrives. Overview shows four stat cards (account value, invested now, drop from peak with the halt level, needs your decision), the equity chart against buy-and-hold, "What the system thinks right now" (from the latest daily and intraday decisions), and "Why nothing has happened lately" (last 10 decision reasons).
- **Tests:** component tests with mocked API for normal, halted and stale-heartbeat states.
- **Done when:** tests pass; PR includes screenshots of Overview in normal and stale states at 1280 px.
- **Out of scope:** other tabs.

### 3.3 Approvals screen

- **Depends on:** 3.1, 3.2. **Files:** `web/src/pages/Approvals.tsx`, tests.
- **Interfaces:** pending plan cards with rationale first, then entry, exit, stop, worst case in dollars and percent of account, fees, account after, void condition, expiry countdown; size selector Full/Half/Quarter that recalculates the dollar figures before submitting; Approve and Deny buttons; past decisions table with "result if followed" and "your result" from shadow orders.
- **Tests:** approve at half sends `size_fraction: 0.5`; buttons disabled after expiry; no control can raise size above Full.
- **Done when:** tests pass; approving a seeded plan in the running stack leads to paper fills visible on reload; screenshots in the PR.

### 3.4 Positions and Activity screens

- **Depends on:** 3.1, 3.2. **Files:** `web/src/pages/Positions.tsx`, `web/src/pages/Activity.tsx`, tests.
- **Interfaces:** Positions: table (asset, value and share, average price, gain, stop, resting orders, plan link) and a funding-watch line (latest funding rate against the Treasury-bill setting). Activity: newest-first log with kind filter, text search, "load more", and each row expandable to its raw record.
- **Tests:** filter and search call the API with the right parameters; plan link opens the plan.
- **Done when:** tests pass; screenshots in the PR.

### 3.5 Reports and Controls screens

- **Depends on:** 3.1, 3.2. **Files:** `web/src/pages/Reports.tsx`, `web/src/pages/Controls.tsx`, tests.
- **Interfaces:** Reports: four stat cards, scorecards (baselines, vetoes, news view placeholder, promotion gate), daily digest list. Controls: Halt (one click, then shows Resume), Flatten (dialog requiring the typed word FLATTEN), settings table with inline edit for: autonomy level (only "approve every plan" selectable for now), approval expiry, silence-alarm minutes, digest time; read-only display of drawdown halt, sizing, venue, mode.
- **Tests:** flatten is impossible without the typed word; a settings edit calls `PUT` and shows the audited value.
- **Done when:** tests pass; halt and flatten work end to end in paper mode in the running stack; screenshots in the PR.

### 3.6 Mobile-width pass

- **Depends on:** 3.2 to 3.5. **Files:** CSS and layout changes only.
- **Done when:** at 390 px wide the status strip, Approvals (approve and deny) and Controls (halt) are fully usable without horizontal scrolling; other tabs are readable; screenshots at 390 px in the PR.

### 4.1 Heartbeats and watchdog

- **Goal:** silence is detected and shown.
- **Depends on:** 3.1.
- **Files:** `ops/heartbeat.py`, `ops/watchdog.py`, `ops/__main__.py` (service `watchdog`), tests.
- **Interfaces:** `heartbeat.beat(session, service, status="ok", detail="")` used by every service loop; `watchdog.check(session, clock) -> list[Alarm]` every minute raises an `events` row with `kind="alarm"` when: a service has no heartbeat for `silence_alarm_minutes` (default 5); today's daily decision rows are missing after 00:15 UTC; no intraday decision for 15 minutes; newest 5m candle older than 10 minutes; ledger balances differ from the adapter's balances. An alarm clears with a `kind="alarm_cleared"` event. Every 3 hours the watchdog writes a `kind="heartbeat_summary"` event: `"All services up. {n} price checks, {e} errors in the last 3 hours. Nothing to do."` (or what was done).
- **Tests:** each alarm condition with a frozen clock; an alarm is raised once, not every minute; it clears.
- **Done when:** tests pass; stopping the `collector` container turns the dashboard strip red within 5 minutes and names it (screenshot in the PR).

### 4.2 Daily digest

- **Depends on:** 4.1. **Files:** `ops/digest.py`, tests.
- **Interfaces:** `digest.build(session, day) -> Digest` at 00:30 UTC for the day just ended; Markdown with: account value and change, invested share, checks run and failed, each daily decision reason, plans proposed/approved/denied/expired, fills and fees, alarms, and one closing line `"No trades today because ..."` when there were none. Stored in `digests`; shown on Reports.
- **Tests:** a day with no trades still yields a digest with the closing line; a day with a fill lists it.
- **Done when:** tests pass; the PR shows a real digest from the running stack.

### 4.3 Deployment on the Ubuntu box

- **Depends on:** 4.1. **Files:** `docker-compose.prod.yml`, `scripts/backup.sh`, `scripts/restore.sh`, `docs/RUNBOOK.md`.
- **Interfaces:** all services `restart: unless-stopped`; dashboard bound to the LAN address only; nightly `pg_dump` to a second disk path with 14 days kept; runbook covers install, upgrade, restore, what each alarm means, and how to halt from the command line.
- **Done when:** on the owner's box: reboot brings everything back without manual steps; restore into a scratch database succeeds; the owner confirms the dashboard opens from their desktop. This spec needs the owner present.
- **Out of scope:** remote access from outside the LAN.

### 5.1 Paper trading start and weekly report

- **Depends on:** phase 4. **Files:** `ops/paper_report.py`, tests, `docs/PAPER.md`.
- **Interfaces:** seed the paper account with $5,000; weekly report compares paper equity with `backtest.engine.run` over the same days using stored candles (tracking gap in percentage points), lists uptime, alarms, and approval response times.
- **Done when:** the first weekly report exists and the gap calculation is covered by a test.

### 5.2 Bitstamp adapter

- **Goal:** a live venue adapter that cannot trade by default.
- **Depends on:** 2.4; the owner has a verified Bitstamp account and has supplied an API key and secret as secrets `TRADER_BITSTAMP_KEY` and `TRADER_BITSTAMP_SECRET`. The key must not have withdrawal permission; check which permissions Bitstamp offers and record the ones chosen in `docs/VENUE.md`.
- **Files:** `execution/bitstamp.py`, `execution/reconcile.py`, tests with recorded fixtures, `docs/VENUE.md`.
- **Interfaces (private endpoints named in Bitstamp's API reference, all `POST` under `https://www.bitstamp.net/api/v2`):** `buy/{market}/` and `sell/{market}/` (limit; fields `amount`, `price`, `moc_order`, `ioc_order`, `client_order_id`), `buy/market/{market}/` and `sell/market/{market}/`, `cancel_order/`, `cancel_all_orders/`, `replace_order/`, `open_orders/{market}/`, `order_status/`, `account_balances/`, `fees/trading/{market}/`. Authentication uses the headers `X-Auth`, `X-Auth-Signature`, `X-Auth-Nonce`, `X-Auth-Timestamp`, `X-Auth-Version`.
- **Steps:** before coding, read the official API reference at `https://www.bitstamp.net/api/` for the signature algorithm, each endpoint's fields and errors, how fills and fees are reported, and rate limits; record the facts used in `docs/VENUE.md`. Implement `ExecutionAdapter`. Entry limit orders set `moc_order=true`. Round quantities to `base_decimals` and prices to `counter_decimals` from `/markets/`, and refuse orders under `minimum_order_value`. `place` raises unless `mode == "live"` and `TRADER_LIVE_ENABLED=true`. `reconcile.run` compares ledger balances and open orders with the venue every minute and raises an alarm on any difference above $0.01. The API reference lists a test server at `https://sandbox.bitstamp.net`; ask the owner whether their account can get keys for it, and if so add an opt-in integration test that places and cancels a far-from-market order there.
- **Tests:** request signing against the reference's example; rounding and minimum size; `place` refuses in paper mode; reconcile detects a seeded mismatch; a rejected maker-or-cancel order is retried at the new best bid.
- **Done when:** tests pass; with the owner's key, `balances()` and the account's actual trading fee for both markets are read and shown in the PR (amounts redacted). No order is sent to the production server in this spec.
- **Out of scope:** withdrawals, deposits, derivatives, other venues.

### 5.3 Promotion gate review

- **Depends on:** 5.1 after 4 weeks. **Files:** `docs/reviews/paper-gate.md`.
- **Done when:** the review states, with numbers: tracking gap against backtest (pass under 1 point over the period), uptime (pass at 99%), alarms and their causes, approval response times, any accounting mismatch (pass at zero), and a go / no-go recommendation. The owner decides.

### 5.4 Go live with $1,000

- **Depends on:** 5.2, 5.3 with a go. Needs the owner present.
- **Steps:** owner confirms the API key has no withdrawal permission and funds $1,000; set mode to live; place and cancel one minimum-size limit order far from the price; let the next approved plan run; test halt, flatten and the application-run emergency stop at minimum size (temporarily set a plan's `stop_price` above the market).
- **Done when:** the first live round trip reconciles to the cent and the owner signs off in `docs/reviews/live-start.md`.

### 5.5 Sleeve E: sudden-drop bids (parked, do not run)

At 0.15% / 0.25% fees this idea returned -1.5% to 0.4% a year over 2022-2026, and Bitstamp's fees are higher, so it is not built. The five-minute cycle (1.5) keeps recording the quote it would have used, which is enough to revisit the idea if a venue with a maker fee under 0.05% becomes available.

### 6.1 to 6.4 Local LLM overlay

Written in detail when phase 3 is merged, because they depend on the model benchmark. Fixed constraints now: the LLM service has no database write access outside `llm_events` and `llm_views` tables it owns; article text is untrusted input and is never executed or used as instructions; output is validated against a JSON schema and discarded if invalid; nothing in `strategy/`, `risk/`, `plans/` or `execution/` imports from `llm/` (enforced by an import-linter test); the trading loop runs unchanged when the LLM service is down.

## C. Review fixes after 5.2

These specs close the findings of `docs/REVIEW-5.2.md`. Section A applies
to every one of them. Use the same template in `docs/KICKOFF.md`, with
"section C" in place of "section B". Branch `devin/<ID>-<slug>`, for
example `devin/R1-price-age-from-close`. One spec, one session, one pull
request. No spec here needs a real API key except R5, and R5 gives the
key to the owner's own machine, not to the session. No spec places an
order on any exchange.

Order: R1 first. R3, R4, R6 and R7 are independent (merge one at a
time). R8 after R1 and R4. R10 and R11 at any time. R2, then R5 and R9,
before spec 5.4.

### R1 Price age is measured from the candle's close

- **Goal:** a buy plan proposed at 00:05 UTC, or approved at any time
  while the collector is healthy, passes the staleness check. Today it
  is always vetoed at 00:05 (review finding 1).
- **Depends on:** nothing.
- **Files:** `src/trader/risk/engine.py`, `tests/risk/test_engine.py`,
  `tests/engine/test_daily_cycle.py`, `tests/plans/test_approval.py`.
- **Background:** the collector stores only closed 5-minute candles.
  `risk.state` sets `price_age_seconds[symbol] = now - newest open_time`.
  At 00:05:00 the newest stored candle opened at 23:55, so the age is
  600 s plus scheduler delay, and `check_plan` rejects when
  `age > limits.stale_candle_seconds` (600). `ops/watchdog.py`
  (`open_time + CANDLE_PERIOD < now - CANDLE_STALE`) and
  `engine/intraday_cycle.py` (`now - (open + CANDLE_PERIOD)`) already
  measure from the close.
- **Steps:**
  1. In `risk/engine.py` add `CANDLE_PERIOD = timedelta(minutes=5)`.
  2. In `state`, compute
     `price_age[symbol] = max(0.0, (now - (newest + CANDLE_PERIOD)).total_seconds())`.
     Update the docstring of `RiskState.price_age_seconds` to say
     "seconds since the newest stored 5m candle closed".
  3. Change nothing else in `risk/`. `Limits.stale_candle_seconds`
     stays 600. Do not change `check_plan`, `watchdog.py` or
     `intraday_cycle.py`.
- **Tests (frozen clock, Postgres test database):**
  1. Newest 5m candle opened 23:55:00; clock 00:05:00.050: age is
     300.05 and `check_plan` accepts a buy plan that passes the other
     checks.
  2. Same candle; clock 00:15:00.050 (two candles missing): age is
     900.05 and the buy is rejected with the existing "stale" reason.
  3. Same candle; clock 00:10:00.000: age is exactly 600 and the buy is
     accepted (the comparison is `>`).
  4. No 5m candle for the symbol: rejected (unchanged behaviour).
  5. Update the existing assertion that expects `120.0` in
     `tests/risk/test_engine.py` to the new definition.
  6. `tests/engine/test_daily_cycle.py`: with daily candles through
     yesterday, 5m candles whose newest opened at 23:55 yesterday, an
     all-cash portfolio and a target outside the trading band, running
     the cycle at 00:05:00.050 writes a `plan_proposed` decision and one
     `pending` plan. This test must fail on the code before step 2.
  7. `tests/plans/test_approval.py`: newest 5m candle opened at 11:55;
     approving a pending plan at 12:05:30 succeeds.
- **Done when:** tests pass, CI is green, and the PR states that test 6
  was seen failing before the change and passing after.
- **Out of scope:** changing any limit value, the collector, the
  watchdog, the schedule.
- **Note:** this is a correction to how one risk input is measured, made
  on the owner's instruction through this spec. It is the only change to
  `risk/` allowed here.

### R2 Bitstamp credentials: documented, passed to two services, never printed

- **Goal:** a key placed in `.env` on the box reaches exactly the
  services that need it (review finding 2).
- **Depends on:** nothing. Must be merged before R5 and 5.4.
- **Files:** `.env.example`, `docker-compose.prod.yml`,
  `src/trader/core/config.py`, `src/trader/execution/bitstamp.py`,
  `tests/core/test_config.py` (extend, or create if absent),
  `tests/execution/test_bitstamp.py`, `docs/VENUE.md`,
  `docs/RUNBOOK.md`, `docs/SETUP.md`.
- **Steps:**
  1. `.env.example`: add, commented out, with one line of explanation
     each: `# TRADER_BITSTAMP_KEY=`, `# TRADER_BITSTAMP_SECRET=`,
     `# TRADER_BITSTAMP_BASE_URL=https://www.bitstamp.net/api/v2`. State
     that paper mode needs none of them and that the key must not have
     withdrawal permission.
  2. `docker-compose.prod.yml`: add to the `environment` of `executor`
     and `watchdog` only:
     `TRADER_BITSTAMP_KEY: ${TRADER_BITSTAMP_KEY:-}`,
     `TRADER_BITSTAMP_SECRET: ${TRADER_BITSTAMP_SECRET:-}`,
     `TRADER_BITSTAMP_BASE_URL: ${TRADER_BITSTAMP_BASE_URL:-https://www.bitstamp.net/api/v2}`.
     Do not add them to `api`, `collector`, `engine`, `web` or `db`.
     (`executor` places orders; `watchdog` runs `execution.reconcile` in
     live mode.) Do not touch `docker-compose.yml`; it is fixed to paper.
  3. `core/config.py`: change `bitstamp_key` and `bitstamp_secret` to
     `pydantic.SecretStr` with default `SecretStr("")`.
  4. `execution/bitstamp.py`, `from_env`: read both with
     `.get_secret_value()`; keep the existing error when either is empty.
     No other behaviour change.
  5. Docs: in `docs/RUNBOOK.md` add a section "Bitstamp API key" (where
     the two lines go in `.env`, `chmod 600 .env`, restart command, how
     to confirm with R5's command once it exists, how to remove the key).
     Update `docs/SETUP.md` section 10 and `docs/VENUE.md` to match.
     Give PowerShell forms for commands run from the desktop.
- **Tests:** `Settings(bitstamp_key="k", bitstamp_secret="s")`: neither
  `repr(settings)` nor `str(settings)` nor `settings.model_dump_json()`
  contains `"s"` as the secret value (use a distinctive value such as
  `"sekret-value-123"`); `from_env` passes the real values to the
  adapter; `from_env` raises `BitstampError` when either is empty.
- **Done when:** tests pass; CI green; the PR shows the output of
  `TRADER_DASHBOARD_PASSWORD=x TRADER_LAN_ADDRESS=127.0.0.1 TRADER_BITSTAMP_KEY=dummy-key TRADER_BITSTAMP_SECRET=dummy-secret docker compose -f docker-compose.prod.yml config`
  (PowerShell: set the four with `$env:NAME = "value"` first) proving
  the two variables appear under `executor` and `watchdog` and under no
  other service.
- **Out of scope:** using a real key; enabling live mode; Docker
  secrets; any change to how orders are placed.

### R3 Migrations run by themselves

- **Goal:** `docker compose up -d` on an empty database gives a working
  stack with no manual step (review finding 3).
- **Depends on:** nothing.
- **Files:** `docker-compose.yml`, `docker-compose.prod.yml`,
  `README.md`, `docs/RUNBOOK.md`, `docs/SETUP.md`.
- **Steps:**
  1. In both Compose files add a service `migrate`: `build: .`,
     `environment: TRADER_DATABASE_URL` as the other services,
     `command: alembic upgrade head`, `depends_on: db: condition:
     service_healthy`, `restart: "no"`.
  2. In both files, for `api`, `collector`, `engine`, `executor` and
     `watchdog`, add to `depends_on`:
     `migrate: condition: service_completed_successfully`.
  3. In `docker-compose.yml` only, publish the API and dashboard on the
     loopback address: `"127.0.0.1:8000:8000"` and `"127.0.0.1:3000:80"`.
     Leave `docker-compose.prod.yml`'s ports as they are.
  4. Docs: remove the manual `alembic upgrade head` step from
     `README.md`, `docs/RUNBOOK.md` (install and upgrade) and
     `docs/SETUP.md` (sections 3.2, 7, 9 and the troubleshooting row);
     say that `migrate` exits with code 0 and that this is expected in
     `docker compose ps -a`.
- **Tests:** none in pytest. Verification is the "Done when" run.
- **Done when:** the PR shows, from a clean state
  (`docker compose down -v`), `docker compose up -d --build` followed
  two minutes later by `docker compose ps -a`: `migrate` exited (0), the
  other seven running, and `docker compose logs collector engine
  executor watchdog` containing no `does not exist`. A second
  `docker compose up -d` changes nothing. `curl
  http://localhost:8000/health` (PowerShell: `Invoke-RestMethod
  http://localhost:8000/health`) returns `{"status":"ok","mode":"paper"}`.
- **Out of scope:** changing `/health`; changing the Dockerfile;
  restart policies.

### R4 The daily cycle retries until the day has a decision

- **Goal:** a day whose first daily run was blocked (missing candle on a
  first start, short data outage at 00:05) gets its decision minutes
  later, not the next day (review finding 4).
- **Depends on:** nothing.
- **Files:** `src/trader/engine/daily_cycle.py`,
  `src/trader/engine/__main__.py`, `tests/engine/test_daily_cycle.py`,
  `tests/engine/test_engine_service.py` (create if no engine service
  test exists; otherwise extend the existing one).
- **Interfaces:**
  - `daily_cycle.MAX_ATTEMPTS = 12`.
  - `daily_cycle.needs_retry(session, clock) -> bool`: `True` only when
    all hold: `clock.now()` is at or after 00:05 UTC today; today has at least
    one daily decision row; every one of today's daily rows has action
    `blocked` or `error`; the number of distinct `ran_at` values among
    today's daily rows is below `MAX_ATTEMPTS`; trading is not halted
    (`risk.engine.state(session, clock).halted` is false; pass the clock
    in, so the signature is `needs_retry(session, clock)`).
  - `Engine.retry_daily()`: opens a session, and if `needs_retry` is
    true calls `run_daily()`.
- **Steps:** add a scheduler job in `Engine.serve`: `self.retry_daily`,
  `"cron"`, `minute="*/5"`, `second=40`, `coalesce=True`,
  `max_instances=1`. Do not change `daily_cycle.run`, the 00:05 job or
  the start-up call.
- **Tests (frozen clock):** `needs_retry` is false with no rows, false
  before 00:05, true with only `blocked` rows, false once any row is
  `none` or `plan_proposed`, false after 12 attempts, false while
  halted. Service test: first
  `run_daily` with no candles writes a `blocked` row; candles are then
  stored; `retry_daily` five minutes later writes normal decisions for
  both symbols; a further `retry_daily` writes nothing.
- **Done when:** tests pass; CI green.
- **Out of scope:** retrying a day in which one asset decided and the
  other was vetoed; changing reasons or the decision schema.

### R5 Read-only venue check, run by the owner (finishes spec 5.2)

- **Goal:** read the account's balances and actual trading fee with the
  owner's key, without the key leaving the owner's machine and without
  any order endpoint being called (review finding 5).
- **Depends on:** R2. **Needs the owner** for the last step.
- **Files:** `src/trader/execution/venue_check.py`,
  `tests/execution/test_venue_check.py`, `docs/VENUE.md`,
  `docs/SETUP.md`.
- **Interfaces:** `python -m trader.execution.venue_check [--show-amounts]`.
  It builds `BitstampAdapter.from_env` and calls only `balances()`,
  `trading_fee("BTC-USD")`, `trading_fee("ETH-USD")`,
  `symbol_rules(...)` for both symbols and `venue_open_orders()`. Output,
  one line each: base URL host; for each of USD, BTC, ETH "present" or
  "zero" (the amount only with `--show-amounts`); maker and taker fee in
  percent for each market, followed by "matches the assumed 0.30% /
  0.40%" or "DIFFERS from the assumed 0.30% / 0.40%" (compare with
  `backtest.costs.BITSTAMP`); tick, lot and minimum order per market;
  number of open orders. Exit code 0 on success; 1 with one plain
  sentence when the key is missing, rejected, or lacks a permission. It
  never prints the key, the secret, a signature or a nonce.
- **Steps:** implement and test against recorded fixtures the same way
  `tests/execution/test_bitstamp.py` does. The session does **not** ask
  for or receive the key. When CI is green, ask the owner to run, on the
  box:
  `docker compose -f docker-compose.prod.yml run --rm executor python -m trader.execution.venue_check`
  (from the Windows desktop: the same command through `ssh trader-box
  "cd /opt/trader && ..."`) and to paste the output into the PR. Record
  the fee read, and the date, in `docs/VENUE.md`.
- **Tests:** the fake transport records every request; assert that no
  path containing `buy`, `sell`, `cancel` or `replace` is requested;
  amounts are absent without `--show-amounts`; a 403 response gives exit
  code 1 and the sentence; the fee comparison prints "DIFFERS" for a
  fixture with other rates.
- **Done when:** tests pass; CI green; the owner's pasted output is in
  the PR; `docs/VENUE.md` states the account's fee. If the fee differs
  from 0.30% / 0.40%, stop and tell the owner; do not change any cost
  constant in this spec.
- **Out of scope:** placing or cancelling orders; the sandbox test;
  changing fees in code.

### R6 Tests refuse to run against a working database

- **Goal:** `pytest` cannot erase the paper account (review finding 6).
- **Depends on:** nothing.
- **Files:** `tests/conftest.py` (new), `tests/core/test_db_guard.py`
  (new), `.github/workflows/ci.yml`, `README.md`, `docs/SETUP.md`.
- **Interfaces:** in `tests/conftest.py`,
  `check_test_database_url(url: str | None) -> None`: returns when `url`
  is empty; otherwise parses it with `sqlalchemy.engine.make_url` and
  raises `ValueError("TEST_DATABASE_URL must name a database ending in
  _test; got '<name>'")` unless the database name ends with `_test`.
  `pytest_configure(config)` calls it with
  `os.environ.get("TEST_DATABASE_URL")` and on `ValueError` calls
  `pytest.exit(str(error), returncode=2)`.
- **Steps:** in `ci.yml` rename the service database to `trader_test`
  and update `TEST_DATABASE_URL` to match. Update the README's
  Development section and `docs/SETUP.md` section 8 to create and use
  `trader_test` (both shells).
- **Tests:** the function accepts `.../trader_test`, accepts `None` and
  `""`, rejects `.../trader` and `.../postgres`.
- **Done when:** tests pass; CI green on the renamed database; the PR
  shows `pytest` exiting with code 2 and the message before any test
  runs when `TEST_DATABASE_URL` is
  `postgresql+psycopg://trader:trader@localhost:5432/trader`
  (Unix: `export TEST_DATABASE_URL=<url>` then `pytest`; PowerShell:
  `$env:TEST_DATABASE_URL = "<url>"` then `pytest`).
- **Out of scope:** moving the per-file fixtures into `conftest.py`.

### R7 Controls that do nothing become read-only

- **Goal:** the Controls screen and the settings API offer only settings
  that take effect (review finding 7).
- **Depends on:** nothing.
- **Files:** `src/trader/api/routes/settings.py`, `tests/api/test_api.py`,
  `web/src/pages/Controls.tsx`, `web/src/__tests__/Controls.test.tsx`,
  `docs/API.md`.
- **Steps:**
  1. `routes/settings.py`: remove `approval_expiry_hours`, `mode` and
     `autonomy` from `EDITABLE` and delete their validators. `GET
     /api/settings` must still return the three keys, with fixed values:
     `approval_expiry_hours` = 6.0 (import
     `plans.builder.APPROVAL_EXPIRY` and convert), `mode` =
     `get_settings().mode`, `autonomy` = `"approve_every_plan"`. A stored
     row for one of these keys is ignored. `PUT` on them returns the
     existing 404 "not editable".
  2. `Controls.tsx`: remove "Autonomy level" and "Approval expiry" from
     the editable list and show them in the read-only list with the
     texts "Approve every plan (other levels are not built yet)" and
     "6 hours (fixed)". "Silence alarm" and "Daily digest" stay editable.
  3. `docs/API.md`: update the settings section.
- **Tests:** API: `PUT /api/settings/approval_expiry_hours`, `/mode` and
  `/autonomy` return 404; `GET` returns the fixed values even when a
  `settings` row with another value exists. Web: the two rows have no
  edit button; the two remaining rows still save.
- **Done when:** tests pass (`pytest`, and `npm test` in `web/`); CI
  green; a screenshot of Controls is in the PR.
- **Out of scope:** making expiry configurable (section A fixes it at 6
  hours); building other autonomy levels; `t_bill_rate`.

### R8 Integration test of the paper pipeline with production timing

- **Goal:** one test that would have caught finding 1: candles stored
  the way the collector stores them, then daily cycle, approval,
  executor, fill and stop in sequence (review finding 8).
- **Depends on:** R1, R4.
- **Files:** `tests/integration/__init__.py`,
  `tests/integration/test_paper_pipeline.py`. No change under `src/`.
  If the test exposes a defect, stop and report it to the owner; do not
  fix it in this spec.
- **Steps (one test function per numbered block, shared helpers; frozen
  clock; Postgres test database; no network):**
  1. Fixture `market(day)`: daily candles for both symbols from
     `research/fixtures/` up to and including the day before `day`;
     5-minute candles for the last two hours before `day` 00:00, the
     newest opening at 23:55, all `source="bitstamp"`, flat at the last
     daily close. Choose `day` from the fixture data so that the BTC
     target weight is above zero (find it in the test by calling the
     strategy functions; do not hard-code a weight). Seed the paper
     account with $5,000 through `ops.paper_report.ensure_seeded`.
  2. Proposal: at `day` 00:05:00.050 call `Engine.run_daily()`. Expect a
     daily decision `plan_proposed` for BTC-USD and one `pending` plan
     with `expires_at` six hours later.
  3. Approval and placement: at 00:06:00 approve at `0.5`. Run
     `Executor.tick()` at 00:06:30. Expect one open non-shadow limit buy
     at the price in the plan's `entry_orders`, with half the plan's
     original quantity.
  4. Maker fill: store the 00:05 candle (low above the limit: no fill
     after a tick at 00:10:30), then the 00:10 candle with a low 1%
     under the order's current limit price (read it from the `orders`
     row, because the executor re-prices every 5 minutes); tick at
     00:15:30. Expect one fill with
     `liquidity="maker"`, fee equal to 0.30% of notional, BTC balance
     equal to the order quantity, USD reduced by notional plus fee, a
     new equity snapshot, plan `completed`.
  5. Market conversion: repeat blocks 2 and 3 in a fresh database, store
     candles that never trade through for 65 minutes, tick after each.
     Expect the limit cancelled and a taker fill at a candle open plus
     slippage, fee 0.40%.
  6. Emergency stop: after block 4, store a candle whose close is under
     the plan's `stop_price`; tick. Expect a market sell of the whole
     position at the taker fee and BTC balance zero.
  7. Expiry: repeat block 2, advance six hours and one minute, run the
     approval sweep. Expect the plan `expired` and shadow orders written.
- **Done when:** the tests pass in CI; the PR states that block 2 was
  run against the commit before R1 and failed there.
- **Out of scope:** the live adapter; the web UI; fixing defects found.

### R9 Live emergency stop uses the public ticker

- **Goal:** in live mode the stop compares against a price seconds old,
  not a stored candle up to six minutes old (review finding 9).
- **Depends on:** nothing. Must be merged before spec 5.4.
- **Files:** `src/trader/execution/base.py`,
  `src/trader/execution/paper.py`, `src/trader/execution/bitstamp.py`,
  `src/trader/execution/__main__.py`, `tests/execution/test_paper.py`,
  `tests/execution/test_bitstamp.py` (the executor's own tests are in
  `tests/execution/test_paper.py`).
- **Interfaces:** add to `ExecutionAdapter`:
  `last_price(self, symbol: str) -> Decimal | None`, "a current price for
  stop checks, or None to use the stored candle". `PaperAdapter` returns
  `None`. `BitstampAdapter` returns `self.ticker(symbol)["bid"]`, and
  `None` if that call raises (log a warning; do not raise).
- **Steps:** in `Executor._emergency_stop`, for each held symbol use
  `adapter.last_price(symbol)`; when it is `None` fall back to the
  stored 5-minute close as today. The comparison `price <= stop_price`,
  the cancel-then-sell sequence and everything else stay as they are.
  The ticker is a public endpoint and needs no key.
- **Tests:** a fake adapter whose `last_price` is under the stop while
  the stored close is above it: the stop fires. `last_price` above the
  stop while the stored close is under it: the stop does not fire.
  `last_price` returns `None`: behaviour equals today's (existing tests
  unchanged). `BitstampAdapter.last_price` returns the bid from a
  recorded ticker fixture and `None` on a transport error.
- **Done when:** tests pass; CI green.
- **Out of scope:** paper mode behaviour; websocket prices; changing
  the stop distance or the 30-second loop.

### R10 Open orders are visible on Positions

- **Goal:** between approval and first fill the owner can see the
  resting order (review finding 10).
- **Depends on:** nothing.
- **Files:** `src/trader/api/routes/positions.py`,
  `src/trader/api/schemas.py`, `tests/api/test_api.py`,
  `web/src/api/client.ts`, `web/src/pages/Positions.tsx`,
  `web/src/__tests__/Positions.test.tsx`, `web/src/__tests__/fixtures.ts`,
  `docs/API.md`.
- **Interfaces:** `GET /api/orders/open` → list of `OpenOrderOut`
  (`OrderOut` fields plus `symbol: str` and `plan_id: int | None`):
  every non-shadow order of the current mode whose status is in
  `RESTING`, oldest first. Authentication as the other routes.
- **Steps:** on Positions add a card "Open orders" above "Funding
  watch" with columns Symbol, Side, Type, Price, Quantity, Placed, Plan.
  Empty state: "No open orders." It is shown whether or not there are
  positions. Refresh with the same interval as the positions query.
- **Tests:** API: an open buy for an asset with no holding is returned;
  a shadow order and a filled order are not. Web: the card renders the
  fixture row and the empty state.
- **Done when:** tests pass (`pytest`, `npm test`); CI green; a
  screenshot is in the PR.
- **Out of scope:** cancelling orders from the dashboard.

### R11 Weekly reports on the Reports screen

- **Goal:** the weekly paper report is readable where the owner looks
  for reports, and no report is written for a week before the account
  existed (review finding 11).
- **Depends on:** nothing.
- **Files:** `src/trader/ops/paper_report.py`,
  `tests/ops/test_paper_report.py`, `src/trader/api/routes/reports.py`,
  `src/trader/api/schemas.py`, `tests/api/test_api.py`,
  `web/src/api/client.ts`, `web/src/pages/Reports.tsx`,
  `web/src/__tests__/Reports.test.tsx`, `web/src/__tests__/fixtures.ts`,
  `docs/API.md`, `docs/PAPER.md`.
- **Interfaces:** `GET /api/reports/weekly?limit=12` → list, newest
  first, of `{week_start, week_end, message, body_md,
  tracking_gap_points, paper_return, backtest_return, mean_uptime}`
  read from `events` rows with `kind="weekly_report"` (fields come from
  the event's `details`).
- **Steps:**
  1. `paper_report.due_week`: return `None` when the window's end is at
     or before the time of the first paper `deposit` event (no account
     existed during that week), and when no paper deposit exists.
  2. Reports screen: a card "Weekly paper reports" above "Daily
     digests", each entry a heading "Week of <start> to <end>" and the
     `body_md` in the same `<pre>` style as digests. Empty state: "No
     weekly report yet; the first is written on the first Monday after
     the account was created."
- **Tests:** `due_week` returns `None` for a week that ended before the
  deposit and the window for the first week that contains it; the route
  returns stored reports newest first and honours `limit`; the web card
  renders a fixture report and the empty state.
- **Done when:** tests pass (`pytest`, `npm test`); CI green; a
  screenshot is in the PR.
- **Out of scope:** changing what the report computes; the `report`
  command (it must still write any window asked for).

## D. Enhancements after the first paper day

Requested by the owner on 2026-11-23, after the first 12 hours of paper
trading showed no trade (all nine rules were off for both assets). The
tests behind D2 are in `docs/studies/more-trades-study.md`. Section A
applies. Use the template in `docs/KICKOFF.md` with "section D" in place
of "section B". Branch `devin/<ID>-<slug>`. One spec, one session, one
pull request. Neither spec needs an API key or places an order on an
exchange. Neither spec changes a rule, a risk limit, the halt or the
approval requirement.

Order: D1 and D2 are independent; merge one at a time.

### D1 Rules screen: what the system is checking and why it is not trading

- **Goal:** one read-only screen shows, per asset, each of the nine
  rules as on or off with the price level it was compared with, the
  resulting target weight, the five-minute check, and the fixed
  settings. The owner can see why nothing is trading without reading
  logs.
- **Depends on:** nothing.
- **Files:** `src/trader/strategy/rules.py`,
  `tests/strategy/test_rules.py`, `src/trader/engine/daily_cycle.py`,
  `tests/engine/test_daily_cycle.py`, `src/trader/api/routes/rules.py`
  (new), `src/trader/api/app.py`, `src/trader/api/schemas.py`,
  `tests/api/test_api.py`, `web/src/api/client.ts`,
  `web/src/pages/Rules.tsx` (new), `web/src/App.tsx`,
  `web/src/__tests__/Rules.test.tsx` (new),
  `web/src/__tests__/fixtures.ts`, `docs/API.md`, `docs/SETUP.md`.
- **Interfaces:**
  1. `rules.levels(close: pd.Series) -> dict[str, float]`: for the last
     row of `close`, the price each rule compared the close with. Keys
     are the nine names of `all_rules`. Values:
     - `sma<n>`: the n-day mean.
     - `momentum<n>`: the close n days earlier.
     - `breakout<n>`: when the rule's last value is 1.0, the
       `n // 2`-day low of closes (a close at or under it turns the rule
       off); otherwise the n-day high of closes (a close at or above it
       turns the rule on).
     - `nan` where the rule itself is undefined.
     Do not change `sma`, `momentum`, `breakout` or `all_rules`.
  2. Daily decision rows that carry `details["rules"]` also carry
     `details["last_close"]` (float, the daily close the rules used) and
     `details["levels"]` (`{rule name: float | null}`, from
     `rules.levels`, NaN stored as `null` with the existing `_finite`).
     Nothing else in the daily cycle changes.
  3. `GET /api/rules` → `RulesOut`, authentication as the other routes:

     ```
     RulesOut
       next_daily_decision_at: datetime        # as in /api/status
       latest_daily: DailyRunOut | None        # newest daily row of any kind
       assets: list[AssetRulesOut]             # one per symbol in SYMBOLS, in that order
       intraday: IntradayRulesOut | None       # from the newest intraday row
       parameters: list[ParameterOut]
     DailyRunOut        ran_at: datetime, action: str, reason: str
     AssetRulesOut
       symbol: str
       decided_at: datetime | None             # None when no row with rules exists
       action: str | None
       reason: str | None
       last_close: float | None
       rules_positive: int | None              # count of rules equal to 1.0
       rules: list[RuleOut]                    # always nine, in all_rules order
       annual_vol: float | None
       raw_weight: float | None
       target_weight: Decimal | None
       current_weight: Decimal | None
     RuleOut
       name: str                               # "sma50"
       label: str                              # see Steps 2
       positive: bool | None                   # None when the stored value is null
       level: float | None                     # None for rows written before this spec
       distance: float | None                  # last_close / level - 1, None if either is missing
     IntradayRulesOut
       ran_at: datetime, action: str, reason: str
       assets: list[IntradayAssetOut]
     IntradayAssetOut
       symbol: str
       last_price: float | None
       nearest_stop_distance: float | None
       drop_bid: float | None                  # details.per_asset[symbol].dip_quote.bid
       drop_bid_distance: float | None         # 1 - drop_bid / last_price
     ParameterOut       name: str, value: str
     ```

     For each symbol the asset block is built from the newest
     `decisions` row with `cycle="daily"`, that `symbol`, and a
     `details` object containing the key `rules`. A newer blocked or
     error row does not replace it; it appears in `latest_daily`.
- **Steps:**
  1. Route `rules.py`, registered in `app.py` like the other routers.
     `next_daily_decision_at` reuses `status._next_daily_decision`.
  2. Labels: `sma<n>` → "Price above its <n>-day average";
     `momentum<n>` → "Price above its level <n> days ago";
     `breakout<n>` → "<n>-day breakout (on at a <n>-day high, off at a
     <n // 2>-day low)".
  3. `parameters`, in this order, with values read from the code
     constants so the screen cannot drift from the code:
     Assets (`SYMBOLS` joined with ", "); Daily decision time
     ("00:05 UTC"); Five-minute check ("every 5 minutes");
     Volatility target (`daily_cycle.VOL_TARGET` as a percentage);
     Trading band (`daily_cycle.BAND` as a percentage, followed by
     " of the larger of target and holding"); Drawdown halt and Largest
     loss per plan (from `Limits.load`); Approval expiry
     (`builder.APPROVAL_EXPIRY` in hours); Entry order ("maker-only
     limit at the best bid, re-priced every <REPRICE_SECONDS / 60>
     minutes, market order after <MARKET_AFTER_SECONDS / 60> minutes");
     Emergency stop ("<STOP_SWINGS> <STOP_DAYS>-day swings below the
     entry price, checked every 30 seconds"); Sudden-drop bids ("Parked.
     Shown as an observation only; no order is placed").
  4. Web: add the tab "Rules" after "Approvals" in `TABS`. The page has
     three cards and refreshes every 60 seconds.
     - "Trend rules (decided daily at 00:05 UTC)". First line: the
       time until `next_daily_decision_at`. If `latest_daily.action` is
       `blocked` or `error`, a warning line with its `reason`. Then one
       block per asset: a heading "<symbol>: <rules_positive> of 9 rules
       on", the asset's `reason`, "Decided <decided_at>", a line
       "Volatility <annual_vol as %> a year. Target <target_weight as
       %>, holding <current_weight as %>.", and a table with columns
       Rule (`label`), Status ("On", "Off", "No data"), Compared with
       (`level`), Last close against it (`distance` as a signed
       percentage). A missing `level` or `distance` shows "—". If any
       level is missing, one muted line: "Levels appear after the next
       daily decision." Empty state for an asset with no `decided_at`:
       "No daily decision recorded yet."
     - "Five-minute check": the intraday `reason` and its time, then
       per asset: price, nearest stop distance (or "none"), and
       "Sudden-drop level <drop_bid> (<drop_bid_distance> under the
       price)". Under the card title, in muted text: "Observation only.
       The sudden-drop sleeve is parked and places no orders."
     - "Fixed settings": the `parameters` rows as a two-column table.
     Prices are formatted by a helper `formatPrice(x)`: 1,000 and
     above with no decimals and thousands separators; 1 to 999.99 with
     2 decimals; under 1 with 4 decimals.
  5. The page contains no form control: no `<input>`, `<select>`,
     `<textarea>` or `<button>`.
  6. `docs/API.md`: document the route. `docs/SETUP.md`: add the Rules
     screen to the list of screens, one sentence.
- **Tests:**
  1. `levels` on a hand-built series: the `sma` value equals the mean of
     the last n closes; the `momentum` value equals the close n rows
     back; `breakout` returns the n-day high while the rule is 0.0 and
     the `n // 2`-day low while it is 1.0; a series shorter than n gives
     NaN for that rule.
  2. Daily cycle: a decision with rules has `last_close` and nine
     `levels`; a blocked decision has neither.
  3. API: with two daily rows per symbol the newer one is used; a newer
     blocked row is returned in `latest_daily` and does not blank the
     assets; a row written without `levels` gives `level: null` and
     `distance: null` for every rule; with no rows `assets` holds one
     entry per symbol with `decided_at: null`; the route returns 401
     without a login.
  4. Web: the fixture renders nine rows per asset with "On" and "Off",
     the "Observation only" line and the settings table; the empty
     state renders; the rendered page has no `input`, `select`,
     `textarea` or `button` element.
- **Done when:** tests pass (`pytest`, `npm test`); CI green; a
  screenshot of the Rules screen from a running stack is in the PR.
- **Out of scope:** editing any rule or setting; showing history of
  rule values; predicting tomorrow's flip price; any change to how a
  decision is made.

### D2 Asset list from configuration, with SOL and XRP available for paper trading

- **Goal:** the owner can paper trade the same nine rules on BTC, ETH,
  SOL and XRP by setting one variable. The default stays BTC and ETH,
  and live mode refuses any asset other than BTC and ETH.
- **Why these two:** on 2026-11-23 SOL/USD and XRP/USD each traded
  about $14 million a day on Bitstamp with over $250,000 resting within
  0.1% of the price. SHIB/USD and BNB/USD traded under $0.2 million a
  day there and are not usable. In the backtest (Binance prices,
  Bitstamp costs, 2022 to 2026) the four-asset version returned 9.6% a
  year against 11.4% for BTC and ETH, with a similar worst loss (-23%
  against -24%) and trades on about 180 days a year against 113. It is
  a paper test of a wider base, not an expected improvement.
- **Depends on:** nothing.
- **Files:** `src/trader/core/config.py`, `tests/core/test_config.py`,
  `src/trader/data/sources/base.py`,
  `src/trader/data/sources/bitstamp.py`,
  `src/trader/data/sources/coinbase.py`, `tests/data/test_sources.py`,
  `src/trader/data/collector.py`, `tests/data/test_collector.py`,
  `src/trader/engine/daily_cycle.py`, `tests/engine/test_daily_cycle.py`,
  `src/trader/engine/intraday_cycle.py`,
  `tests/engine/test_intraday_cycle.py`,
  `src/trader/api/routes/reports.py`, `tests/api/test_api.py`,
  `web/src/pages/Controls.tsx`, `web/src/__tests__/Controls.test.tsx`,
  `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`,
  `docs/SETUP.md`, `docs/PAPER.md`, `docs/SPECS.md` (section A rows
  named in step 9 only), `docs/DECISIONS.md`.
- **Interfaces:**
  1. `Settings.symbols: str = "BTC-USD,ETH-USD"` (variable
     `TRADER_SYMBOLS`) and `Settings.symbol_list -> tuple[str, ...]`:
     split on commas, strip spaces, upper-case; an empty list or a
     repeated symbol raises `ValueError`. A model validator raises
     `ValueError("live mode supports only BTC-USD and ETH-USD")` when
     `mode == "live"` and `symbol_list` holds anything else.
  2. In `data/sources/base.py`:

     ```python
     @dataclass(frozen=True)
     class Asset:
         symbol: str
         bitstamp_market: str
         coinbase_product: str
         tick: Decimal          # price step
         lot: Decimal           # quantity step
         daily_start: datetime  # first daily candle to backfill (UTC)

     ASSETS: dict[str, Asset]   # the four rows below
     SYMBOLS: tuple[str, ...]   # get_settings().symbol_list, each checked against ASSETS
     ```

     | symbol | bitstamp_market | coinbase_product | tick | lot | daily_start |
     |---|---|---|---|---|---|
     | BTC-USD | btcusd | BTC-USD | 0.01 | 0.00000001 | 2019-01-01 |
     | ETH-USD | ethusd | ETH-USD | 0.01 | 0.00000001 | 2019-01-01 |
     | SOL-USD | solusd | SOL-USD | 0.001 | 0.00001 | 2022-08-18 |
     | XRP-USD | xrpusd | XRP-USD | 0.00001 | 0.00000001 | 2019-01-01 |

     BTC and ETH keep exactly today's values, so no existing test
     changes. SOL and XRP steps are Bitstamp's `counter_decimals` and
     `base_decimals` from `GET /api/v2/markets/` on 2026-11-23. A symbol
     in `TRADER_SYMBOLS` that is not in `ASSETS` raises `ValueError`
     naming it when `base.py` is imported, so every service refuses to
     start.
- **Steps:**
  1. `bitstamp.MARKETS` and `coinbase.PRODUCTS` are built from `ASSETS`
     (all four rows, whatever `SYMBOLS` is). Their names and types stay.
  2. Collector: the daily backfill and the daily gap search start at
     `ASSETS[symbol].daily_start` in place of `DAILY_BACKFILL_START`.
     Reason: Bitstamp returns an empty list for a 1,000-day window with
     no candles in it, and fewer than 1,000 rows for a window that
     starts before the first candle; `BitstampSource.fetch` stops on
     both, so SOL would be left with no history. Do not change
     `BitstampSource.fetch`.
  3. Daily cycle: `tick` and `lot` passed to `builder.build` come from
     `ASSETS[symbol]`. Remove `VENUE_TICK` and `VENUE_LOT`. Sizing is
     already `signal / number of assets`; do not change `sizing.py`.
  4. Intraday reason text. Prices use a helper `_fmt_price(p)`: 1,000
     and above `f"{p:,.0f}"`; 1 to under 1,000 `f"{p:,.2f}"`; under 1
     `f"{p:.4f}"`. The list of sudden-drop distances is joined with
     ", " and " and " before the last item. For BTC above 1,000 and ETH
     above 1,000 the text is byte-for-byte what it is today.
  5. `reports.py`, equity baselines: "Buy and hold BTC" and "Buy and
     hold BTC+ETH" always use BTC-USD and ETH-USD, whatever `SYMBOLS`
     is (today the second one averages over `SYMBOLS`). The comparison
     with the backtest keeps using `SYMBOLS`.
  6. Controls screen: the "Sizing" row text becomes "Volatility target
     40%, assets set by TRADER_SYMBOLS (default BTC and ETH), long
     only, no leverage".
  7. Both Compose files: add
     `TRADER_SYMBOLS: ${TRADER_SYMBOLS:-BTC-USD,ETH-USD}` to `api`,
     `collector`, `engine`, `executor` and `watchdog`. `.env.example`:
     add the variable, commented out, with one line of explanation.
  8. `docs/SETUP.md`: a section "Paper trading more assets" with the
     `.env` line `TRADER_SYMBOLS=BTC-USD,ETH-USD,SOL-USD,XRP-USD`, the
     restart command for Linux/macOS and PowerShell
     (`docker compose -f docker-compose.prod.yml up -d` in both), and
     these facts: change the list only while the account holds no
     position; history for a new asset is downloaded at the next
     collector start; a new asset gets its first daily decision at the
     next 00:05 UTC; each asset's share of the account is at most one
     divided by the number of assets (25% with four); live mode starts
     only with BTC and ETH. `docs/PAPER.md`: one paragraph: changing
     the asset list restarts the four-week count of spec 5.3.
  9. `docs/SPECS.md` section A: "Symbols are..." becomes "Symbols are
     the entries of `TRADER_SYMBOLS` (default `BTC-USD`, `ETH-USD`;
     `SOL-USD` and `XRP-USD` are allowed in paper mode); adapters
     translate to venue names."; the Assets row becomes "BTC-USD,
     ETH-USD by default; SOL-USD, XRP-USD in paper mode only"; in the
     Sizing row "signal / 2" becomes "signal / number of assets". In
     `docs/DECISIONS.md` leave the Open row "Asset list" as it is: the
     owner decides after the paper results.
- **Tests:**
  1. Config: default gives `("BTC-USD", "ETH-USD")`; `" btc-usd , sol-usd "`
     gives `("BTC-USD", "SOL-USD")`; empty and repeated values raise;
     `mode="live"` with SOL raises; `mode="live"` with the default does
     not.
  2. Sources: `MARKETS["SOL-USD"] == "solusd"`,
     `PRODUCTS["XRP-USD"] == "XRP-USD"`; an unknown symbol in
     `TRADER_SYMBOLS` raises on import of `base` (use
     `importlib.reload` with the variable set and
     `get_settings.cache_clear()` before it, then restore both).
     Wherever a test below says "`SYMBOLS` patched", patch the name in
     the module under test (for example
     `monkeypatch.setattr(daily_cycle, "SYMBOLS", ...)`): each module
     imports the tuple by name.
  3. Collector: with `SYMBOLS` patched to include SOL-USD, the first
     daily request for SOL starts at 2022-08-18 and the first for BTC at
     2019-01-01.
  4. Daily cycle: with `SYMBOLS` patched to four assets, all rules on
     and volatility under the target, each target weight is 0.25, and a
     SOL plan's limit price has 3 decimals and its quantity 5.
  5. Intraday: `_fmt_price` for 61416.2, 131.416 and 0.52341 gives
     "61,416", "131.42" and "0.5234"; the existing reason-text tests
     pass unchanged; with four assets the drop-bid sentence reads
     "... under BTC, ... under ETH, ... under SOL and ... under XRP.".
  6. Reports: with `SYMBOLS` patched to four assets the BTC+ETH
     baseline equals the two-asset value.
  7. Web: Controls shows the new Sizing text.
- **Done when:** tests pass (`pytest`, `npm test`); CI green. With
  `TRADER_SYMBOLS=BTC-USD,ETH-USD,SOL-USD,XRP-USD` in `.env`,
  `docker compose up -d --build` reaches seven running services, and
  after the backfill
  `docker compose exec -T db psql -U trader -c "select symbol, interval, count(*), min(open_time)::date from candles group by 1,2 order by 1,2"`
  shows more than 1,500 daily candles for SOL-USD starting 2022-08-18
  and more than 2,800 for XRP-USD; the Overview status line names four
  prices; with `TRADER_MODE=live` added, the services exit with the
  live-mode message (paste the log line). Then remove both lines again.
  Paste each output in the PR.
- **Out of scope:** any asset beyond these four; live trading of SOL or
  XRP; per-asset caps or weights other than equal shares; changing a
  rule, the volatility target, the band or any limit; the funding watch
  on Positions; the committed fixtures in `research/`.
