"""Feasibility backtester: daily trend rules on a point-in-time liquid universe.

Signals use data up to the close of day t. Orders fill at the open of day t+1
(delay=1) plus fee and slippage. Long-only unless allow_short. Gross exposure <= 1x.
"""
import json, os
import numpy as np, pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
O = pd.read_parquet(f"{D}/open.parquet"); C = pd.read_parquet(f"{D}/close.parquet")
RANK = pd.read_parquet(f"{D}/rank.parquet")
EVER = json.load(open(f"{D}/ever_top30.json"))
START, END = "2018-04-01", "2026-11-19"
ANN = 365

# stablecoin-like filter: 90d realised vol under 10% annualised is not a trading asset
_r = C.pct_change(fill_method=None)
_vol90 = _r.rolling(90, min_periods=60).std() * np.sqrt(ANN)
RANK = RANK.where(_vol90 > 0.10)
RANK = RANK.rank(axis=1)

def load_funding():
    F = {}
    for s in EVER:
        p = f"{D}/funding/{s}.csv"
        if not os.path.exists(p): continue
        d = pd.read_csv(p)
        if d.empty: continue
        d["date"] = pd.to_datetime(d.calc_time, unit="ms").dt.floor("D")
        F[s] = d.groupby("date").last_funding_rate.sum()
    F = pd.DataFrame(F).reindex(C.index)
    return F
FUND_DEFAULT = 0.0003  # 0.01% per 8h when no history exists
_F = None
def funding():
    global _F
    if _F is None:
        f = load_funding().reindex(columns=C.columns)
        _F = f.where(f.notna(), FUND_DEFAULT).where(C.notna())
    return _F

def members(n, fixed=None):
    """Boolean membership, refreshed on the first day of each month from the prior day's rank."""
    if fixed:
        m = pd.DataFrame(False, index=C.index, columns=C.columns)
        m[fixed] = C[fixed].notna()
        return m
    r = RANK.shift(1)
    first = r.index.to_series().dt.is_month_start
    m = (r <= n).astype(float)
    m[~first.values] = np.nan
    m = m.ffill().fillna(0.0).astype(bool)
    return m & C.notna()

RULES = {}
for n in (50, 100, 200):
    RULES[f"sma{n}"] = (lambda n: lambda c: (c > c.rolling(n, min_periods=n).mean()).astype(float).where(c.rolling(n, min_periods=n).mean().notna()))(n)
for n in (30, 90, 180):
    RULES[f"mom{n}"] = (lambda n: lambda c: (c > c.shift(n)).astype(float).where(c.shift(n).notna()))(n)
def _don(n):
    def f(c):
        hi = c.rolling(n, min_periods=n).max(); lo = c.rolling(n // 2, min_periods=n // 2).min()
        s = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
        s = s.mask(c >= hi, 1.0).mask(c <= lo, 0.0)
        return s.ffill().where(hi.notna())
    return f
for n in (20, 55, 100):
    RULES[f"don{n}"] = _don(n)
_sig_cache = {}
def signal(rule):
    if rule in _sig_cache: return _sig_cache[rule]
    if rule == "ensemble":
        s = sum(signal(r).fillna(0) for r in RULES) / len(RULES)
        s = s.where(signal("sma200").notna())
    elif rule == "hold":
        s = C.notna().astype(float)
    else:
        s = RULES[rule](C)
    _sig_cache[rule] = s
    return s

def target_weights(rule, n=20, fixed=None, vol_target=None, cap=0.25, allow_short=False, gross=1.0):
    m = members(n, fixed)
    s = signal(rule)
    if allow_short: s = 2 * s - 1
    s = s.where(m).fillna(0.0)
    cnt = m.sum(axis=1).clip(lower=1)
    w = s.div(cnt, axis=0)
    if vol_target:
        vol = (_r.ewm(span=30, min_periods=20).std() * np.sqrt(ANN))
        w = w * (vol_target / vol).clip(upper=1.0).fillna(0.0)
    w = w.clip(lower=-cap, upper=cap)
    g = w.abs().sum(axis=1)
    w = w.mul((gross / g).clip(upper=1.0).fillna(1.0), axis=0)
    return w

def run(w, fee=0.0005, slip=0.0005, perp=False, delay=1, band=0.2, rebalance=None, start=START, end=END, equity0=1.0):
    """w: target weights decided at close of day t. Fills at open of day t+delay.
    band: trade an asset only if |target-current| exceeds band*max(target,current), or the target is 0.
    rebalance: optional 'M' to trade only on month starts (benchmarks)."""
    cols = [c for c in w.columns if w[c].abs().sum() > 0]
    idx = C.loc[start:end].index
    W = w[cols].shift(delay).reindex(idx).fillna(0.0).values
    Op = O[cols].reindex(idx).values; Cl = C[cols].ffill().reindex(idx).values
    Fu = funding()[cols].reindex(idx).fillna(0.0).values if perp else None
    ms = idx.to_series().dt.is_month_start.values
    units = np.zeros(len(cols)); cash = equity0
    eq = np.empty(len(idx)); turn = np.zeros(len(idx)); costs = np.zeros(len(idx)); fund = np.zeros(len(idx)); expo = np.zeros(len(idx))
    lastpx = np.zeros(len(cols))
    lastvalid = np.array([idx.get_indexer([C[c].last_valid_index()], method="ffill")[0] if C[c].last_valid_index() >= idx[0] else -1 for c in cols])
    for i in range(len(idx)):
        o = Op[i]; ok = ~np.isnan(o)
        px = np.where(ok, o, lastpx)
        ok = ok | ((i > lastvalid) & (units != 0))  # delisted: close at the last traded price
        e = cash + np.nansum(units * px)
        cur = units * px / e
        tgt = W[i]
        d = tgt - cur
        do = ok & ((np.abs(d) > band * np.maximum(np.abs(tgt), np.abs(cur))) | ((tgt == 0) & (cur != 0)))
        if rebalance == "M" and not (ms[i] or i == 0):
            do = ok & (tgt == 0) & (cur != 0)
        if do.any():
            notional = np.abs(d[do]).sum() * e
            c = notional * (fee + slip)
            units[do] = tgt[do] * e / px[do]
            cash = e - c - np.nansum(units * px)
            turn[i] = notional / e; costs[i] = c / e
        cl = Cl[i]; lastpx = np.where(np.isnan(cl), lastpx, cl)
        pos = units * lastpx
        if perp:
            f = np.nansum(pos * Fu[i]); cash -= f; fund[i] = f / e
        eq[i] = cash + np.nansum(pos)
        expo[i] = np.nansum(np.abs(pos)) / eq[i]
    return pd.DataFrame({"equity": eq, "turnover": turn, "cost": costs, "funding": fund, "exposure": expo}, index=idx)

def stats(res, start=None, end=None):
    r = res.loc[start:end]
    e = r.equity; ret = e.pct_change().dropna()
    yrs = len(e) / ANN
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = (e / e.cummax() - 1).min()
    sh = ret.mean() / ret.std() * np.sqrt(ANN) if ret.std() > 0 else np.nan
    return dict(cagr=cagr, vol=ret.std() * np.sqrt(ANN), sharpe=sh, maxdd=dd, calmar=cagr / abs(dd) if dd < 0 else np.nan,
                turnover_yr=r.turnover.sum() / yrs, cost_yr=r.cost.sum() / yrs, funding_yr=r.funding.sum() / yrs,
                exposure=r.exposure.mean(), mult=e.iloc[-1] / e.iloc[0])
