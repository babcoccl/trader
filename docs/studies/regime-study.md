# Study 4: regimes, strategy switching and grid bots (2026-11-23)

Asked by the owner: can the system detect market regimes and run a
different strategy in each, in particular a grid bot in sideways
markets?

## What the earlier studies did

- The trend sleeve was one fixed strategy run through every year and
  scored separately on 2018 to 2021 and 2022 to 2026. It is regime-aware
  by construction: the number of positive rules is a regime score, so it
  holds more in uptrends and cash in downtrends.
- Add-on tests were switched on only in a stated regime: oversold
  bounces (trend off, trend on, any), the funding rule (trend off), and
  volatility filters.
- No earlier test selected between different strategies by regime. This
  study does.

## Method

Daily tests: `research/regimes.py`, `regimes2.py`, `regimes3.py`,
`regimes4.py` on the study backtester (`research/bt.py`): signal at the
daily close, fill at the next open, long only, no leverage, 0.45% cost
per side (Bitstamp 0.40% immediate plus 0.05% slippage). Grid tests:
`research/gridreg.py`, `gridreg2.py`, `gridreg3.py` on five-minute bars;
a limit order fills only if the bar trades through its price by 0.01%;
fees 0.30% resting and 0.40% immediate plus 0.05% slippage. Prices are
Binance's, not Bitstamp's. BTC and ETH. 2018-04-01 to 2021-12-31 is the
fitting period; 2022-01-01 onward is held out (daily tests to
2026-11-19, grid tests to 2026-10-31). Every regime label for day D uses
data up to the close of day D-1.

Regime by rule count: uptrend = 7 to 9 of the nine rules positive, mixed
= 3 to 6, downtrend = 0 to 2. Calm or volatile = 30-day volatility in
the lower or upper half of its trailing 365 days.

## 1. Where the trend sleeve earns its return (before costs)

| Regime | Share of days 2022-2026 | Holding the coin, a year | Sleeve contribution, points a year | Same, 2018-2021 |
|---|---|---|---|---|
| Uptrend | 31% | +77% | +17.6 | +37.8 |
| Mixed | 29% | +2% | -0.4 | +7.0 |
| Downtrend | 41% | -27% | -1.0 | -0.4 |
| Uptrend, calm | 18% | +87% | +12.0 | +20.1 |
| Uptrend, volatile | 13% | +64% | +5.6 | +17.7 |
| Mixed, calm | 19% | -26% | -2.2 | +6.1 |
| Mixed, volatile | 9% | +62% | +1.8 | +0.9 |

Regimes flip fast. For BTC since 2022: 27 uptrend spells (median 6
days, longest 89), 64 mixed spells (median 4 days, longest 38), 40
downtrend spells (median 10 days, longest 89).

## 2. Regime-switching variants of the daily strategy

| Variant | 2018-2021 a year | 2022-2026 a year | Worst loss 2022-2026 | With no costs 2022-2026 | Days with a trade a year | Days fully in cash |
|---|---|---|---|---|---|---|
| A. Trend sleeve as is | 43.8% | 11.4% | -24.3% | 15.6% | 113 | 10% |
| B. Trend only in uptrend, otherwise cash | 36.4% | 12.9% | -21.5% | 17.5% | 31 | 62% |
| C. Full size in uptrend, as is elsewhere | 46.7% | 11.7% | -25.8% | 16.9% | 117 | 10% |
| D. Cash while mixed and calm | 35.9% | 11.5% | -24.6% | 18.1% | 107 | 21% |
| E. 60% volatility target in calm uptrends | 50.0% | 10.9% | -26.3% | 15.7% | 116 | 10% |
| F. Half size while volatile | 33.1% | 7.4% | -23.1% | 12.2% | 126 | 10% |
| G. A plus dip-buying sleeve while mixed | 46.7% | 12.9% | -24.0% | 18.2% | 113 | 10% |
| H. Switch: trend in uptrend, dip-buying while mixed, cash in downtrend | 39.6% | 14.8% | -22.2% | 20.1% | 36 | 58% |

