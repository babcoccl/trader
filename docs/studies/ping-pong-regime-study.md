# Ping-pong regime study (2026-11-25)

Asked by the owner: which market conditions let the ping-pong booster hold
Coinbase fee tiers at the lowest cost, and how should a rolling-30-day
"smart boost" decide when to quote?

## Data and method

- Source: Coinbase Exchange public candles, `BTC-USD`, 5-minute,
  2026-09-11 to 2026-11-25 (74 days, 21600 bars). Fetched with
  `User-Agent: trader/1.0`, cached to `/tmp/btc_usd_5m_study.csv` (not committed).
- Simulator: `research/ping_pong_study.py`, run with `--csv` to replay.
  Mechanics mirror `PingPongWorker`: `order_levels = 2`,
  `order_notional_usd = $250`, maker-only levels at
  `open * (1 +/- spread_bps * (k + 1))`, counters
  `(buys_remaining, sells_remaining)` resetting when both reach zero,
  auto-seed of the initial sell inventory at the first active bar, and an
  unwind of remaining BTC at the last close so directional drift is fully
  inside Net PnL.
- Fill rule: a resting buy fills at its limit when `bar.low <= price`;
  a resting sell when `bar.high >= price`. This is the optimistic
  5-minute version of the worker's 20-second tick rule — real fills
  depend on the quote still resting when price touches it, so live
  volume and spread capture will be somewhat lower than simulated.
- Regime features are causal: daily ADX(14) (same Wilder math as
  `strategy/regime.py`) and 4h ADX(14) use only completed prior windows;
  the 4h ATR% quartiles use trailing-30-day thresholds; the 4h
  efficiency ratio uses the last 48 closed bars.
- Fees: Tier 1 maker 40 bps (climbing to $10k) and Tier 2 maker 25 bps
  (holding $10k-$50k).

## 1. Regime and spread sweep

Net PnL per $1,000 volume = spread capture minus maker fees minus
inventory drift; the primary ranking metric.

