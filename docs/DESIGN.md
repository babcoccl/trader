# Autonomous crypto trader: design, studies and reasoning

> This file is the history of how the design was reached, in the order it happened. Earlier sections contain proposals that later sections overturn (for example the 15% drawdown limit, perpetuals, Binance.US and Alpaca). **Where this file differs from `docs/DECISIONS.md` or `docs/SPECS.md`, those two win.** Sessions implementing a spec do not need to read this file.

Status: nothing here is built yet. Sections 1 and 9 are based on reading the three repos; the rest is a proposal to argue with.

## 0. Decisions so far (from your answers)

| Topic | Decision | Effect on the design |
|---|---|---|
| Objective | Grow USD value | Home position is USD/stablecoin. Risk is taken only when a signal is on. Results are measured in USD against holding cash and against HODL. |
| Capital | $1k-$10k to start, more if it works | Fees and minimum order sizes dominate. Venue choice is the largest single lever (section 3a). On-chain LP is deferred until capital is larger. |
| Venues | Any CEX or DEX; alt-coins and derivatives are acceptable | Execute where fees are low and keep Coinbase as the fiat on/off-ramp. Shorting becomes possible, so the strategy can earn in downtrends. |
| LLM hardware | Ubuntu box, 2x RTX 5060 Ti 16 GB | Two-model layout, one per card (section 4a). |
| Autonomy | Approval workflow: "I would like to make this trade because... approve/deny?" | Human-in-the-loop is a first-class component (section 5a), with a path to more autonomy as the record builds. |

## 1. What the three earlier attempts show

### eth-bot (Apr-May 2026, 42 flat Python files, ~10.7k lines)

What it is: an hourly regime classifier (`MacroSupervisor`: CRASH / CORRECTION / RECOVERY / RANGE / BULL) that switches five tactical bots on and off (trend, grid/range, correction, recovery, hedge), a Coinbase Advanced Trade executor with a paper mode, a WebSocket feed, a small dashboard, and a hook for an LLM "conviction" score.

What held it back:

- **Fees in the backtest are about 10x lower than fees in the executor.** The bots assume 0.025%-0.065% per side. `eth_coinbase_executor.py` defaults to 0.6%. The saved RangeBot state shows 612 round trips, $148.9k of turnover on $1,000 of capital, $49 net profit after $97 of assumed fees. The same turnover at 0.6% costs about $893, which turns +$49 into roughly -$750. Grid steps of 0.2%-0.4% are smaller than one retail round trip.
- **The result rests on one window.** `regression_baseline.json` records $288.56 total PnL, of which $272.28 comes from one cycle (May-Jun 2022). The other three cycles sum to about +$16.
- **Parameters were tuned on the windows they were scored on** (commits r6 to r15 adjust RSI/z-score gates against the same trend windows). There is no held-out period.
- **Regime labels are partly artefacts.** `TRANSITION_ANALYSIS.md` notes every RECOVERY exit lands at exactly 7.0 days because of a hard-coded `recovery_bars = 168`, and the median BULL lasts 1 day.
- **Live and backtest paths differ.** The live engine keeps 100 hourly bars; supervisor parameters look back up to 720.
- **The LLM is a mock.** `eth_llm_advisor_mock.py` writes a conviction number typed on the command line. No media ingestion exists.

Worth keeping: the supervisor-plus-specialist-bots idea, `BotInterface`, the allocation request/throttle pattern, the LLM digest format, paper mode, and the regression-baseline habit.

### liquidity-bot (May 2026, ~176 Python files, 17+ sprints)

What it is: concentrated-liquidity management on Aerodrome (Base): regime classifier, range selector, accountant, pool hunter/scorer, paper executor, harvest engine, dashboard, email reports, perp-hedge sketch.

What it found, in its own documents:

- 14-day AERO/USDC run: $277 fees, about -$314 to -$388 vs HODL across settings.
- 90-day AERO/USDC run: $1,576 fees, 227 rebalances, $541 slippage, $236 gas, **-$1,481 vs HODL**.
- `next_steps.md` concludes unhedged concentrated LP belongs in sideways markets only, and that a delta hedge or regime-gated allocation is needed.

