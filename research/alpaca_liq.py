import json, time, urllib.request, urllib.parse, statistics as st
def g(u):
    r=urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0","accept":"application/json"}), timeout=30); return json.load(r)
B="https://data.alpaca.markets/v1beta3/crypto/us"; SY=["BTC/USD","ETH/USD"]; q=urllib.parse.quote(",".join(SY),safe=",")
def walk(levels, usd):
    """average fill price for spending `usd` against price-sorted levels [(price, size)]; None if the book shown is too thin"""
    left=usd; qty=0.0
    for p,s in levels:
        take=min(left, p*s); qty+=take/p; left-=take
        if left<=1e-9: return usd/qty
    return None
rows={s:[] for s in SY}; cb={s:[] for s in SY}
for k in range(24):
    try:
        ob=g(f"{B}/latest/orderbooks?symbols={q}")["orderbooks"]
        for s in SY:
            bids=sorted([(x["p"],x["s"]) for x in ob[s]["b"]],reverse=True); asks=sorted([(x["p"],x["s"]) for x in ob[s]["a"]])
            mid=(bids[0][0]+asks[0][0])/2; r={"spread":(asks[0][0]-bids[0][0])/mid*1e4,"mid":mid,"nb":len(bids),"na":len(asks),
               "bid10":sum(p*z for p,z in bids if p>=mid*0.999),"ask10":sum(p*z for p,z in asks if p<=mid*1.001),"bid50":sum(p*z for p,z in bids if p>=mid*0.995),"ask50":sum(p*z for p,z in asks if p<=mid*1.005),
               "bidtot":sum(p*z for p,z in bids),"asktot":sum(p*z for p,z in asks),"t":ob[s]["t"]}
            for usd in (500,2000,5000,10000):
                a=walk(asks,usd); b=walk(bids,usd)
                r[f"buy{usd}"]=None if a is None else (a/mid-1)*1e4; r[f"sell{usd}"]=None if b is None else (1-b/mid)*1e4
            rows[s].append(r)
            c=g(f"https://api.exchange.coinbase.com/products/{s.replace('/','-')}/book?level=1")
            cb[s].append(((float(c["asks"][0][0])-float(c["bids"][0][0]))/((float(c["asks"][0][0])+float(c["bids"][0][0]))/2)*1e4, (float(c["asks"][0][0])+float(c["bids"][0][0]))/2, mid))
    except Exception as e: print("sample error", e)
    time.sleep(8)
med=lambda xs: st.median([x for x in xs if x is not None]) if any(x is not None for x in xs) else None
for s in SY:
    R=rows[s]; print(f"\n{s}: {len(R)} samples over ~{len(R)*9//60} min, last book time {R[-1]['t']}, levels shown bid/ask {R[-1]['nb']}/{R[-1]['na']}")
    print("  spread bps: median %.1f, min %.1f, max %.1f | Coinbase spread median %.2f bps"%(med([r['spread'] for r in R]),min(r['spread'] for r in R),max(r['spread'] for r in R),st.median([c[0] for c in cb[s]])))
    print("  Alpaca mid vs Coinbase mid, bps: median %.1f, worst %.1f"%(st.median([(c[2]/c[1]-1)*1e4 for c in cb[s]]),max(abs((c[2]/c[1]-1)*1e4) for c in cb[s])))
    print("  depth within 0.1%%: bid $%.0f ask $%.0f | within 0.5%%: bid $%.0f ask $%.0f | whole book shown: bid $%.0f ask $%.0f (medians)"%tuple(med([r[k] for r in R]) for k in ("bid10","ask10","bid50","ask50","bidtot","asktot")))
    for usd in (500,2000,5000,10000):
        b=[r[f"buy{usd}"] for r in R]; se=[r[f"sell{usd}"] for r in R]
        print(f"  market order ${usd}: cost vs mid, bps: buy median {med(b)}, sell median {med(se)}; samples where the shown book was too thin: buy {sum(x is None for x in b)}, sell {sum(x is None for x in se)}")
bars=g(f"{B}/bars?symbols={q}&timeframe=1Day&start=2026-10-20T00:00:00Z&limit=1000")["bars"]
for s in SY:
    v=[x["v"]*x["vw"] for x in bars[s]][:-1]; n=[x["n"] for x in bars[s]][:-1]
    print(f"\n{s} daily traded on Alpaca, last {len(v)} full days: median ${st.median(v):,.0f}, min ${min(v):,.0f}, max ${max(v):,.0f}; trades per day median {st.median(n):.0f}")
m=g(f"{B}/bars?symbols={q}&timeframe=5Min&start=2026-11-19T00:00:00Z&end=2026-11-20T00:00:00Z&limit=1000")["bars"]
for s in SY: print(f"{s} 5-minute bars on 2026-11-19: {len(m[s])} of 288 had a trade")
