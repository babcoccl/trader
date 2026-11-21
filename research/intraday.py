"""Intraday study on 5-minute Binance spot bars, BTC and ETH, long only, no leverage.

Timing: every signal uses bars up to the close of bar i-1 and acts in bar i.
Limit (maker) orders fill only if the bar trades THROUGH the price by 1 bp. Market and stop (taker) fills pay fee + slippage.
If a stop and a target are both touched in one bar, the stop is assumed first. A lot bought in a bar cannot be sold for profit in the same bar.
"""
import json, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from numba import njit
import bt
BE = ["BTCUSDT", "ETHUSDT"]; T0, T1, HO = "2018-04-01", "2026-10-31", "2022-01-01"
THRU = 1e-4
COSTS = {  # maker fee, taker fee, taker slippage
    "Binance.US (0% maker, 0.02% taker)": (0.0, 0.0002, 0.0002),
    "Hyperliquid spot (0.04% / 0.07%)": (0.0004, 0.0007, 0.0002),
    "Typical global exchange (0.10% / 0.10%)": (0.001, 0.001, 0.0001),
    "Coinbase Advanced (0.50% / 0.90%)": (0.005, 0.009, 0.0001),
}
M5 = {s: pd.read_parquet(f"{bt.D}/m5/{s}.parquet").loc[T0:T1] for s in BE}
ENS = bt.signal("ensemble")

def gate_for(s, on):
    d = M5[s]
    if not on: return np.ones(len(d), dtype=np.bool_)
    e = ENS[s].shift(1)                      # day D-1's signal is known from the start of day D
    return (e.reindex(d.index.floor("D")).values >= 0.5)

@njit(cache=True)
def sim(o, h, l, c, lim, want, tpf, slf, maxhold, gate, fm, ft, slip, thru):
    """One position at a time, all-in. lim[i]: resting buy price in bar i (nan = none). want[i]: 1 = be long by market, 0 = be flat by market, -1 = no market signal.
    tpf/slf[i]: target and stop distance (fraction) fixed at entry. Returns equity per bar, trade count, sum of gross trade returns, wins, bars held."""
    n = len(o); eq = np.empty(n); cash = 1.0; u = 0.0; ep = 0.0; tp = 0.0; sl = 0.0; held = 0; ntr = 0; gsum = 0.0; wins = 0; inpos = 0
    for i in range(n):
        if u > 0.0:
            held += 1; inpos += 1; x = 0.0; fee = 0.0
            if (not gate[i]) or want[i] == 0 or (maxhold > 0 and held > maxhold): x = o[i] * (1 - slip); fee = ft
            elif sl > 0 and o[i] <= sl: x = o[i] * (1 - slip); fee = ft
            elif sl > 0 and l[i] <= sl: x = sl * (1 - slip); fee = ft
            elif tp > 0 and o[i] >= tp: x = o[i]; fee = fm
            elif tp > 0 and h[i] > tp * (1 + thru): x = tp; fee = fm
            if x > 0.0:
                cash = u * x * (1 - fee); g = x / ep - 1; gsum += g; ntr += 1
                if g > 0: wins += 1
                u = 0.0
        if u == 0.0 and gate[i] and cash > 0:
            if want[i] == 1:
                ep = o[i] * (1 + slip); u = cash * (1 - ft) / ep; ep = o[i]; cash = 0.0; held = 0
                tp = ep * (1 + tpf[i]) if tpf[i] > 0 else 0.0; sl = ep * (1 - slf[i]) if slf[i] > 0 else 0.0
            elif lim[i] > 0 and l[i] < lim[i] * (1 - thru):
                ep = min(lim[i], o[i]); u = cash * (1 - fm) / ep; cash = 0.0; held = 0
                tp = ep * (1 + tpf[i]) if tpf[i] > 0 else 0.0; sl = ep * (1 - slf[i]) if slf[i] > 0 else 0.0
                if sl > 0 and l[i] <= sl:
                    x = sl * (1 - slip); cash = u * x * (1 - ft); gsum += x / ep - 1; ntr += 1; u = 0.0
        eq[i] = cash + u * c[i]
    return eq, ntr, gsum, wins, inpos