What held it back: the accounting could not be trusted. Fees were synthesised from volume estimates, price direction was inverted in places, and a later sprint reports $203k net PnL against $6.3k of gross fees, which looks like an accounting artefact (my inference). That is the stated reason v2 exists.

### liquidity-bot-v2 (June 2026, 95 Python files, 39 sprints)

What it is: a clean, layered, unit-tested rewrite (registry, core IL/fee math, scorer, exit signals, hourly harness, reporting), backtest-only.

Where it stopped:

- Most sprints went into data plumbing: The Graph outage, GeckoTerminal rate limits, DeFiLlama TVL, CoinGecko ID mapping.
- Fee income is still estimated as volume x fee rate x position/TVL, not from on-chain `feeGrowthGlobal`. One TVL bug overstated fees 6x.
- `execution/base_executor.py` raises `NotImplementedError` throughout.
- `results/` holds test fixtures only; no real-data backtest verdict is committed.
- Its primary success criterion (fee and IL numbers matching verified Uniswap V3 math from real fee-growth snapshots) was not reached.

### The common pattern

All three produced a lot of working machinery and no strategy that was shown to make money after real costs on data it had not been tuned on. Engineering was not the constraint. The constraint was a validation process that could not say "this has an edge" or "this does not" early and cheaply.

## 2. Principles the new design follows

1. **Edge first, machinery second.** Any strategy must beat simple baselines (HODL, DCA, a plain moving-average rule) after real venue fees on held-out data before more code is written around it.
2. **One code path.** Backtest, paper and live run the same strategy and risk code. Only the data source and the fill engine change.
3. **Low turnover by default.** At retail fee tiers, strategies that trade a few times a month survive; strategies that trade many times a day do not.
4. **Deterministic core, advisory LLM.** The LLM proposes and explains. Rules decide sizes and limits, and can always veto.
5. **Point-in-time everything.** Every bar, article, LLM output and decision is stored with the time it became known, so results can be replayed without look-ahead.
6. **Capital is promoted in steps.** Backtest, then paper, then a small live allocation, each with written pass criteria.

## 3. Strategy: a portfolio of sleeves

A top-level allocator splits capital across independent sleeves. Each sleeve is a strategy with its own budget and limits. Start with sleeve A only.

### Sleeve A: goal-based trend positioning (build first)

This is the "set goals for buys and sells from trend observation" idea, made explicit.

- Universe: BTC and ETH only. The feasibility study found that widening to the 10 or 20 most-traded assets turned a gain into a loss after 2021.
- Instrument: spot on a low-fee venue. Perpetual funding averaged about 19% a year on the amount held long over 2018-2026 (9.5% since 2022), which cancels the fee saving.
- Direction: long-only. The short side lost money over 2018-2026.
- Signal: the plain average of several simple trend rules, not one tuned rule.
- Size: 40% volatility target (the position is cut when BTC and ETH swing more than usual), matched to the 35% drawdown halt chosen on 2026-11-20.
- Timeframe: daily and 4-hour bars. Decisions a few times a week at most.
- Regime layer: reuse the eth-bot idea but simplify to three or four states derived from trend, volatility and drawdown, with no hard-coded dwell artefacts.
- Output: a **trade plan** per asset, not individual orders. A plan holds a target exposure (0-100% of the sleeve), a ladder of buy zones, a ladder of sell targets, an invalidation level, and an expiry.
- Execution turns the plan into resting limit orders (maker fees) and re-plans when regime, price or news changes.
- Realistic aim: capture most of an uptrend and sit out most of a deep drawdown. Expect it to lag HODL in sharp V-shaped recoveries. Its job is better risk-adjusted return than HODL, not a higher raw return in every year.

### 3a. Venue fees decide what is viable at this account size

Entry-tier rates from each venue's published schedule (checked 2026-11-20; your account page is the authority):

| Venue and product | Maker | Taker | Cost of moving a $5k account in and out once (maker / taker) |
|---|---|---|---|
| Coinbase Advanced spot, US intro tier | 0.50% | 0.90% | $50 / $90 |
| Kraken Pro spot, tier 1 | 0.40% | 0.80% | $40 / $80 |
| Coinbase Advanced futures, intro tier | 0.095% | 0.10% | $9.50 / $10, plus $0.15 per contract |
| Kraken Pro futures, tier 1 | 0.02% | 0.05% | $2 / $5 |
| Hyperliquid perps, base tier | 0.015% | 0.045% | $1.50 / $4.50 |
| Binance.US spot, most pairs | 0% | 0.02% | $0 / $2 |

