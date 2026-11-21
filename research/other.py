"""Other candidate strategies on the same engine, same costs (0.40% fee + 0.05% slippage per side unless stated)."""
import json, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt
C = bt.C; HO = "2022-01-01"; BE = ["BTCUSDT", "ETHUSDT"]
def S(r): return {p: {k: (None if pd.isna(v) else float(v)) for k, v in bt.stats(r, a, b).items()} for p, (a, b) in {"full": (None, None), "is": (None, "2021-12-31"), "ho": (HO, None)}.items()}
ens = bt.signal("ensemble")
out, eqs = {}, {}
def add(name, w, **kw):
    kw.setdefault("fee", 0.004); r = bt.run(w, **kw); out[name] = S(r); eqs[name] = r; return r
Z = lambda: pd.DataFrame(0.0, index=C.index, columns=C.columns)

add("Trend ensemble, BTC+ETH (base)", bt.target_weights("ensemble", fixed=BE, cap=1.0))
for vt in (0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15):
    add(f"Trend, volatility target {int(vt*100)}%", bt.target_weights("ensemble", fixed=BE, cap=1.0, vol_target=vt))

# BTC/ETH rotation: put the whole trend budget on whichever has the stronger 90-day return
w = Z(); m90 = C[BE] / C[BE].shift(90) - 1; lead = m90.idxmax(axis=1)
tot = ens[BE].fillna(0).mean(axis=1)
for a in BE: w[a] = np.where(lead == a, ens[a].fillna(0), 0.0)
add("BTC/ETH rotation (stronger of the two, trend-gated)", w)

# market-gated alt trend: top-10 ensemble, only while BTC's own trend signal is at least 0.5
w = bt.target_weights("ensemble", n=10); gate = (ens["BTCUSDT"] >= 0.5).astype(float)
add("Top 10 trend, only when BTC trend is on", w.mul(gate, axis=0))

# cross-sectional momentum: weekly, hold the k strongest of the top n by look-day return, only when BTC trend is on
def xsmom(n, k, look, gated=True):
    mem = bt.members(n); mom = (C / C.shift(look) - 1).where(mem)
    rk = mom.rank(axis=1, ascending=False)
    pick = ((rk <= k) & (mom > 0)).astype(float) / k
    wk = pd.Series(np.arange(len(C)) % 7 == 0, index=C.index)
    pick[~wk.values] = np.nan; pick = pick.ffill().fillna(0.0).where(mem, 0.0)
    return pick.mul(gate, axis=0) if gated else pick
for n, k, look in ((10, 3, 30), (10, 3, 90), (20, 5, 30), (20, 5, 90)):
    add(f"Cross-sectional momentum: top {k} of {n}, {look}-day", xsmom(n, k, look))
add("Cross-sectional momentum: top 3 of 10, 90-day, no BTC gate", xsmom(10, 3, 90, gated=False))

# short-term mean reversion on BTC+ETH: buy a sharp dip inside an uptrend, hold up to 5 days
def dip(drop, days, hold, trend=True):
    w = Z()
    for a in BE:
        c = C[a]; sig = (c / c.shift(days) - 1 < -drop)
        if trend: sig &= c > c.rolling(200).mean()
        w[a] = sig.astype(float).rolling(hold, min_periods=1).max() / len(BE)
    return w
for drop, days, hold in ((0.10, 3, 5), (0.15, 5, 5), (0.08, 2, 3)):
    add(f"Dip buying in an uptrend: -{int(drop*100)}% in {days} days, hold {hold}", dip(drop, days, hold), band=0)
add("Dip buying, no trend filter: -10% in 3 days, hold 5", dip(0.10, 3, 5, trend=False), band=0)
add("Dip buying in an uptrend at 0.02% fees: -10% in 3 days, hold 5", dip(0.10, 3, 5), band=0, fee=0.0002)

# slower / faster trend definitions, as a check that the base is not a lucky middle
add("Trend, weekly decisions only", bt.target_weights("ensemble", fixed=BE, cap=1.0).iloc[::7].reindex(C.index).ffill())
add("Trend ensemble at 0.02% fees", bt.target_weights("ensemble", fixed=BE, cap=1.0), fee=0.0002)
add("Trend, volatility target 40% at 0.02% fees", bt.target_weights("ensemble", fixed=BE, cap=1.0, vol_target=0.4), fee=0.0002)

json.dump(out, open("other_results.json", "w"), indent=1); pd.to_pickle(eqs, "other_runs.pkl")
f = lambda s: f"{s['cagr']*100:6.1f} {s['maxdd']*100:6.1f} {s['sharpe']:5.2f}"
print(f"{'':62s}   full: cagr maxdd sharpe |  2022+: cagr maxdd sharpe | turn  expo")
for k, v in out.items(): print(f"{k:62s} {f(v['full'])} | {f(v['ho'])} | {v['full']['turnover_yr']:5.1f} {v['full']['exposure']:.2f}")
