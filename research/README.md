# Research

The scripts that produced the study results in `docs/DESIGN.md`. They are reference material: dense, untyped, and excluded from lint, type checks and the Docker image. Do not import them from `src/`.

## What is committed and verified

`fixtures/` holds everything the application's tests need, so no download is required for specs 1.2 and 1.3:

| File | Contents |
|---|---|
| `fixtures/btc_eth_daily.csv` | Binance BTCUSDT and ETHUSDT daily open and close, 2017-08-17 to 2026-11-19 |
| `fixtures/expected_signals.csv` | Every rule, the ensemble, annual volatility and target weight per day and asset, from `bt.py` |
| `fixtures/expected_backtest.json` | Backtest statistics from `bt.py` for two cost models and four trading bands |
| `fixtures/expected_equity_study_040_band_0.2.csv` | Daily equity for the headline run |

`check_spec.py` re-implements specs 1.2 and 1.3 from their text using only these files and compares with the expected values. It was run on 2026-11-21 and matched to 5e-12 on signals and 3e-14 on backtest statistics:

```
cd research
python check_spec.py
```

## Rebuilding the full study from public data

These steps were run once, incrementally, while the studies were made. They were re-run end to end from a clean checkout on 2026-11-21 (spec 0.2): every step worked as written, `make_fixtures.py` regenerated `fixtures/` with no diff, and `check_spec.py` matched. About 200 MB of downloads into `research/data/` (git-ignored).

Linux / macOS:

```
cd research
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
mkdir -p data && curl -L -o data/exchangeInfo.json https://data-api.binance.vision/api/v3/exchangeInfo
python fetch.py      # daily candles for every USDT pair -> data/klines/
python panel.py      # open/close/volume panels, liquidity ranks -> data/*.parquet, data/ever_top30.json
python funding.py    # Binance perpetual funding history -> data/funding/
python fetch2.py     # BTC and ETH perpetual daily candles, 3-month Treasury bill rate
python hl.py         # Hyperliquid funding history
python fetch3.py     # BTC and ETH 5-minute candles -> data/m5/
```

Windows PowerShell:

```
cd research
python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -r requirements.txt
New-Item -ItemType Directory -Force data | Out-Null; curl.exe -L -o data/exchangeInfo.json https://data-api.binance.vision/api/v3/exchangeInfo
python fetch.py; python panel.py; python funding.py; python fetch2.py; python hl.py; python fetch3.py
```

Then, in either shell:

| Script | Study |
|---|---|
| `python study.py` | Study 1: trend rules against buy-and-hold, by universe, venue cost, year; robustness checks |
| `python study2.py` | Study 2: funding carry, sizing and drawdown-limit frontier, halt simulation, bootstrap |
| `python other.py` | Study 2: other candidate strategies |
| `python intraday.py` then `python intraday2.py` | Study 3: 5-minute strategies (dip buying, grids, breakouts), and entry timing |
| `python venue.py` | Trend sleeve at each candidate venue's costs; New York-hours variant |
| `python alpaca_liq.py`, `alpaca_liq2.py`, `bitstamp_liq.py` | Live order-book and volume checks (results change with the market) |
| `python make_fixtures.py` | Regenerates `fixtures/` |

Notes:

- `bt.py` is the daily backtester; its start and end dates (2018-04-01, 2026-11-19) are constants at the top. Data downloaded later will extend past the end date and is ignored.
- The Binance global API refuses some locations; the scripts use the public `data-api.binance.vision` and `data.binance.vision` hosts instead.
- No script needs an API key.
