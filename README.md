# trader

A single-user application that trades BTC and ETH spot on a trend-following rule set, asks its owner to approve each plan in a web dashboard, and reports continuously on what it is doing. It runs on the owner's home Ubuntu box.

Nothing is built yet. This repo currently holds the agreed design, the implementation specs, and the research behind them.

| Path | What it is |
|---|---|
| `docs/SPECS.md` | Per-session implementation specs. Section A is the shared conventions |
| `docs/PLAN.md` | Phases, order of sessions, risks |
| `docs/DECISIONS.md` | The owner's decisions. Authoritative, with `SPECS.md` |
| `docs/DESIGN.md` | How the design was reached, including study results and rejected ideas |
| `docs/mockup.html` | Clickable dashboard mockup (open in a browser); `docs/mockup-source.html` is its source |
| `docs/studies/` | The two interactive study reports |
| `research/` | Study scripts, and `research/fixtures/` with the prices and expected values the tests compare against |
| `docs/KICKOFF.md` | The prompt for session 0.1, a template for later sessions, and the session order |
| `AGENTS.md` | Rules for agent sessions working in this repo |

## How development proceeds

Each session implements one spec from `docs/SPECS.md` and opens one pull request. Start with spec 0.1. The order and dependencies are in `docs/PLAN.md`.

## Expected result, and its limits

On 2018-2026 daily data the chosen rules returned about 12% a year over 2022-2026 at Bitstamp's fees, with a worst loss near 24% in that period and 31% over the whole period. That is a backtest on one exchange's prices, sensitive by a few points to the trading band and the time of day, and it is not a forecast. The paper-trading gate in phase 5 exists to test it before real money is used.
