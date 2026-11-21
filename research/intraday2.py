import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt, intraday as I
BE = I.BE; HO = I.HO
curves = pd.read_pickle("intraday_curves.pkl"); R = pd.read_csv("intraday_results.csv")
CN = "Binance.US (0% maker, 0.02% taker)"; fm, ft, slip = I.COSTS[CN]
# benchmark: simply hold whenever the daily trend gate is on (same engine, same costs)
eqs = {}
for s in BE:
    d = I.M5[s]; n = len(d); g = I.gate_for(s, True)
    r = I.sim(d.open.values, d.high.values, d.low.values, d.close.values, np.full(n, np.nan), np.ones(n, dtype=np.int64), np.zeros(n), np.zeros(n), 0, g, fm, ft, slip, I.THRU)
    eqs[s] = r[0]; print(s, "benchmark trades", r[1], "time in", round(r[4] / n, 3))
st, bench = I.stats_of(eqs, {s: I.M5[s].index for s in BE})
print("BENCHMARK hold when daily trend on:", {k: {a: round(b, 3) for a, b in v.items()} for k, v in st.items()})
hod = {}
for s in BE:
    d = I.M5[s]; n = len(d)
    r = I.sim(d.open.values, d.high.values, d.low.values, d.close.values, np.full(n, np.nan), np.ones(n, dtype=np.int64), np.zeros(n), np.zeros(n), 0, np.ones(n, dtype=np.bool_), fm, ft, slip, I.THRU); hod[s] = r[0]
st2, hodl = I.stats_of(hod, {s: I.M5[s].index for s in BE}); print("HODL BTC+ETH:", {k: {a: round(b, 3) for a, b in v.items()} for k, v in st2.items()})
br = bench.pct_change().dropna()
def alpha(e, a=None, b=None):
    y = e.pct_change().dropna().loc[a:b]; x = br.reindex(y.index).fillna(0)
    X = np.column_stack([np.ones(len(x)), x.values]); beta, *_ = np.linalg.lstsq(X, y.values, rcond=None)
    res = y.values - X @ beta; se = np.sqrt(res.var(ddof=2) * np.linalg.inv(X.T @ X)[0, 0])
    return beta[0] * 365, beta[1], beta[0] / se
rows = []
for (fam, name, gname, cname), e in curves.items():
    if cname != CN or gname == "any time": continue
    af, bf, tf = alpha(e); ah, bh, th = alpha(e, HO)
    rr = R[(R.family == fam) & (R.name == name) & (R.gate == gname) & (R.cost == cname)].iloc[0]
    rows.append(dict(family=fam, name=name, is_cagr=rr.is_cagr, ho_cagr=rr.ho_cagr, ho_dd=rr.ho_maxdd, ho_sharpe=rr.ho_sharpe, beta_ho=bh, alpha_ho=ah, t_ho=th, alpha_full=af, t_full=tf))
A = pd.DataFrame(rows); A.to_csv("intraday_alpha.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
print(A.round(3).to_string(index=False))

# E. entry timing for the daily trend sleeve's own trades
print("\nENTRY TIMING: cost versus the day's open, in bps of trade value (negative = better than open). Baseline = market at open: +4 bps (0.02% fee + 0.02% slippage)")
ens = bt.signal("ensemble")
for side in ("buy", "sell"):
    for x in (0.0, 0.0025, 0.005, 0.01, 0.02):
        res = []; fills = []
        for s in BE:
            d = I.M5[s]; day = d.resample("D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            ch = ens[s].diff().shift(1).reindex(day.index)      # signal change at day D-1 close -> execute on day D
            days = day[(ch > 0) if side == "buy" else (ch < 0)]
            for per, (a, b) in (("full", (None, None)), ("ho", (HO, None))):
                q = days.loc[a:b]
                if side == "buy":
                    lim = q.open * (1 - x); hit = q.low < lim * (1 - I.THRU); px = np.where(hit, lim, q.close * (1 + slip) * (1 + ft)); c = px / q.open - 1
                else:
                    lim = q.open * (1 + x); hit = q.high > lim * (1 + I.THRU); px = np.where(hit, lim, q.close * (1 - slip) * (1 - ft)); c = -(px / q.open - 1)
                res.append((per, c.mean() * 1e4, len(q), c.std() * 1e4)); fills.append((per, hit.mean()))
        f = lambda per: (np.mean([r[1] for r in res if r[0] == per]), sum(r[2] for r in res if r[0] == per), np.mean([r[3] for r in res if r[0] == per]), np.mean([h[1] for h in fills if h[0] == per]))
        a = f("full"); b = f("ho")
        print(f" {side} limit {x*100:4.2f}% from open, else close | full: {a[0]:7.1f} bps (n={a[1]}, sd {a[2]:.0f}, filled {a[3]*100:.0f}%) | 2022+: {b[0]:7.1f} bps (n={b[1]}, sd {b[2]:.0f}, filled {b[3]*100:.0f}%)")