| Tier | ADX gate | Vol band | spread_bps | Volume | Active | Net PnL / $1k | Capture bps | Fee bps | Drift bps | Days to $10k | Days to $10k ($2k/d cap) | Max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Tier 1 | ADX<15 | unfiltered | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | unfiltered | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | unfiltered | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | unfiltered | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | unfiltered | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q2+Q3 Goldilocks | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q2+Q3 Goldilocks | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q2+Q3 Goldilocks | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q2+Q3 Goldilocks | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q2+Q3 Goldilocks | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q1+Q2 quiet | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q1+Q2 quiet | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q1+Q2 quiet | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q1+Q2 quiet | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q1+Q2 quiet | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q3+Q4 active | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q3+Q4 active | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q3+Q4 active | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q3+Q4 active | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<15 | Q3+Q4 active | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 1 | ADX<20 | unfiltered | 5 | $851,371 | 19% | $-4.272 | 6.2 | 40.0 | -8.9 | <1 | 5 | $3,662 |
| Tier 1 | ADX<20 | unfiltered | 10 | $348,189 | 19% | $-4.630 | 11.4 | 40.1 | -17.7 | 2 | 5 | $1,650 |
| Tier 1 | ADX<20 | unfiltered | 15 | $168,937 | 19% | $-5.606 | 16.6 | 40.2 | -32.5 | 4 | 5 | $998 |
| Tier 1 | ADX<20 | unfiltered | 25 | $55,189 | 19% | $-6.025 | 25.6 | 40.1 | -45.7 | 14 | 14 | $345 |
| Tier 1 | ADX<20 | unfiltered | 40 | $12,750 | 19% | $-11.065 | 40.8 | 40.5 | -110.9 | 59 | 59 | $157 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 5 | $569,202 | 13% | $-4.168 | 6.1 | 40.0 | -7.8 | 1 | 5 | $2,385 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 10 | $216,476 | 13% | $-4.655 | 11.1 | 40.1 | -17.6 | 3 | 5 | $1,032 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 15 | $95,236 | 13% | $-4.773 | 16.3 | 40.1 | -23.9 | 8 | 8 | $467 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 25 | $25,984 | 13% | $-4.564 | 25.2 | 40.0 | -30.9 | 29 | 29 | $119 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 40 | $6,243 | 13% | $-15.663 | 40.0 | 41.2 | -155.4 | 120 | 120 | $116 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 5 | $474,391 | 10% | $-4.447 | 6.2 | 40.0 | -10.6 | 2 | 5 | $2,135 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 10 | $187,191 | 10% | $-5.156 | 11.3 | 40.1 | -22.8 | 4 | 5 | $1,003 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 15 | $93,943 | 10% | $-6.820 | 16.3 | 40.3 | -44.2 | 8 | 8 | $692 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 25 | $32,181 | 10% | $-7.740 | 25.6 | 40.2 | -62.7 | 23 | 23 | $262 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 40 | $9,750 | 10% | $-10.128 | 41.0 | 40.8 | -101.5 | 77 | 77 | $117 |
| Tier 1 | ADX<20 | Q3+Q4 active | 5 | $378,182 | 8% | $-4.321 | 6.3 | 40.0 | -9.4 | 2 | 5 | $1,647 |
| Tier 1 | ADX<20 | Q3+Q4 active | 10 | $160,965 | 8% | $-4.167 | 11.6 | 40.0 | -13.2 | 5 | 5 | $671 |
| Tier 1 | ADX<20 | Q3+Q4 active | 15 | $74,720 | 8% | $-6.661 | 16.6 | 40.3 | -42.8 | 10 | 10 | $561 |
| Tier 1 | ADX<20 | Q3+Q4 active | 25 | $22,981 | 8% | $-9.766 | 25.5 | 40.7 | -82.5 | 33 | 33 | $256 |
| Tier 1 | ADX<20 | Q3+Q4 active | 40 | $5,000 | 8% | $-55.320 | 36.0 | 46.0 | -543.2 | 150 | 150 | $341 |
| Tier 1 | ADX<25 | unfiltered | 5 | $2,125,508 | 41% | $-4.128 | 6.3 | 40.0 | -7.6 | <1 | 5 | $8,787 |
| Tier 1 | ADX<25 | unfiltered | 10 | $928,863 | 41% | $-4.214 | 11.5 | 40.0 | -13.7 | <1 | 5 | $3,942 |
| Tier 1 | ADX<25 | unfiltered | 15 | $466,904 | 41% | $-4.206 | 16.5 | 40.0 | -18.6 | 2 | 5 | $1,964 |
| Tier 1 | ADX<25 | unfiltered | 25 | $148,887 | 41% | $-5.142 | 26.1 | 40.1 | -37.4 | 5 | 5 | $796 |
| Tier 1 | ADX<25 | unfiltered | 40 | $38,177 | 41% | $-8.433 | 41.8 | 40.7 | -85.5 | 20 | 20 | $366 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 5 | $988,433 | 21% | $-4.167 | 6.2 | 40.0 | -7.8 | <1 | 5 | $4,145 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 10 | $390,193 | 21% | $-4.510 | 11.2 | 40.1 | -16.3 | 2 | 5 | $1,799 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 15 | $178,470 | 21% | $-4.715 | 16.3 | 40.1 | -23.4 | 4 | 5 | $867 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 25 | $48,981 | 21% | $-6.423 | 25.6 | 40.3 | -49.5 | 15 | 15 | $343 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 40 | $9,949 | 21% | $-9.089 | 41.8 | 40.0 | -92.7 | 75 | 75 | $96 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 5 | $678,388 | 15% | $-4.316 | 6.2 | 40.0 | -9.3 | 1 | 5 | $2,953 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 10 | $263,176 | 15% | $-4.882 | 11.3 | 40.1 | -20.0 | 3 | 5 | $1,323 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 15 | $127,936 | 15% | $-6.132 | 16.3 | 40.2 | -37.4 | 6 | 6 | $835 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 25 | $43,192 | 15% | $-6.526 | 25.7 | 40.2 | -50.8 | 17 | 17 | $297 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 40 | $10,742 | 15% | $-11.757 | 41.0 | 40.7 | -117.9 | 70 | 70 | $140 |
| Tier 1 | ADX<25 | Q3+Q4 active | 5 | $1,447,588 | 26% | $-4.132 | 6.4 | 40.0 | -7.7 | <1 | 5 | $5,995 |
| Tier 1 | ADX<25 | Q3+Q4 active | 10 | $664,921 | 26% | $-4.213 | 11.6 | 40.0 | -13.7 | 1 | 5 | $2,829 |
| Tier 1 | ADX<25 | Q3+Q4 active | 15 | $340,938 | 26% | $-4.180 | 16.5 | 40.0 | -18.3 | 2 | 5 | $1,425 |
| Tier 1 | ADX<25 | Q3+Q4 active | 25 | $104,935 | 26% | $-5.163 | 26.3 | 40.2 | -37.7 | 7 | 7 | $571 |
| Tier 1 | ADX<25 | Q3+Q4 active | 40 | $28,164 | 26% | $-10.485 | 41.4 | 40.9 | -105.3 | 27 | 27 | $347 |
| Tier 1 | ADX<30 | unfiltered | 5 | $2,376,118 | 47% | $-4.112 | 6.3 | 40.0 | -7.4 | <1 | 5 | $9,770 |
| Tier 1 | ADX<30 | unfiltered | 10 | $1,129,295 | 47% | $-4.207 | 11.6 | 40.0 | -13.7 | <1 | 5 | $4,782 |
| Tier 1 | ADX<30 | unfiltered | 15 | $580,857 | 47% | $-4.212 | 16.6 | 40.0 | -18.8 | 1 | 5 | $2,448 |
| Tier 1 | ADX<30 | unfiltered | 25 | $192,842 | 47% | $-4.792 | 26.4 | 40.0 | -34.3 | 4 | 5 | $924 |
| Tier 1 | ADX<30 | unfiltered | 40 | $53,872 | 47% | $-6.289 | 41.5 | 40.0 | -64.4 | 14 | 14 | $343 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 5 | $1,046,434 | 22% | $-4.157 | 6.2 | 40.0 | -7.7 | <1 | 5 | $4,376 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 10 | $414,194 | 22% | $-4.477 | 11.2 | 40.1 | -15.9 | 2 | 5 | $1,893 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 15 | $188,473 | 22% | $-4.663 | 16.3 | 40.1 | -22.9 | 4 | 5 | $905 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 25 | $50,980 | 22% | $-6.365 | 25.5 | 40.3 | -48.8 | 15 | 15 | $351 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 40 | $10,948 | 22% | $-8.788 | 41.7 | 40.0 | -89.5 | 69 | 69 | $102 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 5 | $817,377 | 18% | $-4.276 | 6.2 | 40.0 | -8.9 | <1 | 5 | $3,521 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 10 | $322,163 | 18% | $-4.760 | 11.2 | 40.1 | -18.7 | 2 | 5 | $1,572 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 15 | $150,925 | 18% | $-5.877 | 16.2 | 40.2 | -34.8 | 5 | 5 | $938 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 25 | $48,174 | 18% | $-6.627 | 25.6 | 40.2 | -51.7 | 16 | 16 | $332 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 40 | $11,739 | 18% | $-11.371 | 40.0 | 40.6 | -113.1 | 64 | 64 | $146 |
| Tier 1 | ADX<30 | Q3+Q4 active | 5 | $1,689,282 | 29% | $-4.149 | 6.5 | 40.0 | -7.9 | <1 | 5 | $7,039 |
| Tier 1 | ADX<30 | Q3+Q4 active | 10 | $806,366 | 29% | $-4.203 | 11.8 | 40.0 | -13.8 | <1 | 5 | $3,420 |
| Tier 1 | ADX<30 | Q3+Q4 active | 15 | $431,902 | 29% | $-4.180 | 16.7 | 40.0 | -18.5 | 2 | 5 | $1,807 |
| Tier 1 | ADX<30 | Q3+Q4 active | 25 | $143,907 | 29% | $-4.607 | 26.6 | 40.0 | -32.7 | 5 | 5 | $664 |
| Tier 1 | ADX<30 | Q3+Q4 active | 40 | $41,861 | 29% | $-7.208 | 41.4 | 40.0 | -73.5 | 18 | 18 | $314 |
| Tier 1 | unfiltered | unfiltered | 5 | $2,405,215 | 65% | $-4.085 | 6.2 | 40.0 | -7.1 | <1 | 5 | $9,826 |
| Tier 1 | unfiltered | unfiltered | 10 | $1,809,682 | 65% | $-4.172 | 11.9 | 40.0 | -13.6 | <1 | 5 | $7,550 |
| Tier 1 | unfiltered | unfiltered | 15 | $993,772 | 65% | $-4.184 | 16.9 | 40.0 | -18.8 | <1 | 5 | $4,158 |
| Tier 1 | unfiltered | unfiltered | 25 | $357,747 | 65% | $-4.662 | 26.8 | 40.1 | -33.4 | 2 | 5 | $1,668 |
| Tier 1 | unfiltered | unfiltered | 40 | $108,568 | 65% | $-5.319 | 42.3 | 40.1 | -55.4 | 7 | 7 | $584 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 5 | $1,189,446 | 25% | $-4.127 | 6.2 | 40.0 | -7.5 | <1 | 5 | $4,935 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 10 | $472,207 | 25% | $-4.391 | 11.2 | 40.1 | -15.1 | 2 | 5 | $2,112 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 15 | $214,477 | 25% | $-4.565 | 16.2 | 40.1 | -21.8 | 3 | 5 | $1,005 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 25 | $56,969 | 25% | $-6.296 | 25.6 | 40.3 | -48.2 | 13 | 13 | $384 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 40 | $11,946 | 25% | $-8.545 | 41.5 | 40.0 | -87.0 | 63 | 63 | $108 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 5 | $930,382 | 22% | $-4.238 | 6.2 | 40.0 | -8.5 | <1 | 5 | $3,968 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 10 | $358,159 | 22% | $-4.694 | 11.2 | 40.1 | -18.1 | 2 | 5 | $1,719 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 15 | $167,934 | 22% | $-5.636 | 16.2 | 40.2 | -32.4 | 4 | 5 | $997 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 25 | $52,179 | 22% | $-6.328 | 25.6 | 40.2 | -48.7 | 14 | 14 | $343 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 40 | $12,750 | 22% | $-9.899 | 40.0 | 40.6 | -98.5 | 59 | 59 | $147 |
| Tier 1 | unfiltered | Q3+Q4 active | 5 | $2,354,267 | 43% | $-4.150 | 6.5 | 40.0 | -8.0 | <1 | 5 | $9,769 |
| Tier 1 | unfiltered | Q3+Q4 active | 10 | $1,449,758 | 43% | $-4.162 | 12.0 | 40.0 | -13.6 | <1 | 5 | $6,035 |
| Tier 1 | unfiltered | Q3+Q4 active | 15 | $826,814 | 43% | $-4.171 | 17.0 | 40.0 | -18.8 | <1 | 5 | $3,449 |
| Tier 1 | unfiltered | Q3+Q4 active | 25 | $305,803 | 43% | $-4.594 | 27.0 | 40.1 | -32.9 | 2 | 5 | $1,405 |
| Tier 1 | unfiltered | Q3+Q4 active | 40 | $94,553 | 43% | $-5.667 | 42.3 | 40.1 | -58.8 | 8 | 8 | $558 |
| Tier 2 | ADX<15 | unfiltered | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | unfiltered | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | unfiltered | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | unfiltered | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | unfiltered | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q2+Q3 Goldilocks | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q2+Q3 Goldilocks | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q2+Q3 Goldilocks | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q2+Q3 Goldilocks | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q2+Q3 Goldilocks | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q1+Q2 quiet | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q1+Q2 quiet | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q1+Q2 quiet | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q1+Q2 quiet | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q1+Q2 quiet | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q3+Q4 active | 5 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q3+Q4 active | 10 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q3+Q4 active | 15 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q3+Q4 active | 25 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<15 | Q3+Q4 active | 40 | $0 | 0% | n/a | n/a | n/a | n/a | n/a | n/a | $0 |
| Tier 2 | ADX<20 | unfiltered | 5 | $851,371 | 19% | $-2.771 | 6.2 | 25.0 | -8.9 | <1 | 5 | $2,385 |
| Tier 2 | ADX<20 | unfiltered | 10 | $348,189 | 19% | $-3.127 | 11.4 | 25.0 | -17.7 | 2 | 5 | $1,128 |
| Tier 2 | ADX<20 | unfiltered | 15 | $168,937 | 19% | $-4.099 | 16.6 | 25.1 | -32.5 | 4 | 5 | $745 |
| Tier 2 | ADX<20 | unfiltered | 25 | $55,189 | 19% | $-4.519 | 25.6 | 25.1 | -45.7 | 14 | 14 | $264 |
| Tier 2 | ADX<20 | unfiltered | 40 | $12,750 | 19% | $-9.546 | 40.8 | 25.3 | -110.9 | 59 | 59 | $143 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 5 | $569,202 | 13% | $-2.667 | 6.1 | 25.0 | -7.8 | 1 | 5 | $1,532 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 10 | $216,476 | 13% | $-3.152 | 11.1 | 25.0 | -17.6 | 3 | 5 | $707 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 15 | $95,236 | 13% | $-3.270 | 16.3 | 25.1 | -23.9 | 8 | 8 | $324 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 25 | $25,984 | 13% | $-3.064 | 25.2 | 25.0 | -30.9 | 29 | 29 | $83 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 40 | $6,243 | 13% | $-14.119 | 40.0 | 25.7 | -155.4 | 120 | 120 | $111 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 5 | $474,391 | 10% | $-2.945 | 6.2 | 25.0 | -10.6 | 2 | 5 | $1,423 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 10 | $187,191 | 10% | $-3.651 | 11.3 | 25.1 | -22.8 | 4 | 5 | $722 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 15 | $93,943 | 10% | $-5.308 | 16.3 | 25.2 | -44.2 | 8 | 8 | $551 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 25 | $32,181 | 10% | $-6.231 | 25.6 | 25.2 | -62.7 | 23 | 23 | $215 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 40 | $9,750 | 10% | $-8.599 | 41.0 | 25.5 | -101.5 | 77 | 77 | $107 |
| Tier 2 | ADX<20 | Q3+Q4 active | 5 | $378,182 | 8% | $-2.820 | 6.3 | 25.0 | -9.4 | 2 | 5 | $1,079 |
| Tier 2 | ADX<20 | Q3+Q4 active | 10 | $160,965 | 8% | $-2.667 | 11.6 | 25.0 | -13.2 | 5 | 5 | $430 |
| Tier 2 | ADX<20 | Q3+Q4 active | 15 | $74,720 | 8% | $-5.149 | 16.6 | 25.2 | -42.8 | 10 | 10 | $453 |
| Tier 2 | ADX<20 | Q3+Q4 active | 25 | $22,981 | 8% | $-8.241 | 25.5 | 25.4 | -82.5 | 33 | 33 | $222 |
| Tier 2 | ADX<20 | Q3+Q4 active | 40 | $5,000 | 8% | $-53.596 | 36.0 | 28.7 | -543.2 | 150 | 150 | $334 |
| Tier 2 | ADX<25 | unfiltered | 5 | $2,125,508 | 41% | $-2.627 | 6.3 | 25.0 | -7.6 | <1 | 5 | $5,599 |
| Tier 2 | ADX<25 | unfiltered | 10 | $928,863 | 41% | $-2.713 | 11.5 | 25.0 | -13.7 | <1 | 5 | $2,549 |
| Tier 2 | ADX<25 | unfiltered | 15 | $466,904 | 41% | $-2.706 | 16.5 | 25.0 | -18.6 | 2 | 5 | $1,265 |
| Tier 2 | ADX<25 | unfiltered | 25 | $148,887 | 41% | $-3.638 | 26.1 | 25.1 | -37.4 | 5 | 5 | $574 |
| Tier 2 | ADX<25 | unfiltered | 40 | $38,177 | 41% | $-6.907 | 41.8 | 25.4 | -85.5 | 20 | 20 | $310 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 5 | $988,433 | 21% | $-2.666 | 6.2 | 25.0 | -7.8 | <1 | 5 | $2,662 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 10 | $390,193 | 21% | $-3.008 | 11.2 | 25.0 | -16.3 | 2 | 5 | $1,214 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 15 | $178,470 | 21% | $-3.212 | 16.3 | 25.1 | -23.4 | 4 | 5 | $601 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 25 | $48,981 | 21% | $-4.911 | 25.6 | 25.2 | -49.5 | 15 | 15 | $270 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 40 | $9,949 | 21% | $-7.589 | 41.8 | 25.0 | -92.7 | 75 | 75 | $83 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 5 | $678,388 | 15% | $-2.815 | 6.2 | 25.0 | -9.3 | 1 | 5 | $1,936 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 10 | $263,176 | 15% | $-3.378 | 11.3 | 25.1 | -20.0 | 3 | 5 | $928 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 15 | $127,936 | 15% | $-4.623 | 16.3 | 25.2 | -37.4 | 6 | 6 | $644 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 25 | $43,192 | 15% | $-5.019 | 25.7 | 25.1 | -50.8 | 17 | 17 | $233 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 40 | $10,742 | 15% | $-10.233 | 41.0 | 25.4 | -117.9 | 70 | 70 | $125 |
| Tier 2 | ADX<25 | Q3+Q4 active | 5 | $1,447,588 | 26% | $-2.632 | 6.4 | 25.0 | -7.7 | <1 | 5 | $3,824 |
| Tier 2 | ADX<25 | Q3+Q4 active | 10 | $664,921 | 26% | $-2.712 | 11.6 | 25.0 | -13.7 | 1 | 5 | $1,832 |
| Tier 2 | ADX<25 | Q3+Q4 active | 15 | $340,938 | 26% | $-2.680 | 16.5 | 25.0 | -18.3 | 2 | 5 | $914 |
| Tier 2 | ADX<25 | Q3+Q4 active | 25 | $104,935 | 26% | $-3.657 | 26.3 | 25.1 | -37.7 | 7 | 7 | $414 |
| Tier 2 | ADX<25 | Q3+Q4 active | 40 | $28,164 | 26% | $-8.950 | 41.4 | 25.6 | -105.3 | 27 | 27 | $313 |
| Tier 2 | ADX<30 | unfiltered | 5 | $2,506,189 | 47% | $-2.637 | 6.4 | 25.0 | -7.7 | <1 | 5 | $6,640 |
| Tier 2 | ADX<30 | unfiltered | 10 | $1,129,295 | 47% | $-2.707 | 11.6 | 25.0 | -13.7 | <1 | 5 | $3,088 |
| Tier 2 | ADX<30 | unfiltered | 15 | $580,857 | 47% | $-2.712 | 16.6 | 25.0 | -18.8 | 1 | 5 | $1,577 |
| Tier 2 | ADX<30 | unfiltered | 25 | $192,842 | 47% | $-3.292 | 26.4 | 25.0 | -34.3 | 4 | 5 | $637 |
| Tier 2 | ADX<30 | unfiltered | 40 | $53,872 | 47% | $-4.789 | 41.5 | 25.0 | -64.4 | 14 | 14 | $264 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 5 | $1,046,434 | 22% | $-2.657 | 6.2 | 25.0 | -7.7 | <1 | 5 | $2,806 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 10 | $414,194 | 22% | $-2.975 | 11.2 | 25.0 | -15.9 | 2 | 5 | $1,272 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 15 | $188,473 | 22% | $-3.160 | 16.3 | 25.1 | -22.9 | 4 | 5 | $622 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 25 | $50,980 | 22% | $-4.853 | 25.5 | 25.2 | -48.8 | 15 | 15 | $276 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 40 | $10,948 | 22% | $-7.288 | 41.7 | 25.0 | -89.5 | 69 | 69 | $88 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 5 | $817,377 | 18% | $-2.776 | 6.2 | 25.0 | -8.9 | <1 | 5 | $2,295 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 10 | $322,163 | 18% | $-3.257 | 11.2 | 25.0 | -18.7 | 2 | 5 | $1,088 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 15 | $150,925 | 18% | $-4.369 | 16.2 | 25.1 | -34.8 | 5 | 5 | $711 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 25 | $48,174 | 18% | $-5.120 | 25.6 | 25.1 | -51.7 | 16 | 16 | $260 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 40 | $11,739 | 18% | $-9.848 | 40.0 | 25.4 | -113.1 | 64 | 64 | $129 |
| Tier 2 | ADX<30 | Q3+Q4 active | 5 | $1,689,282 | 29% | $-2.649 | 6.5 | 25.0 | -7.9 | <1 | 5 | $4,506 |
| Tier 2 | ADX<30 | Q3+Q4 active | 10 | $806,366 | 29% | $-2.702 | 11.8 | 25.0 | -13.8 | <1 | 5 | $2,210 |
| Tier 2 | ADX<30 | Q3+Q4 active | 15 | $431,902 | 29% | $-2.680 | 16.7 | 25.0 | -18.5 | 2 | 5 | $1,159 |
| Tier 2 | ADX<30 | Q3+Q4 active | 25 | $143,907 | 29% | $-3.107 | 26.6 | 25.0 | -32.7 | 5 | 5 | $451 |
| Tier 2 | ADX<30 | Q3+Q4 active | 40 | $41,861 | 29% | $-5.708 | 41.4 | 25.0 | -73.5 | 18 | 18 | $261 |
| Tier 2 | unfiltered | unfiltered | 5 | $3,688,238 | 65% | $-2.623 | 6.4 | 25.0 | -7.6 | <1 | 5 | $9,675 |
| Tier 2 | unfiltered | unfiltered | 10 | $1,809,682 | 65% | $-2.672 | 11.9 | 25.0 | -13.6 | <1 | 5 | $4,836 |
| Tier 2 | unfiltered | unfiltered | 15 | $993,772 | 65% | $-2.684 | 16.9 | 25.0 | -18.8 | <1 | 5 | $2,668 |
| Tier 2 | unfiltered | unfiltered | 25 | $357,747 | 65% | $-3.160 | 26.8 | 25.0 | -33.4 | 2 | 5 | $1,134 |
| Tier 2 | unfiltered | unfiltered | 40 | $108,568 | 65% | $-3.815 | 42.3 | 25.1 | -55.4 | 7 | 7 | $429 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 5 | $1,189,446 | 25% | $-2.627 | 6.2 | 25.0 | -7.5 | <1 | 5 | $3,151 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 10 | $472,207 | 25% | $-2.889 | 11.2 | 25.0 | -15.1 | 2 | 5 | $1,404 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 15 | $214,477 | 25% | $-3.062 | 16.2 | 25.0 | -21.8 | 3 | 5 | $683 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 25 | $56,969 | 25% | $-4.786 | 25.6 | 25.2 | -48.2 | 13 | 13 | $300 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 40 | $11,946 | 25% | $-7.045 | 41.5 | 25.0 | -87.0 | 63 | 63 | $92 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 5 | $930,382 | 22% | $-2.737 | 6.2 | 25.0 | -8.5 | <1 | 5 | $2,572 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 10 | $358,159 | 22% | $-3.192 | 11.2 | 25.0 | -18.1 | 2 | 5 | $1,182 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 15 | $167,934 | 22% | $-4.129 | 16.2 | 25.1 | -32.4 | 4 | 5 | $745 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 25 | $52,179 | 22% | $-4.823 | 25.6 | 25.1 | -48.7 | 14 | 14 | $265 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 40 | $12,750 | 22% | $-8.378 | 40.0 | 25.3 | -98.5 | 59 | 59 | $130 |
| Tier 2 | unfiltered | Q3+Q4 active | 5 | $2,844,848 | 43% | $-2.639 | 6.6 | 25.0 | -8.0 | <1 | 5 | $7,507 |
| Tier 2 | unfiltered | Q3+Q4 active | 10 | $1,449,758 | 43% | $-2.662 | 12.0 | 25.0 | -13.6 | <1 | 5 | $3,860 |
| Tier 2 | unfiltered | Q3+Q4 active | 15 | $826,814 | 43% | $-2.671 | 17.0 | 25.0 | -18.8 | <1 | 5 | $2,209 |
| Tier 2 | unfiltered | Q3+Q4 active | 25 | $305,803 | 43% | $-3.091 | 27.0 | 25.0 | -32.9 | 2 | 5 | $955 |
| Tier 2 | unfiltered | Q3+Q4 active | 40 | $94,553 | 43% | $-4.162 | 42.3 | 25.1 | -58.8 | 8 | 8 | $428 |

