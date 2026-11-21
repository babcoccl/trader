"""Independent check: implement spec 1.2 / 1.3 from the text using only the committed fixtures, compare with research/bt.py outputs."""
import json, math, numpy as np, pandas as pd
D = pd.read_csv("fixtures/btc_eth_daily.csv", parse_dates=["date"]).set_index("date")
SY = ["BTC-USD", "ETH-USD"]
def sma(c, n): m = c.rolling(n, min_periods=n).mean(); return (c > m).astype(float).where(m.notna())
def momentum(c, n): p = c.shift(n); return (c > p).astype(float).where(p.notna())
def breakout(c, n):
    hi = c.rolling(n, min_periods=n).max(); lo = c.rolling(n // 2, min_periods=n // 2).min()
    s = pd.Series(np.nan, index=c.index); s[c >= hi] = 1.0; s[c <= lo] = 0.0
    return s.ffill().where(hi.notna())
def rules(c):
    r = {f"sma{n}": sma(c, n) for n in (50, 100, 200)}; r.update({f"momentum{n}": momentum(c, n) for n in (30, 90, 180)}); r.update({f"breakout{n}": breakout(c, n) for n in (20, 55, 100)}); return r
def ensemble(c): r = rules(c); return (sum(v.fillna(0) for v in r.values()) / 9).where(r["sma200"].notna())
def annual_vol(c): return c.pct_change().ewm(span=30, min_periods=20).std() * math.sqrt(365)
def target_weights(sig, vol, vt=0.40):
    w = {s: (0.0 if (pd.isna(sig[s]) or pd.isna(vol[s])) else sig[s] / len(sig) * min(1.0, vt / vol[s])) for s in sig}
    g = sum(w.values()); return {s: (v / g if g > 1.0 else v) for s, v in w.items()}
E_ = pd.read_csv("fixtures/expected_signals.csv", parse_dates=["date"])
worst = 0.0; W = pd.DataFrame(index=D.index, columns=SY, dtype=float); sig = {}; vol = {}
for s in SY:
    c = D[f"{s}_close"]; ex = E_[E_.symbol == s].set_index("date"); r = rules(c); sig[s] = ensemble(c); vol[s] = annual_vol(c)
    for k, v in {**r, "ensemble": sig[s], "annual_vol": vol[s]}.items():
        a, b = v.values, ex[k].values; assert (np.isnan(a) == np.isnan(b)).all(), (s, k, "NaN pattern differs")
        worst = max(worst, np.nanmax(np.abs(a - b)))
for d in D.index:
    w = target_weights({s: sig[s][d] for s in SY}, {s: vol[s][d] for s in SY})
    for s in SY: W.at[d, s] = w[s]
for s in SY: worst = max(worst, np.abs(W[s].values - E_[E_.symbol == s].target_weight.values).max())
print("rules/ensemble/vol/weights: max abs difference vs research =", worst)

def backtest(taker, slip, band, start="2018-04-01", end="2026-11-19"):
    idx = D.loc[start:end].index; Wd = W.shift(1).reindex(idx).fillna(0.0)
    units = {s: 0.0 for s in SY}; cash = 1.0; eq = []; turn = 0.0; expo = []
    for d in idx:
        o = {s: D.at[d, f"{s}_open"] for s in SY}; e = cash + sum(units[s] * o[s] for s in SY); traded = 0.0; new = dict(units)
        for s in SY:
            cur = units[s] * o[s] / e; tgt = Wd.at[d, s]
            if abs(tgt - cur) > band * max(tgt, cur) or (tgt == 0 and cur != 0):
                traded += abs(tgt - cur) * e; new[s] = tgt * e / o[s]
        if traded > 0:
            units = new; cash = e - traded * (taker + slip) - sum(units[s] * o[s] for s in SY); turn += traded / e
        v = sum(units[s] * D.at[d, f"{s}_close"] for s in SY); eq.append(cash + v); expo.append(v / (cash + v))
    return pd.Series(eq, index=idx), turn, np.mean(expo)
def stats(e):
    yrs = len(e) / 365; r = e.pct_change().dropna()
    return dict(cagr=(e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1, maxdd=(e / e.cummax() - 1).min(), sharpe=r.mean() / r.std() * math.sqrt(365))
X = json.load(open("fixtures/expected_backtest.json"))["runs"]; wb = 0.0
for k, v in X.items():
    e, turn, expo = backtest(v["taker_fee"], v["slippage"], v["band"]); f = stats(e); h = stats(e.loc["2022-01-01":])
    diffs = [abs(f[m] - v["full"][m]) for m in f] + [abs(h[m] - v["from_2022"][m]) for m in h] + [abs(turn / (len(e) / 365) - v["full"]["turnover_yr"]), abs(expo - v["full"]["exposure"]), abs(e.iloc[-1] - v["final_equity"]) / v["final_equity"]]
    wb = max(wb, max(diffs)); print(f"{k:22s} cagr {f['cagr']*100:6.2f}% from2022 {h['cagr']*100:6.2f}%  max diff {max(diffs):.2e}")
print("backtest: worst difference vs research =", wb)
