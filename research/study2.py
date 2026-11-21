import json, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt, carry
HO = "2022-01-01"; BE = ["BTCUSDT", "ETHUSDT"]; FEE = 0.004
out = {}
st = lambda r, a=None, b=None: {k: float(v) for k, v in bt.stats(r, a, b).items()}
def yearly(e):
    y = e.groupby(e.index.year).last(); p = y.shift(1); p.iloc[0] = e.iloc[0]
    return {str(k): round(float(v), 4) for k, v in (y / p - 1).items()}
def frame(e): return pd.DataFrame({"equity": e, "turnover": 0.0, "cost": 0.0, "funding": 0.0, "exposure": 0.0})

# ---------- 1. carry
TB = carry.TB
HL = carry.hl_funding(); hl0, hl1 = "2023-05-13", "2026-11-19"
tb_eq = lambda a, b: (1 + TB.loc[a:b] / 365).cumprod()
cr = {
 "Treasury bills (cash benchmark)": frame(tb_eq(carry.C_START, carry.C_END)),
 "Carry, always on (Binance funding)": carry.run(carry.always(BE), x=0.6),
 "Carry only when funding is high, else T-bills (Binance funding)": carry.run(carry.gated(BE), x=0.6, cash_rate=True),
 "Carry, always on, 0.40% spot fee": carry.run(carry.always(BE), x=0.6, fs=0.004),
}
ch = {
 "Treasury bills (cash benchmark)": frame(tb_eq(hl0, hl1)),
 "Carry, always on (Hyperliquid funding)": carry.run(carry.always(BE, HL), x=0.6, FRo=HL, start=hl0, end=hl1),
 "Carry only when funding is high, else T-bills (Hyperliquid funding)": carry.run(carry.gated(BE, FR=HL), x=0.6, cash_rate=True, FRo=HL, start=hl0, end=hl1),
 "Carry, always on (Binance funding)": carry.run(carry.always(BE), x=0.6, start=hl0, end=carry.C_END),
}
out["carry"] = {k: {"full": st(v), "ho": st(v, HO), "yearly": yearly(v.equity), "minmr": (None if "minmr" not in v else float(v.minmr.min()))} for k, v in cr.items()}
out["carry_hl"] = {k: {"full": st(v), "yearly": yearly(v.equity)} for k, v in ch.items()}
out["carry_lev"] = {str(x): {"full": st(r := carry.run(carry.always(BE), x=x)), "minmr": float(r.minmr.min())} for x in (0.5, 0.6, 0.67, 0.8)}
out["funding_by_year"] = {"Binance": {str(y): float(v) for y, v in carry.FR[BE].mean(axis=1).loc["2020":].groupby(lambda d: d.year).sum().items()},
                          "Hyperliquid": {str(y): float(v) for y, v in HL.mean(axis=1).dropna().groupby(lambda d: d.year).sum().items()},
                          "T-bill": {str(y): float(v) for y, v in (TB.loc["2020":] / 365).groupby(lambda d: d.year).sum().items()}}

# ---------- 2. sizing frontier
w = bt.target_weights("ensemble", fixed=BE, cap=1.0)
ks = (0.15, 0.25, 0.35, 0.5, 0.65, 0.8, 1.0); vts = (0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6)
stat_runs = {k: bt.run(w * k, fee=FEE) for k in ks}
vt_runs = {v: bt.run(bt.target_weights("ensemble", fixed=BE, cap=1.0, vol_target=v), fee=FEE) for v in vts}
def with_cash(r):
    ret = r.equity.pct_change().fillna(0) + (1 - r.exposure.shift(1).fillna(0)) * TB.reindex(r.index) / 365
    q = r.copy(); q["equity"] = (1 + ret).cumprod(); return q
fr = lambda r: {"full": st(r), "ho": st(r, HO), "full_cash": st(with_cash(r)), "ho_cash": st(with_cash(r), HO)}
out["frontier"] = {"static": {str(k): fr(r) for k, r in stat_runs.items()}, "voltarget": {str(v): fr(r) for v, r in vt_runs.items()}}

# ---------- 3. halt rule simulation: flat for 30 days after the limit is hit, then resume with a fresh high-water mark
def halt_sim(r, L, cool=30, c=FEE + 0.0005):
    ret = r.equity.pct_change().fillna(0).values; ex = r.exposure.values
    eq = np.empty(len(ret)); e = 1.0; hwm = 1.0; until = -1; n = 0; days = 0
    for i in range(len(ret)):
        if i <= until:
            days += 1
            if i == until: e *= 1 - ex[i] * c; hwm = e
        else:
            e *= 1 + ret[i]; hwm = max(hwm, e)
            if e < hwm * (1 - L): n += 1; e *= 1 - ex[i] * c; until = i + cool
        eq[i] = e
    q = frame(pd.Series(eq, index=r.index)); s = st(q); s2 = st(q, HO)
    return {"halts": n, "days_halted": days, "cagr": s["cagr"], "maxdd": s["maxdd"], "ho_cagr": s2["cagr"]}
Ls = (0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5)
out["halt"] = {"static": {str(k): {str(L): halt_sim(r, L) for L in Ls} for k, r in stat_runs.items()},
               "voltarget": {str(v): {str(L): halt_sim(r, L) for L in Ls} for v, r in vt_runs.items()}}

