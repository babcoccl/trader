import json, io, os, re, zipfile, urllib.request, time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ever = json.load(open("data/ever_top30.json"))
os.makedirs("data/funding", exist_ok=True)
def http(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r: return r.read()
        except Exception as e:
            time.sleep(1 + i)
    return None
def keys(sym):
    out, marker = [], ""
    while True:
        x = http(f"{S3}?prefix=data/futures/um/monthly/fundingRate/{sym}/&marker={marker}")
        if x is None: return out
        x = x.decode()
        k = re.findall(r"<Key>([^<]*\.zip)</Key>", x)
        out += k
        if "<IsTruncated>true" not in x or not k: return out
        marker = k[-1] + ".CHECKSUM"
def one(sym):
    out = f"data/funding/{sym}.csv"
    if os.path.exists(out): return sym, -1
    ks = keys(sym); src = sym
    if not ks:
        ks = keys("1000" + sym); src = "1000" + sym
    rows = []
    for k in ks:
        b = http(f"https://data.binance.vision/{k}")
        if b is None: continue
        z = zipfile.ZipFile(io.BytesIO(b))
        d = pd.read_csv(z.open(z.namelist()[0]))
        rows.append(d)
    if rows:
        d = pd.concat(rows); d.to_csv(out, index=False)
        return sym, len(d)
    open(out, "w").write("calc_time,funding_interval_hours,last_funding_rate\n")
    return sym, 0
with ThreadPoolExecutor(12) as ex:
    res = list(ex.map(one, ever))
print([r for r in res if r[1] == 0])
print(len(res))
