# Decisions

The owner's decisions, newest last. This file and `docs/SPECS.md` are authoritative; `docs/DESIGN.md` records the reasoning and the study results behind them. Do not reopen a decision in an implementation session; ask the owner.

| Date | Topic | Decision |
|---|---|---|
| 2026-11-20 | Objective | Grow USD value. The home position is cash |
| 2026-11-20 | Capital | $1,000 to $10,000 to start, more only if it proves itself |
| 2026-11-20 | Approval | Plan-level approve / reduce / deny in a web dashboard. Silence means no trade. Approval can shrink a plan, never enlarge it |
| 2026-11-20 | Stops and exits | Stops and exposure-reducing sells in an approved plan run without a second approval |
| 2026-11-20 | Notifications | Web dashboard first; other channels in a later phase |
| 2026-11-20 | Strategy | Plain average of nine daily trend rules on BTC and ETH, long only, spot, no leverage |
| 2026-11-20 | Rejected after testing | Alt-coins, shorts, perpetuals for the trend sleeve, standalone funding carry, BTC/ETH rotation, cross-sectional momentum, dip buying, ping-pong grids, hourly breakouts |
| 2026-11-20 | Drawdown halt | 35% below the account high-water mark (raised from 15% after study 2) |
| 2026-11-20 | Sizing | Volatility target 40% a year |
| 2026-11-20 | Clients | Desktop browser first; a simpler mobile view is acceptable |
| 2026-11-20 | Hosting | The owner's home Ubuntu box (2x RTX 5060 Ti 16 GB). Remote access is phase 2 |
| 2026-11-20 | Approval expiry | 6 hours; void if price moves more than 2% |
| 2026-11-20 | Liveness | Status strip on every screen, a logged reason for every decision cycle including "no trade", heartbeats, daily digest, silence alarm |
| 2026-11-20 | Evaluation cadence | Every 5 minutes, each cycle logged; trades stay rare |
| 2026-11-20 | Entry orders | One maker-only limit order at the best bid, re-priced every 5 minutes, bought at market after 1 hour. No ladder below the price |
| 2026-11-20 | Local LLM | Advisory only: extracts news events and writes rationale. Never places orders or overrides limits. Shadow mode until scored |
| 2026-11-20 | Repository | New private repo `babcoccl/trader` |
| 2026-11-20 | Sessions | The owner starts and manages the implementation sessions; specs must be detailed enough for a lightweight model |
| 2026-11-20 | Venue | Bitstamp (Bitstamp USA). The owner lives in Maine: Binance.US and Kraken do not serve Maine, Hyperliquid does not serve the US. Alpaca was rejected for thin trading |
| 2026-11-20 | Emergency stop | Run by the application every 30 seconds; no stop rests on the exchange. Positions are unprotected while the home box is offline (accepted) |
| 2026-11-20 | Sleeve E (sudden-drop bids) | Parked: it does not survive the venue's fees |
| 2026-11-21 | Robinhood Crypto | Reviewed and rejected: 0.95% per side by API at this volume, against 0.30% to 0.40% on Bitstamp directly |

## Open

| Item | Blocks | Default if not answered |
|---|---|---|
| Bitstamp account opened and verified by the owner | Spec 5.2 | Fallback venue: Alpaca crypto, then Crypto.com Exchange |
| News sources for the LLM overlay | Phase 6 | A short allow-list of RSS feeds, chosen in that phase |
| Local model server and models | Phase 6 | Chosen by the benchmark in spec 6.1 |
| Where idle cash earns yield | Later | Leave as plain USD |