# ---------- 4. bootstrap: chance of hitting each limit within 3 years, and the 3-year return distribution
def boot3(ret, n=3000, T=1095, block=20, seed=7):
    rng = np.random.default_rng(seed); N = len(ret); dd = np.empty(n); cg = np.empty(n)
    for j in range(n):
        idx = np.empty(T, dtype=int); i = 0
        while i < T:
            s = rng.integers(N); Lb = rng.geometric(1 / block); k = min(Lb, T - i); idx[i:i + k] = (s + np.arange(k)) % N; i += k
        e = np.cumprod(1 + ret[idx]); dd[j] = (e / np.maximum.accumulate(e) - 1).min(); cg[j] = e[-1] ** (1 / 3) - 1
    return {"p_hit": {str(L): float((dd <= -L).mean()) for L in Ls}, "cagr_q": [float(np.quantile(cg, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)],
            "dd_q": [float(np.quantile(dd, q)) for q in (0.1, 0.5, 0.9)], "p_loss": float((cg < 0).mean())}
def both(r):
    x = r.equity.pct_change().dropna()
    return {"full": boot3(x.values), "ho": boot3(x.loc[HO:].values)}
out["boot"] = {"static": {str(k): both(r) for k, r in stat_runs.items()}, "voltarget": {str(v): both(r) for v, r in vt_runs.items()}}

# ---------- 5. blends over 2020-01..2026-10: trend sleeve + what the idle cash does
gc = cr["Carry only when funding is high, else T-bills (Binance funding)"].equity.pct_change().fillna(0)
tbr = (TB / 365)
def blend(r, idle):
    r = r.loc[carry.C_START:carry.C_END]; ret = r.equity.pct_change().fillna(0); free = 1 - r.exposure.shift(1).fillna(0)
    add = 0 if idle == "none" else free * (tbr.reindex(r.index) if idle == "tbill" else gc.reindex(r.index))
    return frame((1 + ret + add).cumprod())
out["blend"] = {name: {idle: {"full": st(b := blend(r, idle)), "ho": st(b, HO)} for idle in ("none", "tbill", "carry")}
                for name, r in (("Trend at 35% size", stat_runs[0.35]), ("Trend at 50% size", stat_runs[0.5]), ("Trend, volatility target 30%", vt_runs[0.3]), ("Trend, volatility target 40%", vt_runs[0.4]))}
json.dump(out, open("results2.json", "w"), indent=1)
pd.DataFrame({**{f"static {k}": r.equity for k, r in stat_runs.items()}, **{f"vt {v}": r.equity for v, r in vt_runs.items()}}).to_csv("results2_equity.csv")
pd.DataFrame({k: v.equity for k, v in cr.items()}).to_csv("results2_carry.csv"); pd.DataFrame({k: v.equity for k, v in ch.items()}).to_csv("results2_carry_hl.csv")

p = lambda x: f"{x*100:5.1f}"
print("CARRY (Binance funding, 2020-01..2026-10)")
for k, v in out["carry"].items(): print(f" {k[:60]:60s} full {p(v['full']['cagr'])} ho {p(v['ho']['cagr'])} dd {p(v['full']['maxdd'])} | " + " ".join(f"{y[2:]}:{v['yearly'][y]*100:.1f}" for y in v["yearly"]))
print("CARRY (2023-05..2026-11)")
for k, v in out["carry_hl"].items(): print(f" {k[:60]:60s} cagr {p(v['full']['cagr'])} dd {p(v['full']['maxdd'])} | " + " ".join(f"{y[2:]}:{v['yearly'][y]*100:.1f}" for y in v["yearly"]))
print("lev", {x: (round(v['full']['cagr']*100, 1), round(v['minmr'], 2)) for x, v in out["carry_lev"].items()})
print("funding/yr", {k: {y: round(x*100, 1) for y, x in v.items()} for k, v in out["funding_by_year"].items()})
print("FRONTIER  size: full cagr/dd | ho cagr/dd | +cash full, ho | P(hit L in 3y) full-pool / ho-pool for L=15,20,25,30,40 | 3y cagr q10/q50 (ho pool) | halts@L")
for kind in ("static", "voltarget"):
    for k, v in out["frontier"][kind].items():
        b = out["boot"][kind][k]; h = out["halt"][kind][k]
        print(f" {kind[:6]} {k:5s} {p(v['full']['cagr'])}/{p(v['full']['maxdd'])} | {p(v['ho']['cagr'])}/{p(v['ho']['maxdd'])} | {p(v['full_cash']['cagr'])} {p(v['ho_cash']['cagr'])} | "
              + " ".join(f"{b['full']['p_hit'][L]*100:3.0f}/{b['ho']['p_hit'][L]*100:3.0f}" for L in ("0.15", "0.2", "0.25", "0.3", "0.4"))
              + f" | {p(b['ho']['cagr_q'][0])} {p(b['ho']['cagr_q'][2])} / full {p(b['full']['cagr_q'][0])} {p(b['full']['cagr_q'][2])} | "
              + " ".join(f"{L}:{h[L]['halts']}h {h[L]['cagr']*100:.0f}%" for L in ("0.15", "0.2", "0.25", "0.3", "0.4")))
print("BLEND 2020-01..2026-10 (full cagr / ho cagr / full dd)")
for k, v in out["blend"].items(): print(f" {k:30s} " + " | ".join(f"{i}: {p(x['full']['cagr'])} {p(x['ho']['cagr'])} {p(x['full']['maxdd'])}" for i, x in v.items()))
