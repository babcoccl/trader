import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt
BE=["BTCUSDT","ETHUSDT"]; HO="2022-01-01"; FEE=0.004; SLIP=0.0005
C=bt.C[BE]; O=bt.O[BE].reindex(C.index)
def fund(s):
    d=pd.read_csv(f"data/funding/{s}.csv"); t=pd.to_datetime(d.calc_time,unit="ms").dt.floor("D")
    return d.last_funding_rate.groupby(t).sum()          # funding paid by longs per day
F=pd.DataFrame({s:fund(s) for s in BE}).reindex(C.index)
print("funding from",F.dropna().index[0].date(),"to",F.dropna().index[-1].date())
f7=F.rolling(7).mean(); pct=f7.rolling(365,min_periods=180).rank(pct=True)
ens=bt.signal("ensemble")[BE]
fwd=O.shift(-8)/O.shift(-1)-1
print("\n== next-7-day spot return after the signal day, by funding state (mean %, n days)")
for per,(a,b) in {"2020-2021":("2020-03-01","2021-12-31"),"2022+":(HO,None)}.items():
    for name,m in {"7d funding in top 10% of past year":pct>=0.9,"middle 80%":(pct>0.1)&(pct<0.9),"bottom 10%":pct<=0.1,"7d funding negative":f7<0,
                   "negative, trend off":(f7<0)&(ens<=2/9+1e-9),"top 10%, trend on":(pct>=0.9)&(ens>=7/9-1e-9),"all days trend on":ens>=7/9-1e-9,"all days trend off":ens<=2/9+1e-9}.items():
        x=fwd.where(m).loc[a:b].stack()
        # non-overlapping-ish t-stat: divide n by 7
        t=x.mean()/x.std()*np.sqrt(len(x)/7) if len(x)>14 else np.nan
        print(f"  {per:9s} {name:36s} {x.mean()*100:6.2f}%  n={len(x):5d}  t~{t:5.2f}")
base=bt.target_weights("ensemble",fixed=BE,cap=1.0,vol_target=0.4)[BE]
vol=bt._r[BE].ewm(span=30,min_periods=20).std()*np.sqrt(365); vp=vol.rolling(365,min_periods=180).rank(pct=True)
def show(name,w):
    full=pd.DataFrame(0.0,index=bt.C.index,columns=bt.C.columns); full[BE]=w.reindex(full.index).fillna(0.0)
    r=bt.run(full,fee=FEE,slip=SLIP); h=bt.stats(r,HO); g=bt.stats(r.loc["2020-03-01":"2021-12-31"],"2020-03-01")
    x=r.loc[HO:]; td=(x.turnover>0).sum()/(len(x)/365)
    print(f"  {name:52s} 2020-21 {g['cagr']*100:6.1f}% dd {g['maxdd']*100:6.1f} | 2022+ {h['cagr']*100:5.1f}% dd {h['maxdd']*100:6.1f} sharpe {h['sharpe']:.2f} trade days/yr {td:4.0f}")
print("\n== overlays on the trend sleeve (BTC+ETH, 0.45% per side)")
show("trend sleeve as is",base)
show("halve when funding in top 10% of past year",base*np.where(pct>=0.9,0.5,1.0))
show("zero when funding in top 10% of past year",base*np.where(pct>=0.9,0.0,1.0))
show("halve when funding in top 20%",base*np.where(pct>=0.8,0.5,1.0))
neg=(f7<0)
show("plus 25% per asset while 7d funding negative",(base+0.25*neg).clip(upper=0.5))
show("plus 25% per asset, funding negative and trend off",(base+0.25*(neg&(ens<=2/9+1e-9))).clip(upper=0.5))
show("only 25% per asset while 7d funding negative (alone)",0.25*neg)
show("zero when volatility in bottom 25% of past year",base*np.where(vp<=0.25,0.0,1.0))
show("zero when volatility in bottom 50% of past year",base*np.where(vp<=0.5,0.0,1.0))
show("zero when volatility in top 25% of past year",base*np.where(vp>=0.75,0.0,1.0))
print("\nshare of days 7d funding negative 2022+:",(neg.loc[HO:].mean()*100).round(1).to_dict(),"| now:",f7.dropna().iloc[-1].round(5).to_dict(), "pct",pct.dropna().iloc[-1].round(2).to_dict())