What follows from this:

- A strategy that turns the account over twice a month pays about 24% a year in fees on Coinbase spot as a maker, and under 1% as a maker on Kraken futures or Hyperliquid. The same signal can be profitable on one venue and a steady loss on another.
- Coinbase stays as the bank: fiat in and out, and long-term holdings. Trading happens elsewhere.
- The feasibility study (section 3b) found that for long trend positions, perpetual funding cancels the fee saving. Low-fee spot is the better instrument for sleeve A.
- Perpetuals add costs that spot does not have: funding payments (which can be positive or negative), liquidation risk if leverage is used, and exchange or smart-contract counterparty risk. The design caps leverage at 1x and treats funding as a cost in every backtest.
- Each venue has its own eligibility rules by country. You said all are open to you; confirm that for the specific derivatives venue before funding it.
- The execution layer is venue-neutral, so the choice can change later without touching strategy code.

### 3b. Feasibility study result (2026-11-20)

Daily data, April 2018 to November 2026, nine untuned trend rules and their average, real fees, 2022-2026 held out. Full report: `docs/studies/trend-feasibility-study.html`.

| Measure (BTC and ETH, 0.40% fee per side) | Trend rules | Buy and hold BTC |
|---|---|---|
| Yearly return, 2018-2026 | 36.0% | 29.2% |
| Yearly return, 2022-2026 (held out) | 11.5% | 5.5% |
| Worst loss from a peak | -47% | -77% |
| Yearly return when sized for a 15% drawdown limit | 4.0% (2022-2026) to 10.3% (2018-2026) | not applicable |

The post-2022 result has an 85% bootstrap chance of being above zero, which is short of proof. The sleeve is worth building small; it is not a large source of return at this account size.

### 3c. Study 2 result: carry, other strategies, drawdown limit (2026-11-20)

Full report: `docs/studies/carry-and-drawdown-study.html`.

- Drawdown limit: return and loss rise together. In the held-out period (2022-2026) the return peaks at a 35% limit with 40% volatility-target sizing (11.4% a year, against 4.0% at the 15% limit) and is flat beyond it. Each option carries roughly a one-in-four or lower simulated chance of a halt within three years. Decided 2026-11-20: 35% limit with 40% volatility-target sizing. Check run after the decision: the 11.4% depends on the 20% trading band (8.5% with no band, 9.1% at 10%, 11.0% at 30%); the levelling-off at a 40-45% target holds at every band.
- Sizing: volatility target matched to the limit, in place of a fixed share of the account.
- Idle cash: earning a Treasury-bill-like rate on it added 2 to 3.5 points a year since 2022. The product that pays this on the chosen venue is not yet identified.
- Funding carry (sleeve D): 3.6% a year since 2022 at Binance funding rates against 4.1% for Treasury bills; 8.6% against 4.5% at Hyperliquid rates since May 2023, but below Treasury bills in 2026. No separate sleeve now. Record funding from day one and propose a carry trade when funding exceeds the Treasury-bill rate by a set margin.
- Other strategies (BTC/ETH rotation, BTC-gated alt-coin trend, cross-sectional momentum, dip buying): none beat the trend sleeve reliably. Left out.

### Sleeve B: media and fundamentals overlay (collect now, trade later)

- Ingest news, project announcements, regulatory and macro calendars, on-chain and derivatives data (funding, open interest, stablecoin flows, exchange flows).
- The local LLM converts text into structured events: asset, event type, direction, magnitude, novelty, source reliability, confidence.
- At first the overlay only adjusts sleeve A inside a narrow band (for example exposure x0.5 to x1.25, or a temporary "no new buys" flag on a high-severity negative event).
- It runs in shadow mode for weeks before it is allowed to change sizes. Its calls are scored against what happened next, so it earns or loses influence on evidence.
- News cannot be backtested honestly without a point-in-time archive, so collection starts on day one.

### Sleeve C: liquidity provision (second phase, gated)