- Difference from A in daily returns: B -6.1 points a year (t -1.18)
  then +0.7 (t 0.15); G +2.4 (t 0.74) then +1.7 (t 0.98); H -3.5
  (t -0.68) then +2.5 (t 0.61). None is distinguishable from chance.
- By year, A: -18.9% (2022), 43.5%, 43.0%, 6.8%, -4.3% (2026). H: -0.2%,
  29.6%, 39.0%, 5.3%, 4.2%. The switch avoids the 2022 loss and gives up
  14 points in 2023.

## 3. Dip-buying inside a regime (the sleeve in G and H)

Buy at the close when price is under its 20-day average by a set
amount, sell at the next open after it closes back above the average or
after a time limit. 5 triggers (1, 1.5 or 2 standard deviations under
the 20-day average; RSI(14) under 35; RSI(5) under 25) by 3 exits (back
to the average within 10 or 20 days; hold 5 days) = 15 settings per
regime. Average net result per trade:

| Regime | 2018-2021 median | 2022-2026 median | 2022-2026 range | Positive 2022-2026 | Positive in both periods | t above 2 |
|---|---|---|---|---|---|---|
| Mixed (3 to 6 rules) | +0.13% | +0.94% | -0.79% to +9.22% | 12 of 15 | 7 | 3 |
| Sideways (30-day efficiency in the bottom third of the year) | +0.20% | -0.08% | -1.44% to +2.59% | 6 | 3 | 0 |
| Sideways and mixed | +2.07% | +1.02% | -2.56% to +21.11% | 11 | 7 | 0 |
| Mixed and calm | -2.39% | -0.07% | -1.78% to +9.21% | 7 | 2 | 0 |
| Downtrend | -0.96% | -1.83% | -5.53% to -0.65% | 0 | 0 | 0 |
| Uptrend | +1.06% | +2.97% | +1.07% to +5.43% | 12 | 7 | 3 |
| Any | -1.05% | -0.89% | -2.55% to -0.05% | 0 | 0 | 0 |

- The setting chosen on 2018-2021 with at least 15 trades (mixed regime,
  2 standard deviations under, hold 5 days): +1.79% a trade on 15 trades
  in the fitting period, +0.90% on 22 trades held out (t 0.57). As a
  sleeve of 25% of the account per coin it returned 0.9% a year alone.
  This is the setting spec G3 logs in shadow.
- The three settings with t above 2 in the mixed regime are RSI(14)
  under 35, on 7 trades.
- Placebo for 1.5 standard deviations under, back to the average within
  10 days, mixed regime: the same number of entries at random mixed-
  regime days gave a median of -0.84% a trade (5th to 95th percentile
  -2.35% to +0.60%) against +0.94% real; 2% of 120 runs matched it. The
  same setting lost 2.75% a trade in 2018-2021.

## 4. Grid bot run only while a sideways regime is detected

Grid: when the regime switches on, levels are set at a fixed spacing
around that moment's price. One lot (the sleeve divided by the number of
lots) is bought each time price trades down through an empty level and
sold one level higher. Spacing 0.5%, 1%, 1.5%, 2%, 3%, 5%; 5 or 10 lots.

Detectors on daily bars (share of days on): 30-day efficiency ratio in
the bottom third of the year (33%), ADX(14) under 20 (21%), ADX(14)
under 25 (42%), 20-day Bollinger width in the bottom third (38%), 20-day
range in the bottom third (41%), mixed rules (29%), ADX under 20 and not
in a downtrend (15%), ADX under 20 and narrow range (15%), ADX(28) under
20 (46%). On 4-hour and 1-hour bars: ADX(14) under 20 on each, ADX(48)
under 20 on 1-hour, and each with a narrow-range condition.

