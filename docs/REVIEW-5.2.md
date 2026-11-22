# Review after spec 5.2

Reviewed on 2026-11-22 at `main` commit `2079cd9` (specs 0.1 to 5.2
merged, 0.2 optional and not run). Method: read the code against
`docs/SPECS.md`, ran the repository checks, started the Compose stack in
paper mode from an empty database and exercised it through the API and
the dashboard for about twenty minutes. No Bitstamp key was used and no order
was sent to any exchange.

## Verdict

The application matches the specs in structure and most behaviour, and
paper trading works end to end on Bitstamp's public prices **once a plan
exists and is approved**. It does **not** yet do so by itself: one defect
(finding 1) makes the risk check veto every buy plan the daily cycle
proposes at its scheduled time, so an unattended system never asks for an
approval. Until spec R1 is merged the paper period of spec 5.1 produces
no entries, and the four weeks of spec 5.3 should be counted from the
day R1 is deployed.

Live trading is not ready, as expected at this stage (specs 5.3 and 5.4
are open); findings 2, 5 and 9 must be closed before 5.4.

## What was checked, and how

"Run" means observed in the running stack; "tests" means the repository's
test suite; "code" means read only.

| Area | Result | Basis |
|---|---|---|
| `ruff check`, `ruff format --check`, `mypy src/trader` | Pass (114 files, 63 source files) | run |
| `pytest` with a Postgres test database | 276 passed, 1 skipped (the opt-in Bitstamp sandbox test) | run |
| `docker compose build` | Pass | run |
| `/health` | `{"status":"ok","mode":"paper"}` | run |
| Login, cookie session, 401 without it | Works | run |
| Collector on Bitstamp public candles, no key | 2,882 daily and 8,640 five-minute candles per symbol, source `bitstamp` | run |
| Daily cycle: nine rules, sizing, decision rows with reasons | Works; matches the fixtures of spec 1.2 and 1.3 | run, tests |
| Five-minute cycle and its "nothing to do" reasons | Works, one row every 5 minutes | run |
| Paper account seed of $5,000 | Works | run |
| Approval at half size | Works; quantities, fees and worst case scaled | run |
| Executor places the entry limit order | Works, within 30 seconds of approval | run |
| Paper fill from stored candles | Maker fill when a later candle traded through; fee 0.30% | run |
| Ledger, balances, equity snapshot, plan completion, events | Correct to the cent for one buy and one sell | run |
| Positions screen: holding, stop price | Correct | run |
| Halt, resume, flatten | Work; flatten sold at market within 30 seconds, fee 0.40% | run (API) |
| Watchdog alarms raise and clear | Works (`candle_stale`, `intraday_stale` during the first download) | run |
| Heartbeat summary, weekly report | Written | run |
| State after `docker compose restart` | Kept; all seven services back | run |
| Live order gate: `mode == "live"` and `TRADER_LIVE_ENABLED=true` | Present in `BitstampAdapter.place` and covered by a test | code, tests |
| Paper mode never builds the Bitstamp adapter | True (`execution/__main__.py`, `_adapter`) | code |
| Bitstamp request signing, rounding, minimum size, reconcile | Covered by fixture tests only | tests |

Not verified:

- Anything against a real Bitstamp account: balances, the account's
  actual fee, order placement, fills, reconcile. PR #24 states the same.
- The opt-in sandbox test (needs sandbox keys).
- Spec 4.3's "Done when" on the owner's Ubuntu box (reboot, restore).
- The Activity screen and the halt and flatten **buttons** in the
  browser (the API routes behind them were exercised).
- The emergency stop firing on a real price fall; only its code and
  tests were read. The flatten path, which uses the same market-sell
  code, was run.