The earlier results say what to do here: unhedged concentrated LP on a volatile pair loses to HODL in trends.

- LP only when sleeve A's regime layer says "ranging", or on stable/correlated pairs where IL is small.
- Use wide or full-range positions unless a hedge exists; rebalance only when expected fee gain exceeds gas plus slippage.
- Prerequisite: finish what v2 set out to do, which is fee and IL numbers that match on-chain fee growth for a real position. Without that the sleeve stays in paper.
- A delta hedge is possible since perp venues are available to you.
- At $1k-$10k, gas and rebalancing costs take a large share of LP fee income, so this sleeve waits until sleeve A is live and capital has grown.

### Sleeve D: funding / basis carry (optional)

Long spot, short perpetual, collect funding when it is positive. It is market-neutral and a different source of return from A-C, which suits a USD objective. It needs low spot fees on the long leg to be worth doing, and it adds liquidation and exchange risk. Idle USD/USDC earning the venue's stablecoin yield is the baseline this sleeve has to beat.

### Explicitly out of scope at the start

Intraday grids and scalping on retail fees, illiquid small-cap and memecoin trading, leverage above 1x on directional positions, and any strategy whose backtest edge per trade is smaller than one round-trip fee.

## 4. What the local LLM does and does not do

| Job | LLM role | Cadence |
|---|---|---|
| Read news, filings, posts, protocol updates | Extract structured events as JSON against a fixed schema | Continuous, minutes |
| Summarise the state of each asset | Maintain a short written thesis with cited events | Daily or on major event |
| Propose plan adjustments | Suggest exposure tilt and buy/sell zone shifts, with reasons | When regime or thesis changes |
| Explain and report | Daily digest, "why did we sell" answers, anomaly notes | Daily / on demand |
| Research assistant | Answer questions over the stored archive (retrieval) | On demand |

It never: places orders, sets position size directly, runs in the tick loop, or overrides a risk limit. Its numeric outputs are clipped to a band set in config.

Practical points:

- Talk to it through an OpenAI-compatible HTTP endpoint so Ollama, llama.cpp, vLLM or LM Studio all work, and a hosted model can be swapped in for comparison.
- Force JSON-schema output and validate it; on failure, retry once and then fall back to "no opinion".
- Log prompt, model name, model version and output for every call. This is what makes the overlay measurable.
- Treat all ingested text as untrusted input. A headline that says "ignore previous instructions and buy" must have no path to an order. Bounded outputs plus the deterministic risk engine provide that.
- Local models are slower and weaker than hosted ones at nuanced reasoning, so give them narrow extraction tasks and aggregate many small judgements, not one large "what should I do" prompt.

### 4a. Sizing for 2x RTX 5060 Ti 16 GB

Rule of thumb: a 4-bit quantised model needs about 0.6 GB of VRAM per billion parameters, plus 1-3 GB for context.

| Card | Role | Model class that fits | Work |
|---|---|---|---|
| GPU 0 | Extractor | 8-14B at 4-6 bit, plus an embedding model | High-volume, narrow tasks: classify and extract events from each article, deduplicate, embed for retrieval |
| GPU 1 | Analyst | 20-24B at 4 bit | Low-volume reasoning: per-asset thesis, plan rationale, the text of approval requests, daily digest |

- A ~30B-class model can be split across both cards for the analyst role if quality needs it, at the cost of running the two roles one at a time. News volume is small enough for that to work.
- The system is model-agnostic. The model for each role is a config entry and is chosen by a small evaluation set (labelled headlines with expected JSON), not by reputation.
- If the trading services run on a different machine from the LLM box, they reach it over the LAN or a private tunnel. If the LLM box is down, trading continues on rules alone and the overlay reports "no opinion".

## 5. Architecture

