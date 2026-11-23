"""Regenerate the fixtures for specs G1 and G3 (docs/SPECS.md section E): daily high and low, the expected regime label
per day and asset, and the expected dip-buying trigger. Needs data/ built by the fetch scripts (see README.md).
Row for date D holds values computed from candles up to and including D, which is what the 00:05 UTC cycle sees on D+1."""
import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt
BE = ["BTCUSDT", "ETHUSDT"]; NM = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}
C = bt.C[BE].loc[:"2026-11-19"]
def hl(s):
    k = pd.read_csv(f"{bt.D}/klines/{s}.csv"); k.index = pd.to_datetime(k.iloc[:, 0], unit="ms" if k.iloc[0, 0] < 1e14 else "us").dt.floor("D")
    return k.iloc[:, 2].reindex(C.index), k.iloc[:, 3].reindex(C.index)
H = pd.DataFrame({s: hl(s)[0] for s in BE}); L = pd.DataFrame({s: hl(s)[1] for s in BE})
assert (H >= C - 1e-9).all().all() and (L <= C + 1e-9).all().all()
d = pd.DataFrame({"date": C.index.strftime("%Y-%m-%d")})
for s in BE:
    d[f"{NM[s]}_high"] = H[s].values; d[f"{NM[s]}_low"] = L[s].values
d.to_csv("fixtures/btc_eth_daily_hl.csv", index=False)
def adx(h, l, c, n=14):
    up = h.diff(); dn = -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0); mdm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, min_periods=n).mean()
    pdi = 100 * pdm.ewm(alpha=1 / n, min_periods=n).mean() / atr; mdi = 100 * mdm.ewm(alpha=1 / n, min_periods=n).mean() / atr
    return (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=1 / n, min_periods=n).mean()
ens = bt.signal("ensemble")[BE].reindex(C.index)
vol = C.pct_change().ewm(span=30, min_periods=20).std() * np.sqrt(365); vp = vol.rolling(365, min_periods=180).rank(pct=True)
z = (C - C.rolling(20).mean()) / C.rolling(20).std()
rows = []
for s in BE:
    k = (ens[s] * 9).round(); a = adx(H[s], L[s], C[s])
    x = pd.DataFrame({"date": C.index.strftime("%Y-%m-%d"), "symbol": NM[s], "rules_positive": k.values})
    x["trend"] = np.where(ens[s].isna(), "", np.where(k >= 7, "uptrend", np.where(k <= 2, "downtrend", "mixed")))
    x["vol_percentile"] = vp[s].values; x["volatility"] = np.where(vp[s].isna(), "", np.where(vp[s] < 0.5, "calm", "volatile"))
    x["adx14"] = a.values; x["sideways"] = np.where(a.isna(), "", np.where(a < 20, "true", "false"))
    x["z20"] = z[s].values; x["dip_trigger"] = np.where(ens[s].isna() | z[s].isna(), "", np.where((x["trend"] == "mixed").values & (z[s] < -2).values, "true", "false"))
    rows.append(x)
R = pd.concat(rows); R.to_csv("fixtures/expected_regimes.csv", index=False, float_format="%.12g")
h = R[R.date >= "2022-01-01"]
print(h.groupby("symbol").trend.value_counts(normalize=True).round(3).to_dict()); print(h.groupby("symbol").volatility.value_counts(normalize=True).round(3).to_dict())
print("sideways share", (h.sideways == "true").mean().round(3), "dip triggers 2022+", (h.dip_trigger == "true").sum(), "all", (R.dip_trigger == "true").sum())
