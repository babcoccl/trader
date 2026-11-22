# Setup and usage guide

This guide gets the trader running in **paper mode**, explains how to use
it day to day, and shows how to check that it is working. Commands are
given for a Unix shell (Linux, macOS) and for Windows PowerShell; where
only one block is shown the command is identical in both.

State of the application at the time of writing (after spec 5.2, see
`docs/REVIEW-5.2.md`):

- Paper trading works end to end on Bitstamp's **public** price data. No
  Bitstamp account or API key is needed for it.
- Live trading is **not ready**. Do not set `TRADER_MODE=live`. The
  remaining work is listed in `docs/SPECS.md` section C and specs 5.3
  and 5.4.
- One known defect stops the daily cycle from proposing **buy** plans
  (review finding 1, fixed by spec R1). Until R1 is merged the system
  will run, log its checks and manage exits, but will not ask you to
  approve a purchase.

## 1. What runs

| Service | What it does | How often |
|---|---|---|
| `db` | PostgreSQL 16. All state lives here. | always |
| `collector` | Stores Bitstamp BTC/USD and ETH/USD candles (Coinbase as backup). | every minute |
| `engine` | Daily decision (nine trend rules, sizing, plan). Five-minute price check. | 00:05 UTC, and every 5 minutes |
| `executor` | Emergency stop, places approved orders, collects fills, updates the account. | every 30 seconds |
| `watchdog` | Alarms when a part goes quiet, daily digest, weekly paper report. Seeds the paper account. | every minute |
| `api` | HTTP API behind the dashboard. | always |
| `web` | The dashboard. | always |

## 2. What you need

- Docker with the Compose plugin: Docker Desktop on Windows, Docker
  Engine on Ubuntu.
- Git.
- About 2 GB of disk for images and the database.
- Outbound HTTPS to `www.bitstamp.net` and `api.exchange.coinbase.com`.
- Only for development (running the tests): Python 3.12.

You do **not** need a Bitstamp account, an API key or any funds for
paper mode.

## 3. First start on your desktop (paper mode)

This uses `docker-compose.yml`, which always runs in paper mode and
publishes the dashboard on port 3000 and the API on port 8000 of your
machine. For the always-on Ubuntu
box use section 9 instead.

### 3.1 Get the code and set a dashboard password

```bash
# Linux / macOS
git clone https://github.com/babcoccl/trader.git
cd trader
cp .env.example .env
```

```powershell
# Windows PowerShell
git clone https://github.com/babcoccl/trader.git
cd trader
Copy-Item .env.example .env
```

Open `.env` in an editor and set a password of your choice:

```
TRADER_DASHBOARD_PASSWORD=choose-something
```

Login is refused (HTTP 503) while this is empty. `.env` is ignored by
Git; never commit it.

### 3.2 Build, create the tables, start

The tables are **not** created automatically (review finding 3, fixed by
spec R3). Until R3 is merged, run these three commands in this order on
a new database:

```
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose up -d
```

The first command starts everything, but `collector`, `engine` and
`executor` exit because the tables do not exist yet. The second creates
the tables. The third starts the services that exited.

### 3.3 Check it is up

```bash
# Linux / macOS
curl http://localhost:8000/health
docker compose ps
```

```powershell
# Windows PowerShell
Invoke-RestMethod http://localhost:8000/health
docker compose ps
```

Expected: `{"status":"ok","mode":"paper"}` and seven services with state
`running`. `/health` only says the API process is alive; it stays `ok`
even when the tables are missing, so always look at `docker compose ps`
as well.

Open the dashboard at <http://localhost:3000> and log in with the
password from `.env`.

### 3.4 What to expect in the first ten minutes

| When | What you see | Is it a problem? |
|---|---|---|
| First minute | The collector downloads daily candles from 2019 and 30 days of 5-minute candles (about 23,000 rows). | No. |
| First minute | Watchdog deposits $5,000 into the paper account (`deposit` event). | No. |
| First minutes | Alarms `candle_stale:BTC-USD`, `candle_stale:ETH-USD`, `intraday_stale`; the status strip is red. | No. They clear by themselves within about 5 minutes (`alarm_cleared` events). |
| First minute | A daily decision "Yesterday's BTC-USD candle is missing; no decision made." | The engine ran before the download finished and does not retry until 00:05 UTC (finding 4, spec R4). Run `docker compose restart engine` once the collector has finished. |
| After restart of `engine` | Two daily decisions such as "Checked BTC-USD. 3 of 9 rules positive. Target 12%, holding 0%. ..." | No. This is the normal daily result. |
| Every 5 minutes | "Checked prices. BTC 60,541, ETH 1,699. ... Nothing to do." | No. This is the liveness signal. |
| First minutes | "Weekly paper report ...: account $0.00, tracking gap not computable", and a digest for yesterday with "Account: $0.00" | No. Both cover days before the account existed. |