## 2. Spread sweep at the winning filter (unfiltered, unfiltered)

| Tier | spread_bps | Volume | Active | Net PnL / $1k | Capture bps | Fee bps | Drift bps | Max DD |
|---|---|---|---|---|---|---|---|---|
| Tier 1 | 5 | $2,405,215 | 65% | $-4.085 | 6.2 | 40.0 | -7.1 | $9,826 |
| Tier 2 | 5 | $3,688,238 | 65% | $-2.623 | 6.4 | 25.0 | -7.6 | $9,675 |
| Tier 1 | 10 | $1,809,682 | 65% | $-4.172 | 11.9 | 40.0 | -13.6 | $7,550 |
| Tier 2 | 10 | $1,809,682 | 65% | $-2.672 | 11.9 | 25.0 | -13.6 | $4,836 |
| Tier 1 | 15 | $993,772 | 65% | $-4.184 | 16.9 | 40.0 | -18.8 | $4,158 |
| Tier 2 | 15 | $993,772 | 65% | $-2.684 | 16.9 | 25.0 | -18.8 | $2,668 |
| Tier 1 | 25 | $357,747 | 65% | $-4.662 | 26.8 | 40.1 | -33.4 | $1,668 |
| Tier 2 | 25 | $357,747 | 65% | $-3.160 | 26.8 | 25.0 | -33.4 | $1,134 |
| Tier 1 | 40 | $108,568 | 65% | $-5.319 | 42.3 | 40.1 | -55.4 | $584 |
| Tier 2 | 40 | $108,568 | 65% | $-3.815 | 42.3 | 25.1 | -55.4 | $429 |

