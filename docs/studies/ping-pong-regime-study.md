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
| Tier 1 | ADX<20 | unfiltered | 5 | $851,375 | 19% | $-4.270 | 15.1 | 40.0 | -17.8 | <1 | 5 | $3,661 |
| Tier 1 | ADX<20 | unfiltered | 10 | $348,189 | 19% | $-4.629 | 29.1 | 40.1 | -35.3 | 2 | 5 | $1,650 |
| Tier 1 | ADX<20 | unfiltered | 15 | $168,937 | 19% | $-5.606 | 48.9 | 40.2 | -64.7 | 4 | 5 | $998 |
| Tier 1 | ADX<20 | unfiltered | 25 | $55,189 | 19% | $-6.024 | 70.7 | 40.1 | -90.8 | 14 | 14 | $345 |
| Tier 1 | ADX<20 | unfiltered | 40 | $12,750 | 19% | $-11.065 | 149.3 | 40.5 | -219.4 | 59 | 59 | $157 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 5 | $569,202 | 13% | $-4.168 | 13.9 | 40.0 | -15.5 | 1 | 5 | $2,385 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 10 | $216,476 | 13% | $-4.655 | 28.7 | 40.1 | -35.2 | 3 | 5 | $1,032 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 15 | $95,236 | 13% | $-4.773 | 40.2 | 40.1 | -47.8 | 8 | 8 | $467 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 25 | $25,984 | 13% | $-4.564 | 56.0 | 40.0 | -61.7 | 29 | 29 | $119 |
| Tier 1 | ADX<20 | Q2+Q3 Goldilocks | 40 | $6,243 | 13% | $-15.663 | 193.2 | 41.2 | -308.6 | 120 | 120 | $116 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 5 | $474,394 | 10% | $-4.445 | 16.7 | 40.0 | -21.1 | 2 | 5 | $2,134 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 10 | $187,191 | 10% | $-5.156 | 34.1 | 40.1 | -45.5 | 4 | 5 | $1,003 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 15 | $93,943 | 10% | $-6.820 | 60.3 | 40.3 | -88.2 | 8 | 8 | $692 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 25 | $32,181 | 10% | $-7.736 | 87.3 | 40.2 | -124.4 | 23 | 23 | $262 |
| Tier 1 | ADX<20 | Q1+Q2 quiet | 40 | $9,750 | 10% | $-10.128 | 141.9 | 40.8 | -202.4 | 77 | 77 | $117 |
| Tier 1 | ADX<20 | Q3+Q4 active | 5 | $378,182 | 8% | $-4.320 | 15.6 | 40.0 | -18.8 | 2 | 5 | $1,646 |
| Tier 1 | ADX<20 | Q3+Q4 active | 10 | $160,966 | 8% | $-4.164 | 24.7 | 40.0 | -26.3 | 5 | 5 | $671 |
| Tier 1 | ADX<20 | Q3+Q4 active | 15 | $74,720 | 8% | $-6.660 | 58.9 | 40.3 | -85.2 | 10 | 10 | $561 |
| Tier 1 | ADX<20 | Q3+Q4 active | 25 | $22,981 | 8% | $-9.766 | 107.0 | 40.7 | -164.0 | 33 | 33 | $256 |
| Tier 1 | ADX<20 | Q3+Q4 active | 40 | $5,000 | 8% | $-55.320 | 573.7 | 46.0 | -1080.9 | 150 | 150 | $341 |
| Tier 1 | ADX<25 | unfiltered | 5 | $2,125,510 | 41% | $-4.127 | 13.9 | 40.0 | -15.2 | <1 | 5 | $8,786 |
| Tier 1 | ADX<25 | unfiltered | 10 | $928,865 | 41% | $-4.214 | 25.2 | 40.0 | -27.3 | <1 | 5 | $3,942 |
| Tier 1 | ADX<25 | unfiltered | 15 | $466,904 | 41% | $-4.205 | 35.0 | 40.0 | -37.1 | 2 | 5 | $1,963 |
| Tier 1 | ADX<25 | unfiltered | 25 | $148,887 | 41% | $-5.142 | 63.2 | 40.1 | -74.5 | 5 | 5 | $796 |
| Tier 1 | ADX<25 | unfiltered | 40 | $38,177 | 41% | $-8.433 | 127.3 | 40.7 | -171.0 | 20 | 20 | $366 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 5 | $988,433 | 21% | $-4.167 | 14.0 | 40.0 | -15.7 | <1 | 5 | $4,144 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 10 | $390,193 | 21% | $-4.510 | 27.5 | 40.1 | -32.5 | 2 | 5 | $1,799 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 15 | $178,470 | 21% | $-4.715 | 39.8 | 40.1 | -46.8 | 4 | 5 | $867 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 25 | $48,981 | 21% | $-6.423 | 74.7 | 40.3 | -98.6 | 15 | 15 | $343 |
| Tier 1 | ADX<25 | Q2+Q3 Goldilocks | 40 | $9,949 | 21% | $-9.089 | 132.6 | 40.0 | -183.5 | 75 | 75 | $96 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 5 | $678,392 | 15% | $-4.315 | 15.5 | 40.0 | -18.6 | 1 | 5 | $2,953 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 10 | $263,176 | 15% | $-4.882 | 31.3 | 40.1 | -40.0 | 3 | 5 | $1,323 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 15 | $127,936 | 15% | $-6.132 | 53.7 | 40.2 | -74.8 | 6 | 6 | $835 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 25 | $43,192 | 15% | $-6.524 | 76.4 | 40.2 | -101.4 | 17 | 17 | $297 |
| Tier 1 | ADX<25 | Q1+Q2 quiet | 40 | $10,742 | 15% | $-11.757 | 156.7 | 40.7 | -233.6 | 70 | 70 | $140 |
| Tier 1 | ADX<25 | Q3+Q4 active | 5 | $1,447,591 | 26% | $-4.131 | 14.1 | 40.0 | -15.4 | <1 | 5 | $5,994 |
| Tier 1 | ADX<25 | Q3+Q4 active | 10 | $664,923 | 26% | $-4.212 | 25.3 | 40.0 | -27.4 | 1 | 5 | $2,828 |
| Tier 1 | ADX<25 | Q3+Q4 active | 15 | $340,939 | 26% | $-4.179 | 34.7 | 40.0 | -36.5 | 2 | 5 | $1,425 |
| Tier 1 | ADX<25 | Q3+Q4 active | 25 | $104,935 | 26% | $-5.163 | 63.6 | 40.2 | -75.0 | 7 | 7 | $571 |
| Tier 1 | ADX<25 | Q3+Q4 active | 40 | $28,164 | 26% | $-10.485 | 146.8 | 40.9 | -210.7 | 27 | 27 | $347 |
| Tier 1 | ADX<30 | unfiltered | 5 | $2,387,617 | 47% | $-4.109 | 13.7 | 40.0 | -14.8 | <1 | 5 | $9,812 |
| Tier 1 | ADX<30 | unfiltered | 10 | $1,129,300 | 47% | $-4.207 | 25.3 | 40.0 | -27.3 | <1 | 5 | $4,781 |
| Tier 1 | ADX<30 | unfiltered | 15 | $580,860 | 47% | $-4.211 | 35.3 | 40.0 | -37.4 | 1 | 5 | $2,447 |
| Tier 1 | ADX<30 | unfiltered | 25 | $192,842 | 47% | $-4.792 | 60.3 | 40.0 | -68.2 | 4 | 5 | $924 |
| Tier 1 | ADX<30 | unfiltered | 40 | $53,872 | 47% | $-6.289 | 105.5 | 40.0 | -128.4 | 14 | 14 | $343 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 5 | $1,046,434 | 22% | $-4.157 | 13.9 | 40.0 | -15.5 | <1 | 5 | $4,376 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 10 | $414,194 | 22% | $-4.477 | 27.1 | 40.1 | -31.8 | 2 | 5 | $1,893 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 15 | $188,473 | 22% | $-4.663 | 39.2 | 40.1 | -45.7 | 4 | 5 | $905 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 25 | $50,980 | 22% | $-6.365 | 73.8 | 40.3 | -97.2 | 15 | 15 | $351 |
| Tier 1 | ADX<30 | Q2+Q3 Goldilocks | 40 | $10,948 | 22% | $-8.788 | 129.5 | 40.0 | -177.4 | 69 | 69 | $102 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 5 | $817,380 | 18% | $-4.276 | 15.1 | 40.0 | -17.9 | <1 | 5 | $3,520 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 10 | $322,163 | 18% | $-4.760 | 29.9 | 40.1 | -37.4 | 2 | 5 | $1,572 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 15 | $150,925 | 18% | $-5.877 | 51.0 | 40.2 | -69.6 | 5 | 5 | $938 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 25 | $48,174 | 18% | $-6.625 | 77.2 | 40.2 | -103.3 | 16 | 16 | $332 |
| Tier 1 | ADX<30 | Q1+Q2 quiet | 40 | $11,739 | 18% | $-11.371 | 150.6 | 40.6 | -223.7 | 64 | 64 | $146 |
| Tier 1 | ADX<30 | Q3+Q4 active | 5 | $1,689,292 | 29% | $-4.149 | 14.4 | 40.0 | -15.8 | <1 | 5 | $7,037 |
| Tier 1 | ADX<30 | Q3+Q4 active | 10 | $806,370 | 29% | $-4.202 | 25.5 | 40.0 | -27.5 | <1 | 5 | $3,419 |
| Tier 1 | ADX<30 | Q3+Q4 active | 15 | $431,906 | 29% | $-4.179 | 35.0 | 40.0 | -36.8 | 2 | 5 | $1,806 |
| Tier 1 | ADX<30 | Q3+Q4 active | 25 | $143,907 | 29% | $-4.607 | 58.8 | 40.0 | -64.9 | 5 | 5 | $664 |
| Tier 1 | ADX<30 | Q3+Q4 active | 40 | $41,861 | 29% | $-7.208 | 114.6 | 40.0 | -146.7 | 18 | 18 | $314 |
| Tier 1 | unfiltered | unfiltered | 5 | $2,390,467 | 65% | $-4.085 | 13.4 | 40.0 | -14.2 | <1 | 5 | $9,764 |
| Tier 1 | unfiltered | unfiltered | 10 | $1,809,683 | 65% | $-4.172 | 25.4 | 40.0 | -27.1 | <1 | 5 | $7,550 |
| Tier 1 | unfiltered | unfiltered | 15 | $993,774 | 65% | $-4.183 | 35.6 | 40.0 | -37.4 | <1 | 5 | $4,157 |
| Tier 1 | unfiltered | unfiltered | 25 | $357,748 | 65% | $-4.662 | 60.0 | 40.1 | -66.6 | 2 | 5 | $1,668 |
| Tier 1 | unfiltered | unfiltered | 40 | $108,568 | 65% | $-5.319 | 97.6 | 40.1 | -110.7 | 7 | 7 | $584 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 5 | $1,189,447 | 25% | $-4.127 | 13.7 | 40.0 | -14.9 | <1 | 5 | $4,935 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 10 | $472,207 | 25% | $-4.391 | 26.2 | 40.1 | -30.1 | 2 | 5 | $2,112 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 15 | $214,477 | 25% | $-4.565 | 38.1 | 40.1 | -43.7 | 3 | 5 | $1,005 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 25 | $56,969 | 25% | $-6.296 | 73.2 | 40.3 | -95.9 | 13 | 13 | $384 |
| Tier 1 | unfiltered | Q2+Q3 Goldilocks | 40 | $11,946 | 25% | $-8.545 | 127.2 | 40.0 | -172.6 | 63 | 63 | $108 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 5 | $930,385 | 22% | $-4.237 | 14.7 | 40.0 | -17.1 | <1 | 5 | $3,967 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 10 | $358,159 | 22% | $-4.694 | 29.2 | 40.1 | -36.1 | 2 | 5 | $1,719 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 15 | $167,934 | 22% | $-5.636 | 48.6 | 40.2 | -64.7 | 4 | 5 | $997 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 25 | $52,180 | 22% | $-6.327 | 74.2 | 40.2 | -97.3 | 14 | 14 | $343 |
| Tier 1 | unfiltered | Q1+Q2 quiet | 40 | $12,750 | 22% | $-9.899 | 136.8 | 40.6 | -195.2 | 59 | 59 | $147 |
| Tier 1 | unfiltered | Q3+Q4 active | 5 | $2,359,075 | 43% | $-4.134 | 14.3 | 40.0 | -15.6 | <1 | 5 | $9,753 |
| Tier 1 | unfiltered | Q3+Q4 active | 10 | $1,449,759 | 43% | $-4.162 | 25.6 | 40.0 | -27.2 | <1 | 5 | $6,034 |
| Tier 1 | unfiltered | Q3+Q4 active | 15 | $826,815 | 43% | $-4.171 | 35.7 | 40.0 | -37.4 | <1 | 5 | $3,448 |
| Tier 1 | unfiltered | Q3+Q4 active | 25 | $305,803 | 43% | $-4.594 | 59.6 | 40.1 | -65.5 | 2 | 5 | $1,405 |
| Tier 1 | unfiltered | Q3+Q4 active | 40 | $94,553 | 43% | $-5.667 | 101.1 | 40.1 | -117.6 | 8 | 8 | $558 |
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
| Tier 2 | ADX<20 | unfiltered | 5 | $851,375 | 19% | $-2.770 | 15.1 | 25.0 | -17.8 | <1 | 5 | $2,384 |
| Tier 2 | ADX<20 | unfiltered | 10 | $348,189 | 19% | $-3.126 | 29.1 | 25.0 | -35.3 | 2 | 5 | $1,128 |
| Tier 2 | ADX<20 | unfiltered | 15 | $168,937 | 19% | $-4.099 | 48.9 | 25.1 | -64.7 | 4 | 5 | $744 |
| Tier 2 | ADX<20 | unfiltered | 25 | $55,189 | 19% | $-4.519 | 70.7 | 25.1 | -90.8 | 14 | 14 | $264 |
| Tier 2 | ADX<20 | unfiltered | 40 | $12,750 | 19% | $-9.546 | 149.3 | 25.3 | -219.4 | 59 | 59 | $143 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 5 | $569,202 | 13% | $-2.667 | 13.9 | 25.0 | -15.5 | 1 | 5 | $1,532 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 10 | $216,476 | 13% | $-3.152 | 28.7 | 25.0 | -35.2 | 3 | 5 | $707 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 15 | $95,236 | 13% | $-3.270 | 40.2 | 25.1 | -47.8 | 8 | 8 | $324 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 25 | $25,984 | 13% | $-3.064 | 56.0 | 25.0 | -61.7 | 29 | 29 | $83 |
| Tier 2 | ADX<20 | Q2+Q3 Goldilocks | 40 | $6,243 | 13% | $-14.119 | 193.2 | 25.7 | -308.6 | 120 | 120 | $111 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 5 | $474,394 | 10% | $-2.944 | 16.7 | 25.0 | -21.1 | 2 | 5 | $1,423 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 10 | $187,191 | 10% | $-3.651 | 34.1 | 25.1 | -45.5 | 4 | 5 | $722 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 15 | $93,943 | 10% | $-5.308 | 60.3 | 25.2 | -88.2 | 8 | 8 | $551 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 25 | $32,181 | 10% | $-6.227 | 87.3 | 25.2 | -124.4 | 23 | 23 | $215 |
| Tier 2 | ADX<20 | Q1+Q2 quiet | 40 | $9,750 | 10% | $-8.599 | 141.9 | 25.5 | -202.4 | 77 | 77 | $107 |
| Tier 2 | ADX<20 | Q3+Q4 active | 5 | $378,182 | 8% | $-2.819 | 15.6 | 25.0 | -18.8 | 2 | 5 | $1,079 |
| Tier 2 | ADX<20 | Q3+Q4 active | 10 | $160,966 | 8% | $-2.664 | 24.7 | 25.0 | -26.3 | 5 | 5 | $430 |
| Tier 2 | ADX<20 | Q3+Q4 active | 15 | $74,720 | 8% | $-5.148 | 58.9 | 25.2 | -85.2 | 10 | 10 | $453 |
| Tier 2 | ADX<20 | Q3+Q4 active | 25 | $22,981 | 8% | $-8.241 | 107.0 | 25.4 | -164.0 | 33 | 33 | $222 |
| Tier 2 | ADX<20 | Q3+Q4 active | 40 | $5,000 | 8% | $-53.596 | 573.7 | 28.7 | -1080.9 | 150 | 150 | $334 |
| Tier 2 | ADX<25 | unfiltered | 5 | $2,125,510 | 41% | $-2.627 | 13.9 | 25.0 | -15.2 | <1 | 5 | $5,598 |
| Tier 2 | ADX<25 | unfiltered | 10 | $928,865 | 41% | $-2.713 | 25.2 | 25.0 | -27.3 | <1 | 5 | $2,549 |
| Tier 2 | ADX<25 | unfiltered | 15 | $466,904 | 41% | $-2.705 | 35.0 | 25.0 | -37.1 | 2 | 5 | $1,265 |
| Tier 2 | ADX<25 | unfiltered | 25 | $148,887 | 41% | $-3.637 | 63.2 | 25.1 | -74.5 | 5 | 5 | $574 |
| Tier 2 | ADX<25 | unfiltered | 40 | $38,177 | 41% | $-6.907 | 127.3 | 25.4 | -171.0 | 20 | 20 | $310 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 5 | $988,433 | 21% | $-2.666 | 14.0 | 25.0 | -15.7 | <1 | 5 | $2,662 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 10 | $390,193 | 21% | $-3.008 | 27.5 | 25.0 | -32.5 | 2 | 5 | $1,214 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 15 | $178,470 | 21% | $-3.212 | 39.8 | 25.1 | -46.8 | 4 | 5 | $601 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 25 | $48,981 | 21% | $-4.911 | 74.7 | 25.2 | -98.6 | 15 | 15 | $270 |
| Tier 2 | ADX<25 | Q2+Q3 Goldilocks | 40 | $9,949 | 21% | $-7.589 | 132.6 | 25.0 | -183.5 | 75 | 75 | $83 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 5 | $678,392 | 15% | $-2.814 | 15.5 | 25.0 | -18.6 | 1 | 5 | $1,935 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 10 | $263,176 | 15% | $-3.379 | 31.3 | 25.1 | -40.0 | 3 | 5 | $928 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 15 | $127,936 | 15% | $-4.623 | 53.7 | 25.2 | -74.8 | 6 | 6 | $644 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 25 | $43,192 | 15% | $-5.017 | 76.4 | 25.1 | -101.4 | 17 | 17 | $233 |
| Tier 2 | ADX<25 | Q1+Q2 quiet | 40 | $10,742 | 15% | $-10.233 | 156.7 | 25.4 | -233.6 | 70 | 70 | $125 |
| Tier 2 | ADX<25 | Q3+Q4 active | 5 | $1,447,591 | 26% | $-2.631 | 14.1 | 25.0 | -15.4 | <1 | 5 | $3,823 |
| Tier 2 | ADX<25 | Q3+Q4 active | 10 | $664,923 | 26% | $-2.711 | 25.3 | 25.0 | -27.4 | 1 | 5 | $1,831 |
| Tier 2 | ADX<25 | Q3+Q4 active | 15 | $340,939 | 26% | $-2.679 | 34.7 | 25.0 | -36.5 | 2 | 5 | $914 |
| Tier 2 | ADX<25 | Q3+Q4 active | 25 | $104,935 | 26% | $-3.657 | 63.6 | 25.1 | -75.0 | 7 | 7 | $414 |
| Tier 2 | ADX<25 | Q3+Q4 active | 40 | $28,164 | 26% | $-8.950 | 146.8 | 25.6 | -210.7 | 27 | 27 | $313 |
| Tier 2 | ADX<30 | unfiltered | 5 | $2,506,200 | 47% | $-2.637 | 14.1 | 25.0 | -15.5 | <1 | 5 | $6,638 |
| Tier 2 | ADX<30 | unfiltered | 10 | $1,129,300 | 47% | $-2.706 | 25.3 | 25.0 | -27.3 | <1 | 5 | $3,087 |
| Tier 2 | ADX<30 | unfiltered | 15 | $580,860 | 47% | $-2.711 | 35.3 | 25.0 | -37.4 | 1 | 5 | $1,576 |
| Tier 2 | ADX<30 | unfiltered | 25 | $192,842 | 47% | $-3.292 | 60.3 | 25.0 | -68.2 | 4 | 5 | $637 |
| Tier 2 | ADX<30 | unfiltered | 40 | $53,872 | 47% | $-4.789 | 105.5 | 25.0 | -128.4 | 14 | 14 | $264 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 5 | $1,046,434 | 22% | $-2.657 | 13.9 | 25.0 | -15.5 | <1 | 5 | $2,806 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 10 | $414,194 | 22% | $-2.975 | 27.1 | 25.0 | -31.8 | 2 | 5 | $1,272 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 15 | $188,473 | 22% | $-3.160 | 39.2 | 25.1 | -45.7 | 4 | 5 | $622 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 25 | $50,980 | 22% | $-4.853 | 73.8 | 25.2 | -97.2 | 15 | 15 | $276 |
| Tier 2 | ADX<30 | Q2+Q3 Goldilocks | 40 | $10,948 | 22% | $-7.288 | 129.5 | 25.0 | -177.4 | 69 | 69 | $88 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 5 | $817,380 | 18% | $-2.775 | 15.1 | 25.0 | -17.9 | <1 | 5 | $2,294 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 10 | $322,163 | 18% | $-3.257 | 29.9 | 25.0 | -37.4 | 2 | 5 | $1,088 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 15 | $150,925 | 18% | $-4.369 | 51.0 | 25.1 | -69.6 | 5 | 5 | $711 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 25 | $48,174 | 18% | $-5.118 | 77.2 | 25.1 | -103.3 | 16 | 16 | $260 |
| Tier 2 | ADX<30 | Q1+Q2 quiet | 40 | $11,739 | 18% | $-9.848 | 150.6 | 25.4 | -223.7 | 64 | 64 | $129 |
| Tier 2 | ADX<30 | Q3+Q4 active | 5 | $1,689,292 | 29% | $-2.648 | 14.4 | 25.0 | -15.8 | <1 | 5 | $4,504 |
| Tier 2 | ADX<30 | Q3+Q4 active | 10 | $806,370 | 29% | $-2.702 | 25.5 | 25.0 | -27.5 | <1 | 5 | $2,209 |
| Tier 2 | ADX<30 | Q3+Q4 active | 15 | $431,906 | 29% | $-2.679 | 35.0 | 25.0 | -36.8 | 2 | 5 | $1,158 |
| Tier 2 | ADX<30 | Q3+Q4 active | 25 | $143,907 | 29% | $-3.107 | 58.8 | 25.0 | -64.9 | 5 | 5 | $451 |
| Tier 2 | ADX<30 | Q3+Q4 active | 40 | $41,861 | 29% | $-5.708 | 114.6 | 25.0 | -146.7 | 18 | 18 | $261 |
| Tier 2 | unfiltered | unfiltered | 5 | $3,700,602 | 65% | $-2.621 | 14.0 | 25.0 | -15.2 | <1 | 5 | $9,698 |
| Tier 2 | unfiltered | unfiltered | 10 | $1,809,683 | 65% | $-2.672 | 25.4 | 25.0 | -27.1 | <1 | 5 | $4,835 |
| Tier 2 | unfiltered | unfiltered | 15 | $993,774 | 65% | $-2.683 | 35.6 | 25.0 | -37.4 | <1 | 5 | $2,667 |
| Tier 2 | unfiltered | unfiltered | 25 | $357,748 | 65% | $-3.160 | 60.0 | 25.0 | -66.6 | 2 | 5 | $1,134 |
| Tier 2 | unfiltered | unfiltered | 40 | $108,568 | 65% | $-3.815 | 97.6 | 25.1 | -110.7 | 7 | 7 | $429 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 5 | $1,189,447 | 25% | $-2.627 | 13.7 | 25.0 | -14.9 | <1 | 5 | $3,151 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 10 | $472,207 | 25% | $-2.889 | 26.2 | 25.0 | -30.1 | 2 | 5 | $1,404 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 15 | $214,477 | 25% | $-3.062 | 38.1 | 25.0 | -43.7 | 3 | 5 | $683 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 25 | $56,969 | 25% | $-4.786 | 73.2 | 25.2 | -95.9 | 13 | 13 | $300 |
| Tier 2 | unfiltered | Q2+Q3 Goldilocks | 40 | $11,946 | 25% | $-7.045 | 127.2 | 25.0 | -172.6 | 63 | 63 | $92 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 5 | $930,385 | 22% | $-2.736 | 14.7 | 25.0 | -17.1 | <1 | 5 | $2,572 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 10 | $358,159 | 22% | $-3.192 | 29.2 | 25.0 | -36.1 | 2 | 5 | $1,182 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 15 | $167,934 | 22% | $-4.129 | 48.6 | 25.1 | -64.7 | 4 | 5 | $745 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 25 | $52,180 | 22% | $-4.821 | 74.2 | 25.1 | -97.3 | 14 | 14 | $265 |
| Tier 2 | unfiltered | Q1+Q2 quiet | 40 | $12,750 | 22% | $-8.378 | 136.8 | 25.3 | -195.2 | 59 | 59 | $130 |
| Tier 2 | unfiltered | Q3+Q4 active | 5 | $2,844,849 | 43% | $-2.638 | 14.5 | 25.0 | -15.9 | <1 | 5 | $7,506 |
| Tier 2 | unfiltered | Q3+Q4 active | 10 | $1,449,759 | 43% | $-2.662 | 25.6 | 25.0 | -27.2 | <1 | 5 | $3,859 |
| Tier 2 | unfiltered | Q3+Q4 active | 15 | $826,815 | 43% | $-2.671 | 35.7 | 25.0 | -37.4 | <1 | 5 | $2,209 |
| Tier 2 | unfiltered | Q3+Q4 active | 25 | $305,803 | 43% | $-3.091 | 59.6 | 25.0 | -65.5 | 2 | 5 | $955 |
| Tier 2 | unfiltered | Q3+Q4 active | 40 | $94,553 | 43% | $-4.162 | 101.1 | 25.1 | -117.6 | 8 | 8 | $428 |