## 4. Using the dashboard

The strip at the top of every screen shows whether the system is
running or halted, what it last checked and when, and the time to the
next daily decision. It turns red and names the part that stopped when a
check is late.

| Screen | Use it for |
|---|---|
| Overview | Account value against buy and hold, share invested, drop from the peak against the 35% halt, plans waiting for you, and a line per check explaining why nothing happened. |
| Approvals | Each waiting plan: reason, entry order, stop, worst case in dollars, fees, expiry. Approve at full, half or quarter size, or deny. Past decisions are listed below. |
| Positions | Holdings, average cost, the emergency-stop price and resting orders of each. |
| Activity | Every decision, order, fill, alarm, heartbeat summary and report. |
| Reports | Return, worst drop, fees, scorecards and the daily digests. |
| Controls | Halt, resume and flatten buttons; the few settings. |

### How a trade happens

1. At 00:05 UTC the engine computes the nine trend rules on daily
   candles for BTC and ETH and a target share of the account for each
   (40% volatility target).
2. If the target differs from the holding by more than the trading band
   (20% of the larger of the two), it builds a plan.
3. A **buy** plan waits on the Approvals screen for 6 hours. No answer
   means no trade. A plan is also withdrawn if the price moves more than
   2% from the plan's price before you answer.
4. A **sell** plan that only reduces a holding needs no approval.
5. After approval the executor places one limit order at the latest
   price, re-prices it every 5 minutes, and buys the rest at market one
   hour after approval.
6. In paper mode a limit order fills when a later 5-minute candle trades
   through its price (fee 0.30%); market orders fill at the candle open
   (fee 0.40% plus 0.01% slippage). Expect a fill to take 5 to 10
   minutes at the earliest, because only candles that opened after the
   order count.
7. Every 30 seconds the executor compares the latest stored price with
   each position's emergency-stop price and sells at market if it is
   reached. The stop is three ten-day price swings below the entry
   price (22% below in the review run).

### Limits you should know

- **The emergency stop is run by this application, not by the
  exchange.** If the machine is off, has no network or the `executor`
  service is down, nothing protects an open position. This was accepted
  when Bitstamp was chosen.
- The stop reads the newest stored 5-minute candle, so it can react up
  to about 6 minutes after the price crossed the stop (spec R9 shortens
  this for live mode).
- Trading halts by itself when the account is 35% under its peak. Exits
  are still allowed while halted. Resuming resets the peak.
- Changing "Autonomy level" or "Approval expiry" on Controls has no
  effect yet: every buy needs approval and expiry is fixed at 6 hours
  (finding 7, spec R7).

## 5. Checking that it works

Everything below reads the database or the API and changes nothing.

### 5.1 From the command line

```
docker compose ps
docker compose logs --tail 20 engine
docker compose exec db psql -U trader -c "select service, status, detail, time from heartbeats where id in (select max(id) from heartbeats group by service)"
docker compose exec db psql -U trader -c "select interval, symbol, source, count(*), max(open_time) from candles group by 1,2,3 order by 1,2"
docker compose exec db psql -U trader -c "select ran_at, cycle, symbol, action, reason from decisions order by id desc limit 10"
docker compose exec db psql -U trader -c "select id, symbol, side, status, target_weight, size_fraction, expires_at from plans order by id desc limit 10"
docker compose exec db psql -U trader -c "select id, plan_id, side, type, price, quantity, status, shadow from orders order by id desc limit 10"
docker compose exec db psql -U trader -c "select order_id, time, price, quantity, fee, liquidity from fills order by id desc limit 10"
docker compose exec db psql -U trader -c "select distinct on (asset) asset, quantity, time from balances where mode = 'paper' order by asset, id desc"
docker compose exec db psql -U trader -c "select time, equity_usd, high_water_mark, drawdown from equity_snapshots order by id desc limit 5"
docker compose exec db psql -U trader -c "select time, kind, severity, message from events order by id desc limit 20"
docker compose exec db psql -U trader -c "select day, body_md from digests order by day desc limit 1"
```

What healthy looks like:

- `heartbeats`: one row per service (`api`, `collector`, `engine`,
  `executor`, `watchdog`), status `ok`, less than a minute old.
- `candles`: the newest `5m` row is less than 11 minutes old; the newest
  `1d` row is yesterday (UTC).
- `decisions`: an `intraday` row every 5 minutes, and `daily` rows for
  BTC-USD and ETH-USD dated today (UTC).
- `orders.shadow = true` rows are simulations of plans you denied or let
  expire. They never change the account.

### 5.2 From the API