## 3. Rolling 30-day "smart boost" ($10k maintenance, Tier 2)

Policy volume is capped at $2,000/day. Baseline boosts
every day `rolling_30d < $10,000` regardless of conditions. Smart boost
quotes on the A+ filter (unfiltered, unfiltered) to cover a deficit,
tops up on the relaxed fallback (ADX<30, all vol bands) when A+ volume
is more than 2 days short, and — while the tier is still healthy —
pre-builds a fallback cushion when volume expiring within 3-5 days
would push the rolling window under $10,000.

| Policy | Volume traded | Net subsidy cost | Tier uptime |
|---|---|---|---|
| baseline | $20,000 | $52.47 | 87% |
| smart | $20,000 | $52.60 | 98% |

## 4. Top findings

1. Best regime filter: **unfiltered + unfiltered** at
   `spread_bps = 5` — net PnL $-2.623 per
   $1,000 volume on $3,688,238 traded
   (65% of bars active, ~$49,179/day).
2. Second: **unfiltered + Q2+Q3 Goldilocks** at
   `spread_bps = 5` — $-2.627 per
   $1,000.
3. Third: **ADX<25 + unfiltered** at
   `spread_bps = 5` — $-2.627 per
   $1,000.
4. Smart boost spent $52.60 vs
   $52.47 always-on and held tier uptime at
   98% vs 87%
   — the expiry-anticipation cushion, not the regime filter, is what
   keeps the tier alive between A+ spells.