What happens to unsold lots when the regime ends: sold at once; sell
orders kept and the rest sold after 7 days; a 10% stop per lot, with or
without the 7-day limit; or never sold at a loss.

| Version (Bitstamp fees, 2022-2026) | Settings | Positive | Yearly return | Worst loss |
|---|---|---|---|---|
| Ends flat (sold at regime end, within 7 days, or on a stop), daily detectors | 432 | 15 (3 also positive in 2018-2021) | median -16.7%, best +4.2% | down to -99% |
| Same, 4-hour and 1-hour detectors | 120 | 0 | -51% to -2% | |
| Ends flat, daily detectors, no fees | 432 | 126 | median -3.7%, best +12.3% | |
| Never sold at a loss | 120 | 61 | median +0.3%, best +7.9% | median -66% |
| Hindsight regime (the next 14 days stay in a narrow range; not tradable), sold at regime end | 12 | 8 | 14% to 19% at 2% to 5% spacing | -10% to -18% |
| Hindsight regime, rest sold after 7 days | 12 | 9 | 25% to 29% at 2% to 5% spacing | -12% to -19% |

- Best real detector, ADX(14) under 20 on daily bars, 5% spacing, sold
  at regime end: -0.4% a year in 2018-2021, +0.5% in 2022-2026, about 24
  round trips a year. With 7 days to sell the rest: -7.5% then +3.2%.
  This is the setting spec G3 logs in shadow (5% spacing, 10 lots).
- The versions never sold at a loss were in the market 80% of the time
  (median); they are holding coins through downtrends, not trading a
  range.
- A sideways reading does not predict a sideways fortnight. Share of
  days followed by a narrow 14-day range, 2022-2026: 68.7% for all days;
  66.2% to 72.3% after each detector's sideways reading.
- A sideways spell ends with a breakout. If price breaks down the grid
  is full of lots bought on the way; if it breaks up the grid has
  already sold. That is why most settings lose with no fees.
- One buy and sell costs 0.6% in fees, so spacing under about 1.5% loses
  by construction.

## 5. Funding rule (from the 2026-11-23 forced-flow test)

While the trend is off (0 to 2 rules) and the 7-day average perpetual
funding rate is negative, hold 25% of the account in that coin. Binance
funding rates, 2022 to 2026-10-31, 0.45% per side.

| Variant | Yearly return | Worst loss |
|---|---|---|
| Trend sleeve as is | 11.4% | -24% |
| Plus the funding rule, 7-day average | 16.8% | -24% |
| Same with 3, 14, 30-day averages | 13.5%, 12.8%, 14.1% | -22% to -27% |
| Funding rule alone | 4.2% | -8% |
| Same number of trend-off days at random times, 200 runs | median 10.2%, 5th to 95th percentile 7.5% to 12.9% | |

About 40 episodes, mostly 2022 and 2026, and nearly all of the gain is
ETH. Seven-day returns after such days averaged +1.35% against -0.13%
for all trend-off days, not statistically distinguishable. Spec G3 logs
this rule in shadow on Coinbase's funding rate.

## Conclusions

1. Record the regime with every daily decision and show it (spec G1).
   This costs nothing and explains idle periods.
2. Put the selector in place so a sleeve can be switched on per regime
   (spec G2). Only the trend sleeve trades.
3. Run the grid, the mixed-regime dip-buying and the funding rule as
   shadow sleeves on Bitstamp's own prices, with no orders (spec G3).
   A sleeve is considered for the paper account only after its shadow
   record is positive after fees over at least 8 weeks and 20 trades.
4. No change to the trend sleeve's rules, sizing or limits.

## Limits

- Binance prices and funding rates, not Bitstamp's or Coinbase's.
- About 110 daily settings and 700 grid settings were tried, so the
  best ones are flattered.
- No statistical regime model (for example a hidden Markov model) and no
  lower Bitstamp fee tier were tested.
- Variants G and H add sleeve weights without the 2.2 plan builder's
  order logic.