```bash
# Linux / macOS
curl -s -c cookies.txt -X POST http://localhost:8000/api/login \
    -H 'Content-Type: application/json' -d '{"password":"<dashboard password>"}'
curl -s -b cookies.txt http://localhost:8000/api/status
curl -s -b cookies.txt http://localhost:8000/api/positions
curl -s -b cookies.txt "http://localhost:8000/api/plans?status=pending"
```

```powershell
# Windows PowerShell
Invoke-RestMethod -SessionVariable s -Method Post http://localhost:8000/api/login `
    -ContentType 'application/json' -Body '{"password":"<dashboard password>"}'
Invoke-RestMethod -WebSession $s http://localhost:8000/api/status
Invoke-RestMethod -WebSession $s http://localhost:8000/api/positions
Invoke-RestMethod -WebSession $s "http://localhost:8000/api/plans?status=pending"
```

All routes are listed in `docs/API.md`. Logins are held in memory, so
you must log in again after the `api` service restarts.

### 5.3 Paper account and weekly report

The watchdog deposits $5,000 once and writes a weekly report every
Monday at 00:30 UTC. To write a report for any window by hand (the end
date is exclusive):

```
docker compose exec watchdog python -m trader.ops.paper_report report 2026-11-23 2026-11-30
```

The report is an `events` row with `kind = 'weekly_report'`; it appears
on the Activity screen. Details are in `docs/PAPER.md`.

## 6. Halt, resume and flatten

Use the buttons on the Controls screen, or the API:

```bash
# Linux / macOS (after the login in 5.2)
curl -s -b cookies.txt -X POST http://localhost:8000/api/controls/halt \
    -H 'Content-Type: application/json' -d '{"reason":"manual halt"}'
curl -s -b cookies.txt -X POST http://localhost:8000/api/controls/resume
curl -s -b cookies.txt -X POST http://localhost:8000/api/controls/flatten \
    -H 'Content-Type: application/json' -d '{"confirm":"FLATTEN"}'
```

```powershell
# Windows PowerShell (after the login in 5.2)
Invoke-RestMethod -WebSession $s -Method Post http://localhost:8000/api/controls/halt `
    -ContentType 'application/json' -Body '{"reason":"manual halt"}'
Invoke-RestMethod -WebSession $s -Method Post http://localhost:8000/api/controls/resume
Invoke-RestMethod -WebSession $s -Method Post http://localhost:8000/api/controls/flatten `
    -ContentType 'application/json' -Body '{"confirm":"FLATTEN"}'
```

- **Halt** stops new buy plans and approvals. Exits and the emergency
  stop keep working.
- **Flatten** sells every position at market on the executor's next
  pass (within 30 seconds). It does not halt; halt first if you want the
  system to stay out.
- **Resume** lifts the halt and resets the peak used for the 35% limit.

If the API is down but the database is up, halt by writing the setting
directly; the command is in `docs/RUNBOOK.md` ("Halt, resume, flatten
from the command line").

## 7. Stop, restart, upgrade, reset

| Purpose | Command (both shells) | Effect on data |
|---|---|---|
| Stop | `docker compose down` | Kept. |
| Start again | `docker compose up -d` | Kept. The collector refills missed candles; the executor checks stops at once. |
| Restart one service | `docker compose restart engine` | Kept. |
| Follow logs | `docker compose logs -f --tail 50 executor` | None. |
| Upgrade | `git pull`, then `docker compose up -d --build`, then `docker compose exec api alembic upgrade head`, then `docker compose up -d` | Kept. |
| Start over with an empty paper account | `docker compose down -v`, then section 3.2 | **Deleted.** |

While the stack is stopped nothing is checked: no decisions, no fills
and no emergency stop.

## 8. Development and tests

```bash
# Linux / macOS
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
docker compose up -d db
docker compose exec db psql -U trader -c "create database trader_test"
export TEST_DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trader_test
ruff check . && ruff format --check . && mypy src/trader && pytest
```

```powershell
# Windows PowerShell
py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pre-commit install
docker compose up -d db
docker compose exec db psql -U trader -c "create database trader_test"
$env:TEST_DATABASE_URL = "postgresql+psycopg://trader:trader@localhost:5432/trader_test"
ruff check .; ruff format --check .; mypy src/trader; pytest
```

**Never point `TEST_DATABASE_URL` at the `trader` database.** The tests
drop and re-create every table and would erase the paper account and
its history (finding 6, spec R6 adds a guard). Without
`TEST_DATABASE_URL` the database tests are skipped.

The dashboard's own tests: `cd web`, `npm ci`, `npm test`.

## 9. The Ubuntu box

`docs/RUNBOOK.md` is the full procedure (install, nightly backup,
upgrade, restore, alarms). In short:

```bash
git clone https://github.com/babcoccl/trader.git /opt/trader && cd /opt/trader
printf 'TRADER_DASHBOARD_PASSWORD=<password>\nTRADER_LAN_ADDRESS=<LAN IP of the box>\n' > .env
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
```

```powershell
# Windows PowerShell, run from the desktop over ssh
ssh trader-box 'cd /opt/trader && docker compose -f docker-compose.prod.yml up -d --build && docker compose -f docker-compose.prod.yml exec -T api alembic upgrade head'
```

Differences from the desktop stack: services restart by themselves
(also after a reboot), the dashboard is at `http://<LAN IP>:3000` and
reachable from the home network only, and the API and database publish
no ports. Leave `TRADER_MODE` unset so the box stays in paper mode.