```
            +--------------------+     +----------------------+
 exchanges  |  Market data       |     |  Media / on-chain    |  RSS, APIs,
 on-chain ->|  collectors        |     |  collectors          |<- funding, flows
            +---------+----------+     +----------+-----------+
                      |                           |
                      v                           v
            +------------------------------------------------+
            |  Point-in-time store (bars, events, documents) |
            +----------+----------------------+--------------+
                       |                      |
                       v                      v
            +------------------+     +----------------------+
            | Features & regime|     |  LLM service (local) |
            +---------+--------+     |  extract / thesis    |
                      |              +----------+-----------+
                      v                         |
            +------------------------------------------------+
            |  Strategy sleeves  ->  trade plans (targets)   |
            +----------------------+-------------------------+
                                   v
            +------------------------------------------------+
            |  Portfolio allocator + risk engine (hard veto) |
            +----------------------+-------------------------+
                                   v
            +------------------------------------------------+
            |  Execution: plan -> orders -> fills            |
            |  adapters: paper | CEX spot | on-chain (later) |
            +----------------------+-------------------------+
                                   v
            +------------------------------------------------+
            |  Ledger & reconciliation  ->  dashboard/alerts |
            +------------------------------------------------+

   Backtester and paper trader drive the same strategy, risk and
   execution code with replayed data and simulated fills.
```

Component notes:

- **Store.** Parquet files plus DuckDB for research, SQLite or Postgres for live state and the ledger. Every row carries `event_time` and `known_at`.
- **Strategies** are pure functions: (history up to now, current positions, config) to a trade plan. No I/O inside them. This is what makes backtest and live identical.
- **Risk engine** enforces: max exposure per asset and in total, max order size, max daily and weekly loss, max orders per day, price sanity bands against a second data source, stale-data halt, and a manual kill switch.
- **Execution** is a state machine per order with idempotent client order IDs, restart recovery, and reconciliation of balances against the venue on every start and on a timer.
- **Ledger** is the single source of truth for PnL, fees and holdings. Money values use `Decimal` (v2 already does this).
- **Secrets.** Exchange API keys with trade permission only, no withdrawal permission. On-chain keys, when sleeve C arrives, in a dedicated hot wallet holding only that sleeve's capital.
- **Stack.** Python 3.12, one repo, one package, typed interfaces, pytest, config in YAML, Docker Compose for the long-running services. This matches what v2 established.

### 5a. Approval workflow

The risk engine never sends an order that lacks a valid approval, except for exits you approved in advance.

**What you approve.** A trade plan, not each individual order. One approval covers the entry ladder, the sell targets and the stop. The bot then works orders inside those bounds. Anything outside them needs a new request. This keeps the number of notifications low.

**What a request contains.**

```
PROPOSAL #0142  ETH  open long              expires in 4h
Action   buy up to $600 (12% of account) in 3 limit orders: $X1 / $X2 / $X3
Exits    take profit 1/3 at $T1, 1/3 at $T2, trail the rest; stop on daily close below $S
Why      trend: regime turned UP on the daily (evidence listed)
         news:  2 supportive events, 0 negative (linked)
         LLM:   two-sentence thesis, with sources
Risk     worst case at stop -$54 (1.1% of account); fees about $0.40; total exposure 35% -> 47%
History  this setup: N past signals, hit rate, average result (out-of-sample)
[Approve] [Approve half size] [Deny] [Deny + reason] [Snooze 1h]
```

**Rules.**

- No reply before expiry means deny. Nothing that adds risk happens on silence.
- A request is void if price moves more than a set distance from the proposal before you answer; the bot re-proposes at the new price.
- An approval can shrink a trade and can never enlarge it.
- Stops and invalidation exits are approved with the plan, so they execute without waiting for you. This is the one place I recommend against asking each time, because a stop that waits for a reply is not a stop.
- Every proposal is recorded with its outcome, including denied and expired ones, which are tracked in a shadow ledger. After a few months this shows whether your vetoes added or cost money, and which kinds of proposals deserve auto-approval.
- A `halt` command cancels open orders and stops new proposals. A `flatten` command closes everything.

**Channel.** A chat bot with buttons (Telegram, Discord or Slack), a phone push service, or a web dashboard. The channel is an adapter. Only your account ID is accepted, each button carries a one-time token, and the channel can approve or deny but cannot change sizes or prices.

**Autonomy ladder.**

| Level | Behaviour | Move up when |
|---|---|---|
| 0 | Paper only. Proposals are sent for information; you approve or deny and nothing trades. | Four weeks of proposals that read well and reconcile |
| 1 | Live. Every plan needs approval. | A set number of approved plans with results tracking the backtest |
| 2 | Auto-approve plans under a size limit and all risk-reducing actions. Notify after the fact. | Shadow ledger shows approvals are near-automatic |
| 3 | Fully automatic inside risk limits. Daily digest only. | Your call |