- A daily digest for a day with activity. The only one written covered
  the day before the account existed ("Account: $0.00 ... No trades
  today because no daily decision ...").
- A daily cycle proposing a plan by itself: the targets were inside the
  trading band, so the plan used for the run was inserted by hand.

## Findings

Severity: **critical** stops the system doing its job; **high** must be
fixed before live; **medium** misleads the operator or risks data;
**low** is polish. Each finding has a spec in `docs/SPECS.md` section C.

| # | Severity | Finding | Spec |
|---|---|---|---|
| 1 | critical | Every scheduled buy plan is vetoed as "price data is stale" | R1 |
| 2 | high | The Bitstamp key cannot reach the containers and is not documented in `.env.example` | R2 |
| 3 | medium | A new database has no tables until a manual command is run; `/health` stays `ok` | R3 |
| 4 | medium | A blocked daily cycle is not retried until the next day | R4 |
| 5 | high | Spec 5.2's "Done when" is open: balances and actual fee never read with the owner's key | R5 |
| 6 | medium | Running the tests against the working database erases it | R6 |
| 7 | medium | Three controls can be changed but do nothing | R7 |
| 8 | medium | No test runs the pipeline with production candle timing, which is why finding 1 passed CI | R8 |
| 9 | high | The emergency stop reads a price up to about 6 minutes old | R9 |
| 10 | low | An approved entry order is not visible on Positions until it fills | R10 |
| 11 | low | Weekly reports are not on the Reports screen; the first one covers a week before the account existed | R11 |

### 1. Scheduled buy plans are always vetoed (critical)

- `risk.state` measures price age from the newest 5-minute candle's
  **open** time (`risk/engine.py`), and `check_plan` rejects a buy when
  the age is over `stale_candle_seconds = 600`.
- The collector stores only closed candles. At 00:05:00 UTC the newest
  one opened at 23:55, so its age is 600 seconds plus the scheduler's
  delay.
- Reproduced on the stored Bitstamp candles (in a transaction that was
  rolled back): with the table as it is at 00:05:00.050 the age is
  600.05 s, a BTC buy is refused with "Price data for BTC-USD is stale;
  no recent candles.", and a reducing sell is allowed.
- At other times the age swings between about 300 and 660 seconds, so
  an approval clicked in the minute after a 5-minute mark is refused the
  same way.
- The watchdog and the intraday cycle already measure from the candle's
  **close**, which is why no alarm shows. The tests seed "a just-closed
  candle", which hides it.
- Sells that reduce a holding and the emergency stop are not affected.

### 2. Bitstamp key: present in code, absent from configuration (high)

This answers the question about the missing environment variable.

- The settings exist: `TRADER_BITSTAMP_KEY`, `TRADER_BITSTAMP_SECRET`,
  `TRADER_BITSTAMP_BASE_URL` (`core/config.py`), read by
  `BitstampAdapter.from_env`. They are documented only in
  `docs/VENUE.md`.
- `.env.example` does not list them, and neither Compose file passes
  them to any container. A key put in `.env` would be ignored and the
  executor would refuse every pass in live mode.
- Paper mode needs no key and never reads one: the collector uses public
  endpoints and fills are simulated. So nothing is wrong today; it
  blocks spec 5.4.
- Both values are plain strings in `Settings`, so printing the settings
  object would print the secret. Nothing does so today.

### 3. No automatic migration (medium)

`docker compose up -d` on a new database starts every service, the
collector, engine, executor and watchdog fail with
`relation "..." does not exist`, and `/health` still answers `ok`. The
README's "Start stack" row does not mention `alembic upgrade head`. The
development Compose file also publishes the API and dashboard on every
network interface of the desktop.

### 4. A blocked daily cycle waits a day (medium)

On a first start the engine runs the daily cycle before the collector
has stored yesterday's candle and writes "Yesterday's BTC-USD candle is
missing; no decision made." The cycle would retry (its rows are all
`blocked`), but nothing calls it again until 00:05 UTC the next day or
an engine restart. The same applies to any day blocked by a short data
outage at 00:05.

### 5. Spec 5.2 is not finished (high)

Its last "Done when" line needs the owner's key. PR #24 says it was not
done. The fee assumed everywhere (0.30% maker, 0.40% taker) is therefore
still the published schedule, not the account's rate.

### 6. Tests can erase the working database (medium)

The schema tests run `alembic downgrade base` on whatever
`TEST_DATABASE_URL` names. CI itself uses a database called `trader`,
the same name as the working one, so copying CI's value on the desktop
wipes the paper account.

### 7. Controls that do nothing (medium)

- "Approval expiry" is saved to `approval_expiry_hours`, but
  `plans/builder.py` always uses 6 hours.
- `mode` can be written through `PUT /api/settings/mode`, but every
  service reads `TRADER_MODE` from the environment.
- "Autonomy level" is stored and never read.

None of these weakens a control; they mislead.

### 8. Test gap (medium)

Each module is tested with fixtures built for that module. No test
stores candles the way the collector does and then runs daily cycle,
approval, executor and fills in sequence.

### 9. Emergency-stop price is a stored candle (high, before live)

The executor checks every 30 seconds, as specified, but the price it
compares with the stop is the newest stored 5-minute close, which is
between 0 and about 6 minutes old. Acceptable for paper (fills are
simulated from the same candles); for live money the check should use
Bitstamp's public ticker.

### 10 and 11. Dashboard gaps (low)

- `/api/positions` lists resting orders only for assets already held, so
  between approval and the first fill the order appears nowhere but the
  Activity log.
- Weekly reports are `events` rows and appear only on Activity. The
  first one after a new start covers the previous week and reads
  "account $0.00, tracking gap not computable".

## Observations without a spec

- Dashboard logins are held in memory; an API restart logs the owner
  out. Acceptable for one user.
- Approving at half or quarter size stores a quantity with more than 8
  decimals (0.008258865 BTC in the run). The Bitstamp adapter rounds
  down to the lot before sending, so this affects paper only, by less
  than a cent.
- A market sell made by flatten or the emergency stop writes no `fill`
  event. The fill itself is stored and shown on Activity.
- During the first download the status strip is red for about five
  minutes. This is correct and is described in `docs/SETUP.md`.
- `README.md` said "Nothing is built yet"; corrected with this review.

## Order of the follow-up specs

| Step | Specs | Notes |
|---|---|---|
| 1 | R1 | First. Restart the paper period when it is deployed. |
| 2 | R3, R4, R6, R7 | Independent of each other; merge one at a time. |
| 3 | R8 | After R1 and R4. |
| 4 | R10, R11 | Any time. |
| 5 | R2, then R5, R9 | Before spec 5.4. R5 needs the owner. |