@njit(cache=True)
def grid(o, h, l, c, g, K, gate, fm, ft, slip, thru):
    """Ping-pong grid: levels P0*(1+g)^j. Buy one lot (1/K of equity) each time price trades down through an empty level, sell that lot one level up. At most K lots."""
    n = len(o); eq = np.empty(n); cash = 1.0; lg = np.log(1 + g); p0 = o[0]
    lv = np.full(K, -10**9); un = np.zeros(K); ntr = 0; gsum = 0.0; inpos = 0
    for i in range(n):
        if not gate[i]:
            for k in range(K):
                if un[k] > 0: cash += un[k] * o[i] * (1 - slip) * (1 - ft); gsum += o[i] * (1 - slip) / (p0 * np.exp(lv[k] * lg)) - 1; ntr += 1; un[k] = 0.0; lv[k] = -10**9
            eq[i] = cash; continue
        for k in range(K):                       # sells first, for lots held before this bar
            if un[k] > 0:
                sp = p0 * np.exp((lv[k] + 1) * lg)
                if h[i] > sp * (1 + thru):
                    cash += un[k] * sp * (1 - fm); gsum += g; ntr += 1; un[k] = 0.0; lv[k] = -10**9
        e = cash
        for k in range(K): e += un[k] * o[i]
        j = int(np.floor(np.log(o[i] / p0) / lg))
        while True:
            bp = p0 * np.exp(j * lg)
            if not (l[i] < bp * (1 - thru)): break
            free = -1; dup = False
            for k in range(K):
                if un[k] == 0.0 and free < 0: free = k
                if un[k] > 0 and lv[k] == j: dup = True
            if free < 0: break
            if not dup:
                amt = min(e / K, cash)
                if amt <= 0: break
                un[free] = amt * (1 - fm) / bp; lv[free] = j; cash -= amt
            j -= 1
        v = cash
        for k in range(K):
            v += un[k] * c[i]
            if un[k] > 0: inpos += 1
        eq[i] = v
    return eq, ntr, gsum, 0, inpos

