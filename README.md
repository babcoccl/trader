# trader

A single-user application that trades BTC and ETH spot on a trend-following rule set, asks its owner to approve each plan in a web dashboard, and reports continuously on what it is doing. It runs on the owner's home Ubuntu box.

Specs 0.1 to 5.2 are built: the application runs in paper mode on Bitstamp's public prices and needs no exchange account or API key for that. Live trading is not enabled. To install and use it, read `docs/SETUP.md`. Known gaps and the specs that close them are in `docs/REVIEW-5.2.md`.

| Path | What it is |
|---|---|
| `docs/SETUP.md` | Setup and usage guide: first start, dashboard, checks, halt and flatten, troubleshooting |
| `docs/REVIEW-5.2.md` | Review of the application after spec 5.2, with findings |
| `docs/RUNBOOK.md` | Operating the Ubuntu box: install, backup, restore, alarms |
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

Each session implements one spec from `docs/SPECS.md` and opens one pull request. The order and dependencies are in `docs/PLAN.md` and `docs/KICKOFF.md`. Next are the review fixes in `docs/SPECS.md` section C, starting with R1.

## Development

Requires Python 3.12, Docker and Docker Compose. Every command is given for a Unix shell and for Windows PowerShell.

| Purpose | Linux / macOS | Windows PowerShell |
|---|---|---|
| Start stack | `docker compose up -d` | `docker compose up -d` |
| Stop stack | `docker compose down` | `docker compose down` |
| Install | `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"` | `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -e ".[dev]"` |
| Checks | `ruff check . && ruff format --check . && mypy src/trader && pytest` | `ruff check .; ruff format --check .; mypy src/trader; pytest` |
| Migrate | `alembic upgrade head` | `alembic upgrade head` |
| Health check | `curl http://localhost:8000/health` | `Invoke-RestMethod http://localhost:8000/health` |
| Pre-commit hooks | `pre-commit install` | `pre-commit install` |

The Compose files run `alembic upgrade head` themselves through a `migrate` service before the other services start; the Migrate row is for a Python environment outside Compose.

Configuration comes from `TRADER_*` environment variables (see `.env.example`). `mode` defaults to `paper`; live order placement additionally requires `TRADER_LIVE_ENABLED=true`.

## Expected result, and its limits

On 2018-2026 daily data the chosen rules returned about 12% a year over 2022-2026 at Bitstamp's fees, with a worst loss near 24% in that period and 31% over the whole period. That is a backtest on one exchange's prices, sensitive by a few points to the trading band and the time of day, and it is not a forecast. The paper-trading gate in phase 5 exists to test it before real money is used.
