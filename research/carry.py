"""Funding carry: long spot, short the same number of units of perpetual, collect funding.

G: gross capital share per asset decided at the close of day t, filled at the open of day t+delay.
x: share of that capital held as spot; the rest (1-x) is USD margin behind the short. Perp leverage on margin = x/(1-x).
Costs are paid on both legs. The short is marked on perp prices where we have them (BTC, ETH), else on spot.
"""
import numpy as np, pandas as pd
import bt

FR = bt.load_funding()                       # raw daily funding, NaN where no history (no default fill)
HI = None
def _high():
    global HI
    if HI is None:
        HI = {}
        for s in FR.columns:
            try:
                k = pd.read_csv(f"{bt.D}/klines/{s}.csv"); k.index = pd.to_datetime(k.ts, unit="ms"); HI[s] = k.high
            except Exception: pass
        HI = pd.DataFrame(HI).reindex(bt.C.index)
    return HI
PO, PC = bt.O.copy(), bt.C.copy()
for s in ("BTCUSDT", "ETHUSDT"):
    p = pd.read_csv(f"{bt.D}/perp/{s}.csv", index_col="date", parse_dates=True)
    PO.loc[p.index.intersection(PO.index), s] = p.open.reindex(PO.index).dropna()
    PC.loc[p.index.intersection(PC.index), s] = p.close.reindex(PC.index).dropna()
TB = pd.read_csv(f"{bt.D}/tbill.csv", parse_dates=["date"]).set_index("date").rate.reindex(bt.C.index).ffill().bfill() / 100
C_START, C_END = "2020-01-01", "2026-10-31"

def run(G, x=0.5, fs=0.0002, fp=0.0005, slip=0.0005, lev_lo=0.6, lev_hi=1.5, gband=0.25, cash_rate=False,
        delay=1, start=C_START, end=C_END, maint=0.05, FRo=None):
    cols = [c for c in G.columns if G[c].abs().sum() > 0]
    idx = bt.C.loc[start:end].index
    Gt = G[cols].shift(delay).reindex(idx).fillna(0.0).values
    So = bt.O[cols].reindex(idx).values; Sc = bt.C[cols].ffill().reindex(idx).values
    Po = PO[cols].reindex(idx).values; Pc = PC[cols].ffill().reindex(idx).values
    F = (FR if FRo is None else FRo)[cols].reindex(idx).fillna(0.0).values; H = _high()[cols].reindex(idx).values
    r = TB.reindex(idx).values if cash_rate else np.zeros(len(idx))
    L = x / (1 - x)
    n = len(cols); u = np.zeros(n); M = np.zeros(n); pprev = np.zeros(n); cash = 1.0
    eq = np.empty(len(idx)); turn = np.zeros(len(idx)); cost = np.zeros(len(idx)); fund = np.zeros(len(idx)); gross = np.zeros(len(idx))
    minmr = np.full(len(idx), np.nan); nreb = 0
    for i in range(len(idx)):
        so, po = So[i], Po[i]; ok = ~np.isnan(so) & ~np.isnan(po)
        so_ = np.where(ok, so, 0.0); po_ = np.where(ok, po, pprev)
        M = M - u * (po_ - pprev) * (u > 0); pprev = np.where(u > 0, po_, pprev)
        E = cash + (u * so_).sum() + M.sum()
        gcur = (u * so_ + M) / E; gt = Gt[i]
        with np.errstate(divide="ignore", invalid="ignore"): lev = np.where(M > 0, u * po_ / M, np.where(u > 0, np.inf, L))
        do = ok & ((np.abs(gt - gcur) > gband * np.maximum(gt, gcur)) | ((gt == 0) & (u > 0)) | ((u > 0) & ((lev > L * lev_hi) | (lev < L * lev_lo))))
        if do.any():
            un = np.where(do, x * gt * E / np.where(ok, so, 1.0), u)
            d = (np.abs(un - u) * so_)[do].sum(); c = d * (fs + fp + 2 * slip)
            u = un; M = np.where(do, (1 - x) * gt * E, M); pprev = np.where(do, po_, pprev)
            cash = E - c - (u * so_).sum() - M.sum(); turn[i] = d / E; cost[i] = c / E; nreb += 1
        sc, pc = Sc[i], Pc[i]; sc_ = np.nan_to_num(sc); pc_ = np.where(np.isnan(pc), pprev, pc)
        h = np.where(np.isnan(H[i]), pc_, H[i])
        with np.errstate(divide="ignore", invalid="ignore"):
            mr = np.where(u > 0, (M - u * (h * (po_ / np.where(so_ > 0, so_, 1)) - pprev)) / (u * h), np.nan)
        if (u > 0).any(): minmr[i] = np.nanmin(mr)
        M = M - u * (pc_ - pprev); pprev = np.where(u > 0, pc_, pprev)
        f = (u * pc_ * F[i]).sum(); M = M + u * pc_ * F[i]
        cash += cash * r[i] / 365
        e = cash + (u * sc_).sum() + M.sum()
        fund[i] = f / e; eq[i] = e; gross[i] = ((u * sc_).sum() + M.sum()) / e
    out = pd.DataFrame({"equity": eq, "turnover": turn, "cost": cost, "funding": -fund, "exposure": gross, "minmr": minmr}, index=idx)
    out.attrs["rebalances"] = nreb
    return out

def hl_funding():
    f = {}
    for coin in ("BTC", "ETH"):
        d = pd.read_csv(f"{bt.D}/hl_funding_{coin}.csv", parse_dates=["t"])
        f[coin + "USDT"] = d.groupby(d.t.dt.floor("D")).fundingRate.sum()
    return pd.DataFrame(f).reindex(bt.C.index)

def always(assets, FR=FR):
    g = pd.DataFrame(0.0, index=bt.C.index, columns=bt.C.columns)
    for a in assets: g[a] = FR[a].notna().astype(float) / len(assets)
    return g

def gated(assets, look=14, thr_in=0.08, thr_out=0.03, FR=FR):
    """On when trailing-mean funding (annualised) is above thr_in, off below thr_out."""
    g = pd.DataFrame(0.0, index=bt.C.index, columns=bt.C.columns)
    for a in assets:
        m = FR[a].rolling(look, min_periods=look).mean() * 365
        s = pd.Series(np.nan, index=m.index); s[m > thr_in] = 1.0; s[m < thr_out] = 0.0
        g[a] = s.ffill().fillna(0.0) / len(assets)
    return g

def topk(n=10, k=3, look=14, thr_in=0.08, thr_out=0.03, hold=7):
    """Among the n most liquid assets with funding history, hold the k with the highest trailing funding, re-picked every `hold` days."""
    m = FR.rolling(look, min_periods=look).mean() * 365
    mem = bt.members(n)[FR.columns] & FR.notna()
    m = m.where(mem)
    g = pd.DataFrame(0.0, index=bt.C.index, columns=bt.C.columns)
    cur = []
    for i, d in enumerate(m.index):
        if i % hold == 0:
            row = m.loc[d].dropna()
            keep = [a for a in cur if a in row.index and row[a] > thr_out]
            cand = [a for a in row.sort_values(ascending=False).index if row[a] > thr_in and a not in keep]
            cur = (keep + cand)[:k]
        for a in cur: g.at[d, a] = 1.0 / k
    return g
