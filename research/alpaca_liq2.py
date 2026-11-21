import json, time, urllib.request, urllib.parse, statistics as st, datetime as dt
def g(u):
    for i in range(4):
        try:
            r=urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0","accept":"application/json"}), timeout=30); return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(5+5*i); continue
            return {"err":e.code}
        except Exception as e: time.sleep(2)
    return {"err":"retry"}
B="https://data.alpaca.markets/v1beta3/crypto/us"
m=g(f"{B}/bars?symbols={urllib.parse.quote('BTC/USD,ETH/USD',safe=',')}&timeframe=5Min&start=2026-11-19T00:00:00Z&end=2026-11-19T23:59:00Z&limit=1000")["bars"]
for s in m: print(s,"5-min bars 11-19:",len(m[s]),"with at least one trade:",sum(1 for x in m[s] if x["n"]>0),"trades:",sum(x["n"] for x in m[s]),"bars with zero volume:",sum(1 for x in m[s] if x["v"]==0))
res={}
end=dt.datetime(2026,11,20,12,tzinfo=dt.timezone.utc)
for s in ("BTC/USD","ETH/USD"):
    out=[]
    for d in range(28):
        for h in (1,5,9,13,17,21):
            t=(end-dt.timedelta(days=d)).replace(hour=h,minute=7)
            if t>end: continue
            x=g(f"{B}/quotes?symbols={urllib.parse.quote(s,safe='')}&start={t.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=1")
            if "err" in x or not x.get("quotes",{}).get(s): continue
            q=x["quotes"][s][0]; mid=(q["ap"]+q["bp"])/2
            out.append((t.weekday()>=5,h,(q["ap"]-q["bp"])/mid*1e4,q["bs"]*q["bp"],q["as"]*q["ap"]))
            time.sleep(0.15)
    res[s]=out; sp=[o[2] for o in out]; sz=[min(o[3],o[4]) for o in out]
    qs=lambda v,p: sorted(v)[int(p*(len(v)-1))]
    print(f"\n{s}: {len(out)} quotes over 28 days. spread bps median {st.median(sp):.1f}, 90th pct {qs(sp,.9):.1f}, max {max(sp):.1f}. smaller side at best price: median ${st.median(sz):,.0f}, 10th pct ${qs(sz,.1):,.0f}")
    we=[o[2] for o in out if o[0]]; wd=[o[2] for o in out if not o[0]]
    print("   weekday median %.1f (n=%d) | weekend median %.1f, 90th pct %.1f (n=%d)"%(st.median(wd),len(wd),st.median(we),qs(we,.9),len(we)))
    for h in (1,5,9,13,17,21):
        v=[o[2] for o in out if o[1]==h]; print(f"   {h:02d}:00 UTC median {st.median(v):.1f} max {max(v):.1f}")
