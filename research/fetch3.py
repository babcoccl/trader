import io, re, zipfile, urllib.request, time, os
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
def http(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r: return r.read()
        except Exception: time.sleep(1 + i)
    return None
os.makedirs("data/m5", exist_ok=True)
for sym in ("BTCUSDT", "ETHUSDT"):
    ks, marker = [], ""
    while True:
        x = http(f"{S3}?prefix=data/spot/monthly/klines/{sym}/5m/&marker={marker}").decode()
        k = re.findall(r"<Key>([^<]*\.zip)</Key>", x); ks += k
        if "<IsTruncated>true" not in x or not k: break
        marker = re.findall(r"<Key>([^<]*)</Key>", x)[-1]
    def one(k):
        z = zipfile.ZipFile(io.BytesIO(http(f"https://data.binance.vision/{k}")))
        d = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if not str(d.iloc[0, 0]).isdigit(): d = d.iloc[1:]
        d = d.iloc[:, [0, 1, 2, 3, 4, 7]].astype(float); d.columns = ["ts", "open", "high", "low", "close", "qvol"]
        d.loc[d.ts > 1e14, "ts"] /= 1000
        return d
    with ThreadPoolExecutor(12) as ex: rows = list(ex.map(one, ks))
    d = pd.concat(rows); d["t"] = pd.to_datetime(d.ts, unit="ms"); d = d.drop_duplicates("t").set_index("t").sort_index().drop(columns="ts")
    d.to_parquet(f"data/m5/{sym}.parquet"); print(sym, len(d), d.index.min(), d.index.max(), "gaps>5m:", int((d.index.to_series().diff() > pd.Timedelta("5min")).sum()))