def stats_of(eq_by_asset, idx):
    e = sum(pd.Series(v, index=idx[s]).resample("D").last() for s, v in eq_by_asset.items()) / len(eq_by_asset)
    out = {}
    for name, a in (("full", None), ("is", (None, "2021-12-31")), ("ho", (HO, None))):
        x = e if a is None else e.loc[a[0]:a[1]]
        r = x.pct_change().dropna(); yrs = len(x) / 365
        out[name] = dict(cagr=float((x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1), maxdd=float((x / x.cummax() - 1).min()), sharpe=float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0)
    return out, e

def prep(s):
    d = M5[s]; c = d.close
    r = np.log(c).diff()
    sig1h = (r.rolling(288, min_periods=288).std() * np.sqrt(12)).shift(1)     # 1-hour volatility, known before bar i
    pc = c.shift(1)
    H = d.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return d, sig1h, pc, H

def variants(s):
    """Yield (family, name, kwargs for sim) for one asset."""
    d, sig, pc, H = prep(s); n = len(d); none = np.full(n, np.nan); nowant = np.full(n, -1, dtype=np.int64); z = np.zeros(n)
    # A. dip buying: resting bid k one-hour-sigmas under the last price, target back up m*k sigmas, stop 2k sigmas down, time limit
    for k in (1, 2, 3, 4):
        lim = (pc * (1 - k * sig)).values
        for m in (0.5, 1.0):
            for hold in (12, 72, 288):
                yield "A dip buying", f"bid {k} sigma below, target {m:g}x, hold up to {hold//12}h", dict(lim=lim, want=nowant, tpf=np.nan_to_num((m * k * sig).values), slf=np.nan_to_num((2 * k * sig).values), maxhold=hold)
    # B. hourly breakout: buy when the hour closes above the prior N-hour high, sell when it closes below the prior N/2-hour low
    for N in (6, 12, 24, 48, 96, 168):
        hi = H.high.rolling(N).max().shift(1); lo = H.low.rolling(N // 2).min().shift(1)
        st = pd.Series(np.nan, index=H.index); st[H.close > hi] = 1; st[H.close < lo] = 0; st = st.ffill().fillna(0)
        w = st.shift(1).reindex(d.index, method="ffill").fillna(0).values.astype(np.int64)   # hour h's state is usable from hour h+1
        yield "B hourly breakout", f"{N}-hour breakout", dict(lim=none, want=w, tpf=z, slf=z, maxhold=0)
    # D. hourly moving-average crossover
    for f, sl in ((4, 16), (8, 32), (12, 48), (24, 96), (48, 192), (96, 384)):
        st = (H.close.ewm(span=f).mean() > H.close.ewm(span=sl).mean()).astype(float); st.iloc[:sl] = 0
        w = st.shift(1).reindex(d.index, method="ffill").fillna(0).values.astype(np.int64)
        yield "D hourly average crossover", f"{f}h over {sl}h", dict(lim=none, want=w, tpf=z, slf=z, maxhold=0)

def main():
    idx = {s: M5[s].index for s in BE}; arr = {s: tuple(M5[s][k].values for k in ("open", "high", "low", "close")) for s in BE}
    gens = {s: list(variants(s)) for s in BE}; rows = []; curves = {}
    for gi, gname in ((False, "any time"), (True, "only when the daily trend is on")):
        gate = {s: gate_for(s, gi) for s in BE}
        specs = [("sim", gens[BE[0]][j][0], gens[BE[0]][j][1], j) for j in range(len(gens[BE[0]]))]
        specs += [("grid", "C ping-pong grid", f"{g*100:g}% spacing, {K} lots", (g, K)) for g in (0.0025, 0.005, 0.01, 0.02, 0.04) for K in (5, 10, 20)]
        for kind, fam, name, key in specs:
            for cname, (fm, ft, slip) in COSTS.items():
                eqs = {}; ntr = 0; gs = 0.0; ip = 0
                for s in BE:
                    o, h, l, c = arr[s]
                    if kind == "sim":
                        kw = gens[s][key][2]; r = sim(o, h, l, c, kw["lim"], kw["want"], kw["tpf"], kw["slf"], kw["maxhold"], gate[s], fm, ft, slip, THRU)
                    else:
                        r = grid(o, h, l, c, key[0], key[1], gate[s], fm, ft, slip, THRU)
                    eqs[s] = r[0]; ntr += r[1]; gs += r[2]; ip += r[4]
                st, e = stats_of(eqs, idx); yrs = len(e) / 365
                rows.append(dict(family=fam, name=name, gate=gname, cost=cname, trades_yr=ntr / yrs / len(BE), gross_bps=(gs / ntr * 1e4 if ntr else 0.0),
                                 time_in=ip / sum(len(idx[s]) for s in BE) / (key[1] if kind == "grid" else 1), **{f"{p}_{k}": v for p, d in st.items() for k, v in d.items()}))
                curves[(fam, name, gname, cname)] = e
    R = pd.DataFrame(rows); R.to_csv("intraday_results.csv", index=False); pd.to_pickle(curves, "intraday_curves.pkl")
    return R

if __name__ == "__main__":
    R = main()
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    b = R[R.cost.str.startswith("Binance.US")]
    print(b[["family", "name", "gate", "trades_yr", "gross_bps", "time_in", "full_cagr", "is_cagr", "ho_cagr", "ho_maxdd", "ho_sharpe"]].round(3).to_string())
