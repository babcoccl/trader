# Study 3: can the system trade more often? (2026-11-23)

Asked by the owner after 12 hours of paper trading without a trade.

## Why it was idle

On Bitstamp's daily closes for 2026-11-22, all nine rules were off for
both assets, so the target was 0% and the account held cash.

| | BTC | ETH |
|---|---|---|
| Close | 61,170 | 1,722 |
| Against the 50, 100, 200-day averages | -20%, -21%, -15% | -23%, -27%, -19% |
| Against the price 30, 90, 180 days ago | -25%, -23%, -19% | -28%, -31%, -17% |
| 20-day high of closes | 80,434 (+31%) | 2,422 (+41%) |

In the 2022 to 2026 backtest the strategy was fully in cash on 10% of
days, in stretches of up to 34 days, and traded on 31% of days.

## Method

Same backtester as studies 1 and 2 (`research/bt.py`): signal at the
daily close, fill at the next open, long only, no leverage. Cost per
side 0.45% (Bitstamp taker 0.40% plus 0.05% slippage). Prices are
Binance's, not Bitstamp's. Results are for 2022-01-01 to 2026-11-19
unless stated; 2018 to 2021 was the fitting period for the bounce test.

## 1. Faster rules, tighter band, larger positions (BTC and ETH)

| Change | Yearly return | Worst loss | Days with a trade per year |
|---|---|---|---|
| Current: rules of 50 to 200 days, 20% band, 40% volatility target | 11.4% | -24% | 113 |
| Rules of 20 to 100 days | 8.0% | -27% | 152 |
| Rules of 5 to 50 days | 0.8% | -30% | 213 |
| All 15 rules together | 5.5% | -27% | 192 |
| Band 5% | 9.1% | -25% | 212 |
| Band 10% | 9.1% | -25% | 165 |
| Band 30% | 11.0% | -24% | 88 |
| Volatility target 60% | 11.4% | -28% | 107 |
| Volatility target 80% | 11.8% | -28% | 103 |
| No volatility target | 11.5% | -29% | 103 |

Over 2018 to 2026 a 60% to 80% volatility target returned 33% to 37% a
year against 24.5%, with a worst loss of -41% to -43% against -31%.
That is beyond the 35% halt.

## 2. Buying oversold bounces

Seven triggers (RSI over 2, 5 and 14 days; a 1, 3 or 5-day fall of
more than 2 to 2.5 standard deviations; a close under the 20-day lower
band) and four exits (hold 3, 5 or 10 days; or exit above the 5-day
average), 28 settings in all.

| Traded only while | Average result per trade after costs | Settings with a positive average | Sleeve alone, yearly | Added to the trend sleeve, yearly |
|---|---|---|---|---|
| Trend off (0 to 2 rules on), 2018 to 2021 | -9.3% to +0.4% (median -2.7%) | 3 of 28 | not run | not run |
| Trend off, 2022 to 2026 | -4.7% to +0.7% (median -2.3%) | 3 of 28 | -20.7% to -1.5%, worst loss -30% to -69% | -11.9% to 9.9%; none beat 11.4% |
| Trend on (7 to 9 rules on), 2022 to 2026 | -3.7% to +5.7% (median +1.0%) | 16 of 28 | -1.9% to 8.9% | 8.7% to 14.4% (median 11.8%) |

- While the trend is off, a sharp fall was usually followed by more
  falling. The three positive settings in each period were different
  ones.
- While the trend is on, the gain is about what any 5 to 10 days in an
  uptrend earned (0.9% to 1.7%, before the 0.9% round-trip cost), with
  2 to 38 trades per setting. Only 1 of 28 settings is statistically
  distinguishable from zero. It also adds exposure on top of the trend
  sleeve, so it is partly the "larger positions" result above.

Rejected.

## 3. More assets

Bitstamp listed about 110 USD spot pairs on 2026-11-23. Liquidity that
day, one sample of each order book:

| Pair | Traded in 24 hours | Spread | Resting within 0.1% of the price (smaller side) | First daily candle |
|---|---|---|---|---|
| BTC/USD | $87m | 0.003% | $1,356k | 2011 |
| ETH/USD | $16m | 0.002% | $1,263k | 2017 |
| SOL/USD | $11m | 0.001% | $277k | 2022-08-18 |
| XRP/USD | $5m | 0.001% | $281k | 2016 |
| DOGE/USD | $6m | 0.002% | $249k | 2022-12-21 |
| ADA/USD | $1.4m | 0.014% | $36k | not checked |
| LTC/USD | $0.3m | 0.022% | $71k | not checked |
| LINK/USD | $0.2m | 0.046% | $22k | not checked |
| SHIB/USD | $0.2m | 0.276% | $0 | not checked |
| BNB/USD | $0.04m | 0.045% | $6k | not checked |

Median traded per day over 31 days: SOL $13.9m, XRP $14.6m, DOGE $5.3m.

Trend sleeve with more assets (equal shares, 40% volatility target, 20%
band). SOL and DOGE are used only from 200 days after their first
Bitstamp candle.

| Assets | Yearly return | Worst loss | Return per unit of risk | Days with a trade per year |
|---|---|---|---|---|
| BTC, ETH | 11.4% | -24% | 0.61 | 113 |
| BTC, ETH, SOL | 12.6% | -24% | 0.70 | 137 |
| BTC, ETH, XRP | 8.1% | -24% | 0.50 | 160 |
| BTC, ETH, SOL, XRP | 9.6% | -23% | 0.59 | 182 |
| BTC, ETH, SOL, XRP, DOGE | 9.7% | -23% | 0.62 | 195 |
| BTC, ETH plus the 2 most traded Bitstamp-listed coins at the end of 2021 (SHIB, BNB) | 9.7% | -19% | 0.59 | 195 |
| BTC, ETH plus the 8 most traded at the end of 2021 | 5.7% | -24% | 0.42 | 282 |

Each asset alone, same rules: BTC 14.5%, SOL 11.4%, BNB 9.6%, DOGE
7.0%, ETH 6.0%, SHIB 4.2%, ADA 1.6%, AVAX 1.3%, BCH 1.0%, XRP 0.1%,
LINK -3.6%, DOT -4.5%, SAND -4.9%, LTC -10.1%.

- SOL and XRP were chosen because they are liquid on Bitstamp today.
  That is a choice made with knowledge of which coins survived, so the
  12.6% for SOL is not evidence that adding it helps.
- More assets give more trading days at about the same risk-adjusted
  result and a lower return, because each asset's share shrinks.
- On 2026-11-19 all nine rules were off for SOL, XRP and DOGE as well.

## Limits

- Binance prices throughout; Bitstamp prices were used only for the
  idle check and the liquidity table.
- Liquidity is one order-book sample per pair on one day.
- The bounce test uses daily data; an intraday version was not run.
