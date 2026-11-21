# Autonomous trader: implementation plan for Devin sessions

Status: agreed with the owner, 2026-11-21. The documents, study scripts and test fixtures are already in this repo. Companion to `docs/DESIGN.md` (design, study results, decisions) and `docs/mockup.html` (screens).

## 1. What is being built

A single-user trading application that runs on the home Ubuntu box, trades BTC and ETH spot, long only, and asks for approval in a web dashboard before each plan.

Decisions already made (do not reopen in sessions):

| Topic | Decision |
|---|---|
| Objective | Grow USD value; home position is cash |
| Capital | $1k-$10k to start |
| Strategy | Plain average of nine daily trend rules (SMA 50/100/200, momentum 30/90/180, breakout 20/55/100) on BTC and ETH |
| Sizing | 40% volatility target, no leverage, long only |
| Drawdown halt | 35% from the account high-water mark |
| Approval | Plan-level approve / reduce / deny in the dashboard; 6-hour expiry; silence means no trade; stops in an approved plan run without a second approval |
| LLM | Local, advisory only: extracts news events and writes rationale; never places orders or overrides limits; trading continues on rules if it is offline |
| Hosting | Home Ubuntu box (2x RTX 5060 Ti 16 GB); remote access is phase 2 |
| Clients | Desktop browser first; simpler mobile view acceptable |
| Liveness | Status strip, a logged reason for every decision cycle including "no trade", heartbeats, daily digest, silence alarm |
| Repository | New private repo `babcoccl/trader` |
| Venue | Bitstamp (0.30% maker, 0.40% taker under $10,000 a month), behind an adapter interface. Binance.US, Kraken and Hyperliquid are not available to the owner (Maine); Alpaca was rejected for thin trading |
| Emergency stop | Run by the application; no stop rests on the exchange. Positions are unprotected while the home box is offline (accepted) |
| Evaluation cadence | Every 5 minutes, each cycle logged; trades stay rare |
| Experimental sleeve E | Parked: it does not survive this venue's fees |
| Not in scope now | Alt-coins, shorts, perpetuals, standalone funding carry, liquidity provision, grids, small-dip scalping |

Still open (each blocks only the phase named):

| Open item | Blocks | Default if not answered |
|---|---|---|
| Bitstamp account opened and verified by the owner | Phase 5 (venue adapter) | If refused: Alpaca crypto, then Crypto.com Exchange |
| News sources for the LLM overlay | Phase 6 | RSS feeds from a short allow-list, chosen in that phase |
| Which local model server and models | Phase 6 | Ollama with one small and one mid-size model, chosen by a benchmark session |
| Where idle cash earns yield | Phase 7 | Leave as plain USD |

## 2. Architecture in brief

- **Language and runtime:** Python 3.12, one repository, services run with Docker Compose on the Ubuntu box.
- **Database:** PostgreSQL (TimescaleDB optional later). All state lives here; services are restartable at any time.
- **Services:** `collector` (prices, funding), `engine` (signals, plans, risk), `executor` (orders, fills, reconciliation), `api` (FastAPI, REST + server-sent events), `web` (React + TypeScript dashboard), `llm` (phase 6), `watchdog` (liveness).
- **One code path:** backtest, paper and live use the same strategy, risk and plan code. Only the market-data source and the execution adapter differ.
- **Ledger is the source of truth:** append-only orders, fills, fees and balances, reconciled against the venue.
- **Money safety:** API keys are trade-only with withdrawals disabled, read from environment or a local secrets file, never committed.

## 3. Rules for every session

1. One session, one pull request, against the new repo. Keep PRs under about 800 changed lines where possible.
2. Read `docs/DESIGN.md`, `docs/DECISIONS.md` and the task's acceptance criteria before writing code.
3. Tests come with the code. CI (lint, type check, unit tests) must pass before asking for review.
4. No session may place a real order or use a real key unless its task says so. Live-capable code must default to paper mode.
5. Never weaken a risk limit, the halt, or the approval requirement to make a test pass.
6. Any number shown in the dashboard must be traceable to a ledger or log row.
7. Windows note: the owner's desktop is Windows with Docker Desktop and no `make`; provide PowerShell alongside shell commands in docs. The deployment target is Ubuntu.
8. Finish with: what was built, how it was verified, what is not done.

## 4. Phases and sessions

Sessions inside a phase are listed in dependency order. "Parallel" marks sessions that can run at the same time.

