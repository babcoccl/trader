import json, glob, os, re
import pandas as pd, numpy as np
info = json.load(open("data/exchangeInfo.json"))
base = {x["symbol"]: x["baseAsset"] for x in info["symbols"] if x["quoteAsset"] == "USDT"}
STABLE = {"USDC","BUSD","TUSD","PAX","USDP","DAI","FDUSD","USDS","USDSB","SUSD","UST","USTC","EUR","GBP","AUD","EURI","AEUR","USD1","XUSD","BFUSD","USDE","PYUSD","RLUSD","EURC","FRAX","USDD","GUSD","LUSD","USDJ","VAI","PAXG","XAUT","WBTC","BETH","WBETH","BNSOL","WSTETH","USDSOLD","BTTC","EUL0","U","CRCLB","SNDKB","SPCXB"}
frames = {}
for f in glob.glob("data/klines/*.csv"):
    s = os.path.basename(f)[:-4]
    b = base[s]
    if b in STABLE or re.search(r"(UP|DOWN|BULL|BEAR)$", b) or b.startswith("USD") or b.endswith("USD"):
        continue
    if os.path.getsize(f) < 100: continue
    d = pd.read_csv(f)
    if len(d) < 30: continue
    d["date"] = pd.to_datetime(d.ts, unit="ms")
    d = d.set_index("date")
    # a trading gap of 5+ days (relisting, token swap) starts a separate asset
    gaps = d.index.to_series().diff().dt.days.fillna(1)
    seg = (gaps > 5).cumsum()
    if seg.max() > 0:
        print("split", s, [str(x.date()) for x in d.index[gaps > 5]])
    for k, g in d.groupby(seg.values):
        if len(g) < 30: continue
        frames[s if k == seg.max() else f"{s}#{k}"] = g
print(len(frames))
O = pd.DataFrame({s: d.open for s, d in frames.items()}).sort_index()
C = pd.DataFrame({s: d.close for s, d in frames.items()}).sort_index()
Q = pd.DataFrame({s: d.quote_volume for s, d in frames.items()}).sort_index()
# drop the final partial day
last = C.index.max(); print("last", last)
O, C, Q = O.iloc[:-1], C.iloc[:-1], Q.iloc[:-1]
# zero-volume days (halts) -> NaN volume but keep price
O.to_parquet("data/open.parquet"); C.to_parquet("data/close.parquet"); Q.to_parquet("data/qvol.parquet")
print(C.shape, C.index.min(), C.index.max())
# point-in-time universe: top N by trailing 90d median quote volume, >= 120d listing history
medv = Q.rolling(90, min_periods=60).median()
hist = C.notna().cumsum()
elig = (hist >= 120) & Q.rolling(7).sum().gt(0)
rank = medv.where(elig).rank(axis=1, ascending=False)
rank.to_parquet("data/rank.parquet")
for dt in ["2018-06-01","2020-01-01","2021-06-01","2022-04-01","2023-01-01","2024-06-01","2026-10-01"]:
    r = rank.loc[dt].dropna().sort_values()
    print(dt, [s[:-4] for s in r.index[:20]])
ever = sorted({s for dt in rank.index[::30] for s in rank.loc[dt].dropna().sort_values().index[:30]})
print(len(ever)); json.dump(ever, open("data/ever_top30.json","w"))
# data sanity
r = C.pct_change(fill_method=None)
print("max abs daily ret:", r.abs().max().sort_values(ascending=False).head(8).to_dict())
print("gaps in BTC index:", pd.date_range(C.index.min(), C.index.max()).difference(C.index).size)
