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
  - `engine.halt(session, actor, reason)`, `engine.resume(session, actor)` (resume resets the high-water mark to current equity and writes `audit_log`), `engine.flatten_requested(session, actor)`.
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
- **Depends on:** 0.3.
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