## 2. Spread sweep at the winning filter (unfiltered, unfiltered)

| Tier | spread_bps | Volume | Active | Net PnL / $1k | Capture bps | Fee bps | Drift bps | Max DD |
|---|---|---|---|---|---|---|---|---|
| Tier 1 | 5 | $2,390,467 | 65% | $-4.085 | 13.4 | 40.0 | -14.2 | $9,764 |
| Tier 2 | 5 | $3,700,602 | 65% | $-2.621 | 14.0 | 25.0 | -15.2 | $9,698 |
| Tier 1 | 10 | $1,809,683 | 65% | $-4.172 | 25.4 | 40.0 | -27.1 | $7,550 |
| Tier 2 | 10 | $1,809,683 | 65% | $-2.672 | 25.4 | 25.0 | -27.1 | $4,835 |
| Tier 1 | 15 | $993,774 | 65% | $-4.183 | 35.6 | 40.0 | -37.4 | $4,157 |
| Tier 2 | 15 | $993,774 | 65% | $-2.683 | 35.6 | 25.0 | -37.4 | $2,667 |
| Tier 1 | 25 | $357,748 | 65% | $-4.662 | 60.0 | 40.1 | -66.6 | $1,668 |
| Tier 2 | 25 | $357,748 | 65% | $-3.160 | 60.0 | 25.0 | -66.6 | $1,134 |
| Tier 1 | 40 | $108,568 | 65% | $-5.319 | 97.6 | 40.1 | -110.7 | $584 |
| Tier 2 | 40 | $108,568 | 65% | $-3.815 | 97.6 | 25.1 | -110.7 | $429 |