### Phase 0: Foundation (3 sessions)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 0.1 | Create repo and skeleton: package layout (`core`, `data`, `strategy`, `risk`, `execution`, `ledger`, `api`, `web`), Docker Compose with Postgres, config loading, structured logging, CI (ruff, mypy, pytest), pre-commit | none | `docker compose up` starts Postgres and an empty API returning health; CI green |
| 0.2 | Optional: rebuild the full study from public data by following `research/README.md` and confirm the committed fixtures | none | `check_spec.py` output quoted in the PR |
| 0.3 | Database schema and migrations (Alembic): candles, funding, signals, decisions, plans, approvals, orders, fills, balances, equity snapshots, events, heartbeats, settings, audit log | 0.1 | Migrations apply and roll back cleanly; schema documented |

### Phase 1: Data and the strategy core (4 sessions)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 1.1 | Market-data collector: daily and hourly candles for BTC and ETH from the chosen venue plus one backup source, gap detection and backfill, funding-rate recording | 0.3 | Backfills 2017 onward; restarts without duplicates; a deliberately removed day is detected and refilled |
| 1.2 | Strategy library: the nine rules, the ensemble, volatility-target sizing, as pure functions over a price frame | 0.1 | Unit tests match the research code's signals and weights on fixed fixtures to 1e-9 |
| 1.3 | Backtest engine in the repo using the production strategy and cost model (fee, slippage, next-open fills, trading band) | 1.2 | Reproduces the study: 40% volatility target, 0.40% fee, 20% trading band, about 24.5% a year 2018-2026 and 11.4% a year 2022-2026, within 0.3 points; look-ahead test passes; reports the result at trading bands of 0%, 10%, 20% and 30% |
| 1.4 | Decision cycle: scheduled job after the daily close that computes target exposure and writes a decision record with a plain-language reason, including "no trade" | 1.1, 1.2 | Replaying 30 historical days writes 30 decision rows with reasons; no row is skipped when nothing changes |

### Phase 2: Risk, plans, ledger, paper trading (5 sessions)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 2.1 | Risk engine: exposure caps, per-plan worst-case loss, 35% drawdown halt, stale-data and stale-price guards, global halt and flatten state | 1.4 | Property tests: no sequence of inputs produces an order that breaches a limit; halt blocks new plans |
| 2.2 | Plan builder: turns a target-exposure change into a plan (entry ladder, stop, expiry, fees, worst case, rationale text) | 2.1 | Golden-file tests for buy, reduce and exit plans; worst-case figure equals the sum of its parts |
| 2.3 | Ledger and accounting: append-only orders, fills, fees; positions, cost basis, equity curve, high-water mark | 0.3, 1.4 | Recomputing balances from fills equals stored balances on randomised sequences |
| 2.4 | Paper execution adapter: simulated limit and stop orders against live prices with fees and slippage; same interface the live adapter will use | 2.3 | A scripted price path produces the expected fills; partial fills and expiries handled |
| 2.5 | Approval state machine: pending, approved (with size reduction), denied, expired, voided on price move; shadow ledger for denied and expired plans | 2.2, 2.4 | State-transition tests cover every edge; an unanswered plan expires at 6 hours and is tracked in shadow |

### Phase 3: API and dashboard (6 sessions; 3.2-3.5 parallel after 3.1)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 3.1 | API: REST endpoints and a server-sent event stream for status, decisions, plans, positions, activity, reports, settings; local login | 2.5 | OpenAPI spec published; contract tests pass; unauthenticated calls rejected |
| 3.2 | Web shell and Overview screen: status strip on every page, account value against baselines, exposure, drop from peak, "why nothing has happened lately" | 3.1 | Matches the mockup; status strip updates live without reload |
| 3.3 | Approvals screen: plan card, full / half / quarter, approve, deny, expiry countdown, past decisions with shadow results | 3.1 | Approving in the UI changes plan state and triggers paper orders; cannot enlarge a plan |
| 3.4 | Positions and Activity screens: holdings, stops, resting orders, funding watch; searchable activity log | 3.1 | Every row links to its ledger or log record |
| 3.5 | Reports and Controls screens: scorecards, fees, live-versus-backtest gap; halt, flatten (typed confirmation), settings with audit log | 3.1 | Halt and flatten work end to end in paper mode; every settings change is logged |
| 3.6 | Mobile-width pass: approvals and status usable on a phone; other screens readable | 3.2-3.5 | Approve / deny works at 390 px width |

### Phase 4: Liveness and operations (3 sessions)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 4.1 | Heartbeats and watchdog: each service reports on a schedule; watchdog raises a silence alarm when one is late or a daily decision did not run; periodic heartbeat entries in the activity log | 3.1 | Killing any one service turns the status strip red within 5 minutes and names the service |
| 4.2 | Daily digest: one summary a day even when nothing traded (checks run, decisions and reasons, account value, anything unusual), shown in the dashboard and stored | 4.1 | A day with no trades still produces a digest |
| 4.3 | Deployment on the Ubuntu box: Compose stack, automatic restart, nightly database backup and restore test, runbook | 4.1 | Reboot test: everything returns without manual steps; restore from backup verified |

