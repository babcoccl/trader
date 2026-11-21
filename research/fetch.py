import json, time, os, sys
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

BASE = "https://data-api.binance.vision/api/v3/klines"
info = json.load(open("data/exchangeInfo.json"))
syms = [(x["symbol"], x["baseAsset"], x["status"]) for x in info["symbols"] if x["quoteAsset"] == "USDT" and x["symbol"].isascii()]

def get(url):
    for i in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(20 * (i + 1)); continue
            if e.code == 400:
                return []
            time.sleep(2)
        except Exception:
            time.sleep(2)
    raise RuntimeError(url)

def fetch(sym):
    out = f"data/klines/{sym}.csv"
    if os.path.exists(out):
        return sym, -1
    rows, start = [], 0
    while True:
        k = get(f"{BASE}?symbol={sym}&interval=1d&limit=1000&startTime={start}")
        if not k:
            break
        rows += k
        if len(k) < 1000:
            break
        start = k[-1][0] + 86400000
    df = pd.DataFrame([[r[0], r[1], r[2], r[3], r[4], r[5], r[7]] for r in rows],
                      columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])
    df.to_csv(out, index=False)
    return sym, len(df)

os.makedirs("data/klines", exist_ok=True)
with ThreadPoolExecutor(6) as ex:
    for i, (s, n) in enumerate(ex.map(fetch, [s[0] for s in syms])):
        if i % 50 == 0:
            print(i, s, n, flush=True)
print("done")