## 3. Rolling 30-day "smart boost" ($10k maintenance, Tier 2)

Policy volume is capped at $2,000/day. Baseline boosts
every day `rolling_30d < $10,000` regardless of conditions. Smart boost
quotes only on A+ days (unfiltered, unfiltered) while
`days_until_drop > 5`, and relaxes to a fallback filter (ADX<30, all vol
bands) when volume expiring within 3-5 days would push the rolling
window under $10,000.

| Policy | Volume traded | Net subsidy cost | Tier uptime |
|---|---|---|---|
| baseline | $20,000 | $52.41 | 87% |
| smart | $18,000 | $47.46 | 57% |

## 4. Top findings

1. Best regime filter: **unfiltered + unfiltered** at
   `spread_bps = 5` — net PnL $-2.621 per
   $1,000 volume on $3,700,602 traded
   (65% of bars active, ~$49,344/day).
2. Second: **unfiltered + Q2+Q3 Goldilocks** at
   `spread_bps = 5` — $-2.627 per
   $1,000.
3. Third: **ADX<25 + unfiltered** at
   `spread_bps = 5` — $-2.627 per
   $1,000.
4. Smart boost cut the 30-day maintenance cost to
   $47.46 vs $52.41
   always-on, at 57% vs
   87% tier uptime.

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
3. Smart boost: quote the A+ filter only while `days_until_drop > 5`;
   when volume expiring within 3-5 days would drop `rolling_30d` below
   $10,000, relax to the fallback (ADX<30, all volatility bands) until
   the window is back above target. On this window that saved ~10% of
   cost but lost 30 points of tier uptime — the fallback must engage
   earlier than 3-5 days when the daily cap binds.
4. Keep the $2,000/day booster cap; the unfiltered run averaged
   $49,344/day, so the cap is the throttle that
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
