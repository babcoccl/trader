import json, itertools, time
import numpy as np, pandas as pd
import bt

IS_END, HO_START = "2021-12-31", "2022-01-01"
UNIV = {"BTC": dict(fixed=["BTCUSDT"], cap=1.0), "BTC+ETH": dict(fixed=["BTCUSDT", "ETHUSDT"], cap=1.0),
        "Top 10": dict(n=10), "Top 20": dict(n=20)}
# fee per side; every scenario also pays 0.05% slippage per side
COSTS = {
    "Spot 0.90% (Coinbase spot taker)": dict(fee=0.0090),
    "Spot 0.50% (Coinbase spot maker)": dict(fee=0.0050),
    "Spot 0.40% (Kraken spot maker)": dict(fee=0.0040),
    "Spot 0.02% (Binance.US taker)": dict(fee=0.0002),
    "Perp 0.05% + funding (Kraken futures / Hyperliquid taker)": dict(fee=0.0005, perp=True),
    "Perp 0.02% + funding (maker)": dict(fee=0.0002, perp=True),
}
MID = "Spot 0.40% (Kraken spot maker)"
out = {}
def S(res, a=None, b=None): return {k: (None if pd.isna(v) else float(v)) for k, v in bt.stats(res, a, b).items()}
def three(res): return {"full": S(res), "is": S(res, None, IS_END), "ho": S(res, HO_START, None)}

# ---- benchmarks
bench = {
    "HODL BTC": bt.run(bt.target_weights("hold", fixed=["BTCUSDT"], cap=1), fee=0.004, band=0.9),
    "HODL 50/50 BTC+ETH": bt.run(bt.target_weights("hold", fixed=["BTCUSDT", "ETHUSDT"], cap=1), fee=0.004, rebalance="M", band=0),
    "HODL equal-weight Top 20": bt.run(bt.target_weights("hold", n=20, cap=1), fee=0.004, rebalance="M", band=0),
}
out["bench"] = {k: three(v) for k, v in bench.items()}

# ---- A. ensemble x universe x cost
A, runs = {}, {}
for u, kw in UNIV.items():
    w = bt.target_weights("ensemble", **kw)
    for c, ck in COSTS.items():
        r = bt.run(w, **ck); runs[(u, c)] = r
        A[f"{u}|{c}"] = three(r)
out["A"] = A

# ---- B. yearly returns
def yearly(e):
    y = e.groupby(e.index.year).last(); first = e.iloc[0]
    prev = y.shift(1); prev.iloc[0] = first
    return (y / prev - 1).round(4).to_dict()
out["yearly"] = {"Trend BTC+ETH": yearly(runs[("BTC+ETH", MID)].equity), "Trend BTC": yearly(runs[("BTC", MID)].equity),
                 "Trend Top 10": yearly(runs[("Top 10", MID)].equity),
                 **{k: yearly(v.equity) for k, v in bench.items()}}

# ---- C. single-rule dispersion at the mid cost
Cc, single = {}, {}
for u, kw in UNIV.items():
    for rule in bt.RULES:
        r = bt.run(bt.target_weights(rule, **kw), **COSTS[MID]); single[(u, rule)] = r
        Cc[f"{u}|{rule}"] = three(r)
out["C"] = Cc

# ---- D. walk-forward rule selection (BTC+ETH, mid cost): each Jan 1 pick the rule with best trailing-3y Sharpe
def walk_forward(u):
    rets = pd.DataFrame({rule: single[(u, rule)].equity.pct_change() for rule in bt.RULES}).fillna(0)
    parts, picks = [], {}
    for y in range(2021, 2027):
        tr = rets.loc[f"{y-3}-01-01":f"{y-1}-12-31"]
        best = (tr.mean() / tr.std()).idxmax(); picks[y] = best
        parts.append(rets.loc[f"{y}-01-01":f"{y}-12-31", best])
    r = pd.concat(parts); e = (1 + r).cumprod()
    return e, picks
wf = {}
for u in UNIV:
    e, picks = walk_forward(u)
    ens = runs[(u, MID)].equity.loc["2021-01-01":]
    hb = bench["HODL BTC"].equity.loc["2021-01-01":]
    f = lambda e: S(pd.DataFrame({"equity": e, "turnover": 0, "cost": 0, "funding": 0, "exposure": 0}))
    wf[u] = {"picks": picks, "walk_forward": f(e), "ensemble_same_period": f(ens), "hodl_btc_same_period": f(hb)}
out["D"] = wf

# ---- E. robustness on BTC+ETH ensemble, mid cost
w = bt.target_weights("ensemble", **UNIV["BTC+ETH"])
E = {"base": three(runs[("BTC+ETH", MID)]),
     "delay 2 days": three(bt.run(w, delay=2, **COSTS[MID])),
     "no rebalance band": three(bt.run(w, band=0.0, **COSTS[MID])),
     "wide band 0.4": three(bt.run(w, band=0.4, **COSTS[MID])),
     "slippage 0.25%": three(bt.run(w, slip=0.0025, **COSTS[MID]))}