## 5. Recommendation for Step 2 (`strategy/subsidy.py`, `execution/ping_pong.py`)

1. Regime gating earned almost nothing in this window: the unfiltered
   run was cheapest per dollar of volume, every gate or band traded
   less for a similar cost per dollar, and ADX(14) never once fell
   under 15 — inventory drift, not maker fee, is the cost driver. Wire
   the gate in, but set it loose: **daily ADX(14) < 30** with no 4h-ATR%
   band restriction keeps ~half of bars quotable at the same per-dollar
   cost while still cutting off genuinely trending days.
2. Set `subsidy_spread_bps = 5`. Wider spreads raise capture
   per fill but collapse fill count and leave inventory parked longer;
   the net per $1,000 worsens monotonically past 5 bps.
3. Smart boost: on a deficit, quote the A+ filter and top up on the
   fallback (ADX<30, all bands) when the shortfall exceeds two days of
   cap; while healthy, watch the 30-day window and pre-build a fallback
   cushion when volume expiring within 3-5 days would push
   `rolling_30d` under $10,000.
4. Keep the $2,000/day booster cap; the unfiltered run averaged
   $49,179/day, so the cap is the throttle that
   paces a $10k window, not the market.

## Limits

- 5-minute bars, not the worker's 20-second ticks: both a buy and a sell
  can fill inside one bar here, and fills assume the quote rested the
  whole bar. Treat volumes as an upper bound.
- Single ~74-day window on BTC-USD only; quartile thresholds and the
  winning filter are fitted on this window, so out-of-sample results
  will be worse.
- No book depth: a fill never moves the market and every quote gets the
  maker rate.
