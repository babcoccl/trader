import json, time, urllib.request, statistics as st
def g(u):
    r=urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0","accept":"application/json"}), timeout=40); return json.load(r)
B="https://www.bitstamp.net/api/v2"; SY=["btcusd","ethusd"]
def walk(levels, usd):
    left=usd; qty=0.0
    for p,s in levels:
        take=min(left,p*s); qty+=take/p; left-=take
        if left<=1e-9: return usd/qty
    return None
rows={s:[] for s in SY}
for k in range(24):
    for s in SY:
        try:
            ob=g(f"{B}/order_book/{s}/"); bids=[(float(p),float(z)) for p,z in ob["bids"]]; asks=[(float(p),float(z)) for p,z in ob["asks"]]
            mid=(bids[0][0]+asks[0][0])/2; r={"spread":(asks[0][0]-bids[0][0])/mid*1e4,"bid10":sum(p*z for p,z in bids if p>=mid*0.999),"ask10":sum(p*z for p,z in asks if p<=mid*1.001),"bid50":sum(p*z for p,z in bids if p>=mid*0.995),"ask50":sum(p*z for p,z in asks if p<=mid*1.005),"top":min(bids[0][0]*bids[0][1],asks[0][0]*asks[0][1])}
            for usd in (500,2000,5000,10000): r[f"buy{usd}"]=(walk(asks,usd)/mid-1)*1e4; r[f"sell{usd}"]=(1-walk(bids,usd)/mid)*1e4
            rows[s].append(r)
        except Exception as e: print("err",s,e)
    time.sleep(7)
med=lambda v: st.median(v)
for s in SY:
    R=rows[s]; print(f"\n{s}: {len(R)} samples")
    print("  spread bps: median %.2f, max %.2f | smaller side at best price median $%.0f"%(med([r['spread'] for r in R]),max(r['spread'] for r in R),med([r['top'] for r in R])))
    print("  depth within 0.1%%: bid $%.0f ask $%.0f | within 0.5%%: bid $%.0f ask $%.0f"%tuple(med([r[k] for r in R]) for k in ("bid10","ask10","bid50","ask50")))
    for usd in (500,2000,5000,10000): print(f"  market ${usd}: buy {med([r[f'buy{usd}'] for r in R]):.2f} bps, sell {med([r[f'sell{usd}'] for r in R]):.2f} bps vs mid")
    d=g(f"{B}/ohlc/{s}/?step=86400&limit=33")["data"]["ohlc"][:-1][-31:]
    v=[float(x["volume"])*(float(x["high"])+float(x["low"]))/2 for x in d]
    print(f"  daily traded, last {len(v)} full days: median ${med(v):,.0f}, min ${min(v):,.0f}, max ${max(v):,.0f}")
    m=g(f"{B}/ohlc/{s}/?step=300&limit=288&start=1795046400")["data"]["ohlc"]   # 2026-11-19 00:00 UTC
    print(f"  5-minute bars from 2026-11-19 00:00 UTC: {len(m)} returned, {sum(1 for x in m if float(x['volume'])>0)} with volume; first ts {m[0]['timestamp']}")
    t=g(f"{B}/transactions/{s}/?time=hour"); print(f"  trades in the last hour: {len(t)}")
    # spread history is not available publicly; sample hourly closes vs Coinbase instead is skipped