Level 0 doubles as the paper-trading phase, so the approval flow is exercised from the first day.

## 6. Validation protocol

A strategy is promoted only if it passes all of the following.

1. Costs: the venue's real fee tier for your volume, plus a slippage model, plus gas for on-chain actions.
2. Baselines: reported next to HODL, weekly DCA, and a 200-day moving-average rule on the same period and capital.
3. Walk-forward: parameters chosen on one window and scored on the next, rolled through the full history. The reported number is the out-of-sample one.
4. Robustness: results hold when parameters are moved 20% either way and when the start date is shifted. No single cycle may supply most of the profit.
5. Sample size: a stated minimum number of independent trades before a result counts.
6. Paper: at least four weeks live-paper with fills that match the backtest's assumptions within a tolerance.
7. Small live: a fixed small allocation with tight loss limits; scale only after live results track paper.

The backtester prints a single verdict line per run (pass/fail against these gates), so a result cannot be read optimistically.

## 7. Roadmap

| Phase | Deliverable | Exit test |
|---|---|---|
| 0 | Decisions in section 10; new repo skeleton; data collectors running (prices and news archive start accumulating) | Collectors run unattended for a week |
| 1 | Store, backtester, baselines, sleeve A v1, verdict report | Sleeve A passes gates 1-5, or is rejected cheaply |
| 2 | Execution with paper adapter, risk engine, ledger, approval workflow at level 0, dashboard | Four weeks of paper proposals with clean reconciliation |
| 3 | LLM service: event extraction, thesis, daily digest, shadow scoring | Overlay scores are tracked; no sizing influence yet |
| 4 | Live adapter for the chosen venue, small capital, approval level 1 | Live tracks paper within tolerance |
| 5 | Overlay allowed to tilt sizes within its band; sleeve C in paper with verified fee math; sleeve D if a venue is available | Each passes its own gates |

Phases 1 and 3 can overlap. Phase 0-2 is roughly one to two Devin sessions each; the calendar time is set by the paper-trading and shadow periods, not by coding.

## 8. Main risks

- **No edge is found.** Possible and worth knowing early. The fallback is a disciplined, rules-based accumulation and de-risking tool, which still has value.
- **Overfitting.** Addressed by section 6, and by keeping the parameter count small.
- **LLM noise or manipulation.** Bounded influence, shadow period, untrusted-input handling.
- **Operational.** Exchange outages, API changes, key compromise, a bug that loops orders. Addressed by the risk engine, withdrawal-disabled keys, reconciliation and the kill switch.
- **Smart-contract and bridge risk** for sleeve C. Limited by a small dedicated wallet.
- **Tax and regulatory.** The ledger should export every fill in a form your tax tooling accepts. Venue choice depends on where you live.

## 9. What to reuse

| From | Reuse | Leave behind |
|---|---|---|
| eth-bot | Supervisor/sleeve concept, `BotInterface` shape, allocation throttle, LLM digest, Coinbase REST/WS code as reference, annotated ETH history | Flat file layout, versioned filenames, high-turnover bots, fee assumptions, hard-coded dwell logic |
| liquidity-bot | Findings in `next_steps.md`, hypothesis log format, fee-leakage rebalance gate idea, dashboard ideas | The harness and accounting code |
| liquidity-bot-v2 | Package layout, `Decimal` and atomic-write rules, registry, IL math and tests, Multicall `slot0` price reader, Sugar pool discovery | Volume-based fee estimate as the final answer; fetch scripts tied to rate-limited free APIs |

## 10. Open decisions

Settled: stops defined in an approved plan execute without a second approval; approvals go through a web dashboard first, with other notification channels in a later phase; the system halts and waits for you at a 35% account drawdown from the high-water mark.

Still open:

1. **Execution venue.** Which low-fee venue to build the first live adapter for (section 3a). Paper trading does not depend on this, so it can wait until phase 2.
2. **Where the trading services run.** On the LLM box, or elsewhere with the LLM reached over the network.
3. **Media sources.** Any you already trust or pay for.
4. **Repository.** A new repo for the platform, with the three earlier repos kept as references.


