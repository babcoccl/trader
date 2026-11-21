import io, re, zipfile, urllib.request, time, os
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
def http(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r: return r.read()
        except Exception: time.sleep(1 + i)
    return None
os.makedirs("data/perp", exist_ok=True)
for sym in ("BTCUSDT", "ETHUSDT"):
    x = http(f"{S3}?prefix=data/futures/um/monthly/klines/{sym}/1d/").decode()
    ks = re.findall(r"<Key>([^<]*\.zip)</Key>", x)
    def one(k):
        z = zipfile.ZipFile(io.BytesIO(http(f"https://data.binance.vision/{k}")))
        d = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if not str(d.iloc[0, 0]).isdigit(): d = d.iloc[1:]
        return d.iloc[:, :5].astype(float)
    with ThreadPoolExecutor(12) as ex: rows = list(ex.map(one, ks))
    d = pd.concat(rows); d.columns = ["ts", "open", "high", "low", "close"]
    d["date"] = pd.to_datetime(d.ts, unit="ms"); d = d.drop_duplicates("date").set_index("date").sort_index()
    d.to_csv(f"data/perp/{sym}.csv"); print(sym, len(d), d.index.min(), d.index.max())
t = pd.read_csv(io.BytesIO(http("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3&cosd=2017-01-01")))
t.columns = ["date", "rate"]; t["rate"] = pd.to_numeric(t.rate, errors="coerce"); t.to_csv("data/tbill.csv", index=False)
print(t.tail(2), t.rate.isna().sum())
