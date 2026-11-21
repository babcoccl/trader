import json, urllib.request, time, pandas as pd
def post(body):
    for i in range(6):
        try:
            req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())
        except Exception as e:
            err = e; time.sleep(2 + 2 * i)
    raise err
for coin in ("BTC", "ETH"):
    start = int(pd.Timestamp("2023-01-01").timestamp() * 1000); rows = []
    while True:
        x = post({"type": "fundingHistory", "coin": coin, "startTime": start})
        if not x: break
        rows += x; start = x[-1]["time"] + 1
        if len(x) < 500: break
        time.sleep(0.25)
    d = pd.DataFrame(rows); d["t"] = pd.to_datetime(d.time, unit="ms"); d["fundingRate"] = d.fundingRate.astype(float)
    d[["t", "fundingRate", "premium"]].to_csv(f"data/hl_funding_{coin}.csv", index=False)
    print(coin, len(d), d.t.min(), d.t.max())
    print((d.groupby(d.t.dt.year).fundingRate.agg(["sum", "count"])).round(4))