## UX decisions (2026-11-20)

- Desktop browser first; a simpler mobile view is acceptable.
- Runs on the home Ubuntu LLM box. Remote access from outside the home network is phase 2.
- Approval requests expire after 6 hours.
- Liveness feedback is a hard requirement, because earlier bots went quiet and it was unclear whether they were idle or broken:
  - A status strip on every screen: running or halted, time since the last check, time to the next decision.
  - Every decision cycle logs one line with its reason, including "no trade" outcomes.
  - Periodic heartbeat entries in the activity log and a daily digest even when nothing traded.
  - A silence alarm when any service misses its check or a scheduled decision does not run.
- Mockup: `docs/mockup.html`.


## 3d. Study 3: frequent small-movement trading, and venue (2026-11-20)

Five-minute Binance spot bars, BTC and ETH, April 2018 to October 2026, 2022-2026 held out. Limit orders count as filled only when price trades through them. Code: `research/intraday.py`, `research/intraday2.py`.

| Idea (traded only while the daily trend is on, Binance.US fees) | Yearly return 2022-2026 | Worst loss | Return beyond simply holding in the uptrend |
|---|---|---|---|
| Simply hold while the daily trend is on (comparison) | 19.3% | -32% | not applicable |
| Buy small dips (1 to 3 hourly swings), sell the bounce | -20% to -2% | -15% to -67% | negative in all 18 settings |
| Ping-pong grid, 0.25% to 4% spacing | 2.5% to 12.4% | -3% to -30% | none (-9 to +1 points, none significant) |
| Hourly breakout or moving-average crossover, 6 hours to 16 days | -6% to 21% | -22% to -41% | none significant; the slowest did best |
| Bid about 2% under the price for sudden drops (4 hourly swings), about 15 fills a year per asset | 2.6% to 4.8% | -5% to -6% | +2.4 to +4.2 points in all 6 settings, borderline significance |

- Small-movement trades lose before fees: the average trade on 1 to 3 swing dips made -2 to -10 hundredths of a percent with a 0% maker fee, because a resting bid is filled mostly when price keeps falling.
- Fast settings picked on 2018-2021 failed afterwards (24-hour breakout: 63% a year, then -6%).
- At Coinbase fees no uptrend-only version returned more than 1% a year.
- Waiting for a dip on the trend sleeve's own entries cost more than buying at the open, because the days with no dip are the days price ran away. A limit order at the open price filled almost always and saves the 0.04% taker cost.
- Decision: the system evaluates every 5 minutes and logs what it sees, but the only frequent-trading idea carried forward is the sudden-drop bid, as a paper-only experimental sleeve (sleeve E) using idle cash. Not modelled: Binance.US's own prices and outages during sudden drops.

Venue: Binance.US lists 0% maker and 0.02% taker on all pairs (its fee page, read 2026-11-20). Its live BTC/USD and ETH/USD books showed a 0.3 to 1.5 hundredths-of-a-percent spread and $24k to $85k within 0.1% of the price. It is not offered in every US state; the owner must confirm availability. Fallback: Hyperliquid spot at 0.04% / 0.07% (eligibility also to be confirmed). First live adapter: Binance.US. Repository: new private `babcoccl/trader`.


## 3e. Venue for a Maine resident (2026-11-20)

Binance.US and Kraken do not serve Maine; Hyperliquid does not serve the US. Routes around location or identity checks are out of scope.

| Route open to a Maine resident | Cost per side | Trend sleeve, yearly return 2022-2026 | Notes |
|---|---|---|---|
| Alpaca crypto (chosen) | 0.15% resting, 0.25% immediate | 13.1% to 14.0% | Regulated broker built for API trading; has a paper endpoint; Maine is on its support page's list (dated May 2023), to be confirmed at sign-up |
| On-chain on Base, own wallet (Uniswap or Aerodrome 0.05% pools, cbBTC and WETH against USDC) | about 0.12% with slippage | 14.5% | No account. Adds wallet-key, smart-contract, wrapped-bitcoin and stablecoin risk; no simple resting stop |
| Crypto.com Exchange | 0.25% / 0.50% | 10.8% to 13.1% | |
| Bitstamp by Robinhood | 0.30% / 0.40% | about 12.6% | |
| Coinbase Advanced | 0.50% / 0.90% | 7.0% to 10.8% | 10.8% only if every order rests |
| Bitcoin and ether ETFs at a commission-free broker | about 0.03% plus 0.25% a year fund fee | about 10.5% | Weekday trading only costs about 2 points a year |