### Phase 5: Paper-trading gate, then live (4 sessions plus a waiting period)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 5.1 | Start paper trading and a weekly paper report comparing paper results with the backtest for the same days | Phase 4 | Report generated automatically each week |
| 5.2 | Bitstamp adapter: balances, fees, orders behind a flag; reconciliation of ledger against venue | 2.4, Bitstamp account | Signing, rounding and reconcile tests pass; balances and fee tier read with the owner's key; adapter refuses to trade unless mode is live |
| 5.3 | Promotion gate review after 4 weeks of paper: tracking error against backtest, uptime, alarm history, approval latency | 5.1 | Written go / no-go; owner sign-off required |
| 5.4 | Go live with $1,000: owner creates live keys with the narrowest permissions offered; first live plan approved in the dashboard | 5.2, 5.3 | First live round trip reconciles; halt and flatten tested live at minimum size |

### Phase 6: Local LLM overlay, shadow mode (4 sessions; can start after phase 3)

| ID | Session | Depends on | Acceptance criteria |
|---|---|---|---|
| 6.1 | Model serving on the two GPUs and a benchmark: candidate extraction and analyst models scored on speed and on a labelled set of crypto news items | 4.3 | Benchmark report; chosen models recorded in `DECISIONS.md` |
| 6.2 | News ingestion: allow-listed feeds, de-duplication, storage with timestamps of first sight (no look-ahead) | 0.3 | Re-ingesting the same feed adds nothing |
| 6.3 | Event extraction and thesis: structured events with sources and confidence; a daily written view per asset; treat article text as untrusted input | 6.1, 6.2 | Schema-validated output; prompt-injection test articles do not change system behaviour |
| 6.4 | Shadow scoring and dashboard: the news view appears on plans and is scored against outcomes; it cannot change sizes | 6.3, 3.3 | Scorecard on the Reports screen; a code-level test proves the overlay has no path to sizing |

### Phase 7: Later, each needs its own go-ahead

- Remote access from outside the home network (VPN or tunnel) and push notifications.
- Autonomy ladder step 3: auto-approve plans risking under a set share of the account.
- LLM tilt inside a narrow band, only after the shadow scorecard justifies it.
- Carry proposals when funding clearly exceeds the Treasury-bill rate.
- Idle-cash yield.
- Liquidity provision as a separate sleeve.

Detailed per-session specifications for a lightweight model are in `docs/SPECS.md`. Session added there: 1.5 (five-minute evaluation loop). 5.5 (sleeve E) is parked.

## 5. Order and rough size

| Phase | Sessions | Can overlap with |
|---|---|---|
| 0 Foundation | 3 | none |
| 1 Data and strategy | 4 | 1.1 and 1.2 in parallel |
| 2 Risk, plans, ledger, paper | 5 | 2.3 in parallel with 2.1 |
| 3 API and dashboard | 6 | 3.2-3.5 in parallel |
| 4 Liveness and operations | 3 | Phase 6 start |
| 5 Paper gate and live | 4 | 4 weeks of paper running is the long pole |
| 6 LLM overlay | 4 | Phases 4-5 |

About 30 sessions. The calendar time is set by the four-week paper period, not by the build.

## 6. Risks to watch

- **The edge is modest and unproven after 2022** (85% bootstrap chance of being above zero). The paper and small-live gates exist to find out cheaply.
- **The 11.4% figure depends on the trading band.** At the 40% volatility target, 2022-2026 returned 8.5% a year with no band, 9.1% at 10%, 11.4% at 20% and 11.0% at 30%. The return levelling off at a 40-45% target holds at every band. Plan on 8.5-11.5%, and do not tune the band to the higher number.
- **Approval delay is not in the backtest.** The backtest buys at the day's open; live, a buy waits for approval for up to 6 hours. The weekly paper report (5.1) measures the gap this causes.
- **The emergency stop is not in the backtest and does not rest on the exchange.** It is wide (roughly 20-30% below entry) and is run by the application every 30 seconds, so it does nothing while the home box is offline. The silence alarm (4.1) and a later off-site watchdog are the mitigations.
- **Venue check was one sample.** Bitstamp's depth was measured once, for about 4 minutes on 2026-11-20; its fee schedule taking effect 2026-11-24 keeps the same trading tiers, and the account's actual rate is read in 5.2.
- **Fees:** the study's headline uses 0.40% per side, which is Bitstamp's immediate rate; with resting orders at 0.30% the same sizing returned 12.8% a year over 2022-2026, and 11.9% at 0.40%. The sleeve trades only about 7 times the account a year, so each 0.1% of cost per side is worth about 0.9 points a year.
- **Home hosting:** power or internet loss stops the system, including the emergency stop.
- **Single venue data:** the study used Binance prices; live results on another venue will differ slightly.
