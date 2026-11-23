# Paper trading (spec 5.1)

Paper mode is the default (`TRADER_MODE` unset or `paper`). The stack is
the regular Compose one — see `docs/RUNBOOK.md` for the Ubuntu box.

## Starting the account

The account needs its $5,000 starting balance once. The `watchdog`
service seeds it automatically on its first pass (it writes a `deposit`
event and a `balances` row), so a normal `docker compose up -d` is
enough. To seed by hand instead, inside the stack or a venv:

```
# Linux / macOS
python -m trader.ops.paper_report seed
# Windows PowerShell (same command)
python -m trader.ops.paper_report seed
```

The seed is idempotent: any existing paper deposit (including one you
made yourself) means it does nothing.

## The weekly report

In paper mode the watchdog writes one `weekly_report` event every week,
on Monday at `digest_time_utc` (default 00:30 UTC), covering the
Monday–Sunday week just ended. It waits for Sunday's daily candle, for at
most a day, and catches up after a restart on any later weekday. The
report always reads the paper account. To write one on demand for an arbitrary window:

```
# Linux / macOS
python -m trader.ops.paper_report report 2026-11-16 2026-11-23
# Windows PowerShell (same command)
python -m trader.ops.paper_report report 2026-11-16 2026-11-23
```

(`end` is exclusive — the example covers Monday 16th through Sunday
22nd.) The report lives in the `events` table, so it shows on the
dashboard's Reports screen ("Weekly paper reports"), on the Activity
screen and in the log; the Markdown body and all numbers are in the
event's `details`. No report is written for a week that ended at or
before the account's first deposit — the account did not exist during
it, so the first report after a fresh start covers the seed week.

Each report contains:

- **Account**: paper equity at the start and end of the week.
- **Tracking gap**: paper return minus the return of
  `backtest.engine.run` over the same days on the stored daily candles
  at Bitstamp costs, in percentage points. This is the number the
  promotion gate (spec 5.3) reads; it is `null` when snapshots or
  candles cannot cover the window.
- **Uptime**: share of the week's minutes that held at least one
  heartbeat, per service.
- **Alarms**: every `alarm` and `alarm_cleared` event of the week.
- **Approvals**: minutes from proposal to decision for every plan
  decided in the week, plus the median and slowest.

## Changing the asset list

`TRADER_SYMBOLS` can add SOL-USD and XRP-USD to the paper account
(see `docs/SETUP.md`, "Paper trading more assets"). Changing the asset
list restarts the four-week count of spec 5.3: the promotion gate
compares paper against backtest over the same four weeks, and the
weeks before the change traded a different portfolio, so the count
starts over at the change.