For reference, the same test at Binance.US's fees gave 15.3%. The sleeve turns the account over about 7 times a year, so each 0.1% of cost per side is worth about 0.9 points a year. Moving the decision time from midnight UTC to the New York close with a next-morning trade lowered the 2022-2026 result by about 2.5 points, so the result is sensitive to timing by a few points. Sleeve E (sudden-drop bids) returned -1.5% to 0.4% at Alpaca's fees and is parked. Code: `research/venue.py`.

Alpaca liquidity check (2026-11-20, its public market data): the quoted order book is deep for this account (BTC/USD about $200k and ETH/USD about $73k within 0.1% of the price; a $10,000 market order cost 0.05% to 0.06% beyond the mid price; spread 0.03% to 0.04% at every hour sampled over 28 days, weekends included, worst 0.10%; prices within 0.03% of Coinbase). Actual trading is thin: about $118k a day in BTC/USD and $38k in ETH/USD, and ETH/USD had no trade in 170 of 288 five-minute intervals on 19 November. Consequence: buy and sell immediately rather than counting on resting orders; at 0.25% plus 0.05% per side the trend sleeve's 2022-2026 result is about 12.6% to 13.1% a year. Not observed: the book during a market crash. Code: `research/alpaca_liq.py`, `research/alpaca_liq2.py`.

Bitstamp against Alpaca (2026-11-20, each venue's own pages and public market data):

| Measure | Alpaca crypto | Bitstamp (Bitstamp USA) |
|---|---|---|
| Fee, under $10,000 a month | 0.15% resting, 0.25% immediate | 0.30% resting, 0.40% immediate |
| Realistic cost per side | about 0.30% (immediate, plus spread) | 0.30% if the resting order fills, 0.40% if not |
| Trend sleeve, yearly return 2022-2026 | about 12.8% | 11.9% to 12.8% |
| Traded per day, BTC/USD and ETH/USD | about $118k and $38k | about $120m and $22m |
| Cost of a $10,000 market order beyond the mid price | 0.05% to 0.06% | under 0.003% |
| Orders within 0.1% of the price | about $200k and $73k | over $1m each |
| Maine | Listed on a support page dated May 2023 | Maine money transmitter licence listed on Bitstamp USA's legal page |
| Stop order resting on the exchange, by API | Yes (stop-limit) | No: its API documents stop orders for derivatives only, which US customers cannot use |
| Test environment | Paper endpoint | A test server is documented; access for retail accounts not confirmed |

Decided 2026-11-20: Bitstamp. The emergency stop is run by the application, with no stop resting on the exchange; the owner accepts that positions are unprotected while the home box is offline. Alpaca is the fallback. Code: `research/bitstamp_liq.py`.

Robinhood Crypto reviewed (2026-11-21, its fee schedule dated 2026-11-02 and its help pages on order routing, fee tiers and the trading API):

| Way of trading on Robinhood | Cost per side under $10,000 a month | Trend sleeve, yearly return 2022-2026 |
|---|---|---|
| API v1, or the app's default (market-maker routing) | No commission, but 0.95% of the order value is in the spread | 6.7% |
| API v2 (exchange routing) | 0.95%: API orders pay the standard rate until the reduced rate is rolled out to them | 6.7% |
| App or Legend, resting limit order (exchange routing) | 0.50% | 10.9% |
| Bitstamp directly (chosen) | 0.30% resting, 0.40% immediate | 12.8%, 11.9% |

Robinhood sends resting limit orders to Bitstamp's own order book, so it is the same market at a higher fee. Robinhood's tiers fall below Bitstamp's only from $50,000 of trading a month (0.25% / 0.125%), which at about 7 turns a year needs an account near $90,000. Its API does accept stop-loss and stop-limit orders, which Bitstamp's spot API does not. Decision unchanged: Bitstamp. Bitstamp's fee schedule taking effect 2026-11-24 has the same trading tiers as the current one.