out["E"] = E
def rolling(e, bench_e, n):
    a = e / e.shift(n) - 1; b = bench_e / bench_e.shift(n) - 1
    a, b = a.dropna(), b.dropna()
    return dict(pct_positive=float((a > 0).mean()), pct_beat_hodl=float((a > b).mean()), worst=float(a.min()), median=float(a.median()),
                hodl_worst=float(b.min()), hodl_median=float(b.median()), hodl_pct_positive=float((b > 0).mean()))
out["rolling"] = {u: {f"{n}d": rolling(runs[(u, MID)].equity, bench["HODL BTC"].equity, n) for n in (365, 730)} for u in UNIV}

# ---- F. stationary block bootstrap of Sharpe (mean block 20 days)
def boot(a, b, n=4000, block=20, seed=1):
    rng = np.random.default_rng(seed); T = len(a); res = []
    for _ in range(n):
        idx = np.empty(T, dtype=int); i = 0
        while i < T:
            s = rng.integers(T); L = rng.geometric(1 / block)
            seg = (s + np.arange(L)) % T; k = min(L, T - i); idx[i:i + k] = seg[:k]; i += k
        x, y = a[idx], b[idx]
        res.append((x.mean() / x.std() * np.sqrt(365), y.mean() / y.std() * np.sqrt(365), x.mean() * 365, y.mean() * 365))
    r = np.array(res); d = r[:, 0] - r[:, 1]
    q = lambda v: [float(np.quantile(v, p)) for p in (0.05, 0.5, 0.95)]
    return dict(strat_sharpe=q(r[:, 0]), hodl_sharpe=q(r[:, 1]), diff=q(d), p_strat_sharpe_gt0=float((r[:, 0] > 0).mean()),
                p_beats_hodl_sharpe=float((d > 0).mean()), strat_mean_ann=q(r[:, 2]))
Fb = {}
for u in UNIV:
    for lab, a, b in (("full", None, None), ("ho", HO_START, None)):
        x = runs[(u, MID)].equity.loc[a:b].pct_change().dropna().values
        y = bench["HODL BTC"].equity.loc[a:b].pct_change().dropna().values
        Fb[f"{u}|{lab}"] = boot(x, y)
out["F"] = Fb

# ---- G. sizing for a 15% drawdown halt (BTC+ETH ensemble)
G = {}
for cname in (MID, "Spot 0.90% (Coinbase spot taker)", "Spot 0.02% (Binance.US taker)", "Perp 0.05% + funding (Kraken futures / Hyperliquid taker)"):
    for k in (1.0, 0.5, 0.35, 0.25, 0.15):
        G[f"{cname}|{k}"] = three(bt.run(w * k, **COSTS[cname]))
out["G"] = G
def halts(e, lim=0.15):
    n, hwm, dates = 0, e.iloc[0], []
    for d, v in e.items():
        hwm = max(hwm, v)
        if v < hwm * (1 - lim): n += 1; dates.append(str(d.date())); hwm = v
    return dict(count=n, dates=dates)
out["halts"] = {f"{k}": halts(bt.run(w * k, **COSTS[MID]).equity) for k in (1.0, 0.5, 0.35, 0.25)}
# vol-targeted variant
out["voltarget"] = {f"{vt}": three(bt.run(bt.target_weights("ensemble", vol_target=vt, **UNIV["BTC+ETH"]), **COSTS[MID])) for vt in (0.6, 0.4, 0.25, 0.15)}

# ---- H. long/short on perps
H = {}
pc = COSTS["Perp 0.05% + funding (Kraken futures / Hyperliquid taker)"]
for u, kw in UNIV.items():
    H[f"{u}|long-only"] = three(runs[(u, "Perp 0.05% + funding (Kraken futures / Hyperliquid taker)")])
    H[f"{u}|long-short"] = three(bt.run(bt.target_weights("ensemble", allow_short=True, **kw), **pc))
    ws = bt.target_weights("ensemble", allow_short=True, **kw).clip(upper=0)
    H[f"{u}|short leg only"] = three(bt.run(ws, **pc))
out["H"] = H

# ---- series for charts
ser = pd.DataFrame({"Trend BTC+ETH": runs[("BTC+ETH", MID)].equity, "Trend BTC": runs[("BTC", MID)].equity,
                    "Trend Top 10": runs[("Top 10", MID)].equity, "Trend Top 20": runs[("Top 20", MID)].equity,
                    "Trend BTC+ETH, Coinbase spot taker": runs[("BTC+ETH", "Spot 0.90% (Coinbase spot taker)")].equity,
                    **{k: v.equity for k, v in bench.items()}})
ser.to_csv("results_equity.csv")
runs[("BTC+ETH", MID)].to_csv("results_btceth_mid.csv")
json.dump(out, open("results.json", "w"), indent=1)
print("ok")