## 10. Bitstamp API key and live mode

**Paper mode never uses an API key.** Prices come from Bitstamp's public
endpoints and fills are simulated from them. That is why `.env.example`
has no key in it.

The live adapter (spec 5.2) reads these variables:

| Variable | Meaning | Default |
|---|---|---|
| `TRADER_MODE` | `paper` or `live`. | `paper` |
| `TRADER_LIVE_ENABLED` | Second switch. Orders are refused unless this is `true` **and** the mode is `live`. | `false` |
| `TRADER_BITSTAMP_KEY` | Bitstamp API key. | empty |
| `TRADER_BITSTAMP_SECRET` | Bitstamp API secret. | empty |
| `TRADER_BITSTAMP_BASE_URL` | API address; set to the sandbox address for the opt-in test. | `https://www.bitstamp.net/api/v2` |

Do not set them yet:

- Neither Compose file passes `TRADER_BITSTAMP_KEY` or
  `TRADER_BITSTAMP_SECRET` to the containers, so a key in `.env` is
  ignored (finding 2, spec R2).
- The live adapter has never been run against a real account. Reading
  the balances and the account's actual fee is spec R5 and needs you.
- Going live is spec 5.4, after the four-week paper gate (spec 5.3).

When you create the key (for R5), follow `docs/VENUE.md`: no withdrawal
permission, and keep the secret out of Git, chat and screenshots. In
live mode with no key the executor refuses every pass with
"TRADER_BITSTAMP_KEY / TRADER_BITSTAMP_SECRET are not set" and places
nothing.

## 11. All settings

Environment variables (in `.env` next to the Compose file):

| Variable | Used by | Meaning |
|---|---|---|
| `TRADER_DASHBOARD_PASSWORD` | `api` | Dashboard login. Required. |
| `TRADER_LAN_ADDRESS` | `docker-compose.prod.yml` only | Address the dashboard binds to. Required on the box. |
| `TRADER_MODE`, `TRADER_LIVE_ENABLED` | `docker-compose.prod.yml` only | See section 10. `docker-compose.yml` is fixed to paper. |
| `TRADER_LOG_LEVEL` | all | `INFO` by default. |
| `TRADER_DATABASE_URL` | all | Set by Compose. Change only when running a service outside Compose. |

Settings on the Controls screen: silence alarm (minutes without a
heartbeat before an alarm, default 5) and daily digest time (default
00:30 UTC). The risk limits (35% halt, 40% volatility target, 6-hour
expiry, 2% price-move void) are fixed in code on purpose.

## 12. Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `docker compose ps` shows `collector`, `engine` or `executor` as exited; logs say `relation "..." does not exist` | Tables were not created. | `docker compose exec api alembic upgrade head`, then `docker compose up -d`. |
| Login returns 503 "dashboard password is not configured" | `TRADER_DASHBOARD_PASSWORD` is empty. | Set it in `.env`, then `docker compose up -d`. |
| Every API call returns 401 after a restart | Logins are kept in memory. | Log in again. |
| Status strip red, `candle_stale` or `intraday_stale` right after a start | Initial download still running. | Wait 5 minutes. If it stays, read `docker compose logs collector`. |
| `service_silent:<name>` alarm | That service stopped. | `docker compose ps`, `docker compose logs <name>`, `docker compose up -d`. |
| Daily decision says "Yesterday's ... candle is missing" | Engine ran before the collector finished. | `docker compose restart engine`. |
| Daily decision says "Trade blocked by risk: Price data for ... is stale" | Finding 1. | Needs spec R1. No workaround that is safe to script. |
| "Approve" answers that price data is stale | Same cause; happens in the minute after each 5-minute mark. | Click Approve again a minute later. |
| Approved order still open after several minutes | Paper fills need a later candle to trade through the price. | Wait. It is re-priced every 5 minutes and bought at market after one hour. |
| Port 5432, 8000 or 3000 already in use | Another program uses it. | Stop that program, or change the left-hand port in `docker-compose.yml`. |
| `docker compose` not found on Windows | Docker Desktop not running. | Start Docker Desktop and reopen PowerShell. |
