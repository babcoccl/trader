import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt
BE=["BTCUSDT","ETHUSDT"]; HO="2022-01-01"
w=bt.target_weights("ensemble",fixed=BE,cap=1.0,vol_target=0.4)
print("24/7 spot, 40% volatility target, band 20%: cost per side (fee+slippage) -> full CAGR/maxDD | 2022+ CAGR/maxDD | turnover/yr")
for name,c in (("Binance.US taker (reference) 0.04%",0.0004),("On-chain Base 0.12%",0.0012),("Alpaca crypto maker 0.17%",0.0017),("Alpaca crypto taker 0.27%",0.0027),("Crypto.com maker 0.27%",0.0027),("Bitstamp maker 0.32%",0.0032),("Crypto.com taker / Coinbase maker 0.52%",0.0052),("Gemini maker 0.62%",0.0062),("Coinbase taker 0.92%",0.0092)):
    r=bt.run(w,fee=c,slip=0.0); f=bt.stats(r); h=bt.stats(r,HO)
    print(f" {name:42s} {f['cagr']*100:5.1f}/{f['maxdd']*100:6.1f} | {h['cagr']*100:5.1f}/{h['maxdd']*100:6.1f} | {f['turnover_yr']:.1f}")
# ETF route: signal from the 16:00 New York price every calendar day, trades only at 09:30 New York on weekdays
px={}
for s in BE:
    d=pd.read_parquet(f"{bt.D}/m5/{s}.parquet"); d.index=d.index.tz_localize("UTC").tz_convert("America/New_York")
    t=d.index; c16=d.close[(t.hour==15)&(t.minute==55)]; o930=d.open[(t.hour==9)&(t.minute==30)]
    c16.index=c16.index.normalize().tz_localize(None); o930.index=o930.index.normalize().tz_localize(None)
    px[s]=(c16[~c16.index.duplicated()],o930[~o930.index.duplicated()])
C=pd.DataFrame({s:px[s][0] for s in BE}).loc["2017-08-18":"2026-10-30"].asfreq("D").ffill(); O=pd.DataFrame({s:px[s][1] for s in BE}).reindex(C.index).ffill()
sig=sum(f(C).fillna(0) for f in bt.RULES.values())/len(bt.RULES); sig=sig.where(bt.RULES["sma200"](C).notna()).fillna(0)
vol=C.pct_change().ewm(span=30,min_periods=20).std()*np.sqrt(365)
W=(sig/2*(0.4/vol).clip(upper=1.0).fillna(0)); W=W.mul((1/W.sum(axis=1)).clip(upper=1.0).fillna(1.0),axis=0)
def sim(weekday_only, cost, er, band=0.2, start="2018-04-01"):
    idx=C.loc[start:].index; Wd=W.shift(1).reindex(idx).fillna(0).values; Op=O.reindex(idx).values; Cl=C.reindex(idx).values
    u=np.zeros(2); cash=1.0; eq=np.empty(len(idx)); turn=0.0; wd=idx.dayofweek.values<5
    for i in range(len(idx)):
        if (wd[i] or not weekday_only):
            e=cash+(u*Op[i]).sum(); cur=u*Op[i]/e; tgt=Wd[i]; dlt=tgt-cur
            do=(np.abs(dlt)>band*np.maximum(tgt,cur))|((tgt==0)&(cur!=0))
            if do.any():
                n=np.abs(dlt[do]).sum()*e; u[do]=tgt[do]*e/Op[i][do]; cash=e-n*cost-(u*Op[i]).sum(); turn+=n/e
        cash-=(u*Cl[i]).sum()*er/365
        eq[i]=cash+(u*Cl[i]).sum()
    e=pd.Series(eq,index=idx); st=lambda x:((x.iloc[-1]/x.iloc[0])**(365/len(x))-1,(x/x.cummax()-1).min())
    return st(e),st(e.loc[HO:]),turn/(len(idx)/365)
print("\nSame rules on New York hours (signal at 16:00 ET, trade at 09:30 ET next day), to Oct 2026:")
for name,kw in (("Every day, 0.04% cost (24/7 venue reference)",dict(weekday_only=False,cost=0.0004,er=0.0)),
                ("Weekdays only, 0.03% cost + 0.25%/yr fund fee (ETF route)",dict(weekday_only=True,cost=0.0003,er=0.0025)),
                ("Weekdays only, 0.10% cost + 0.25%/yr fund fee (ETF, pessimistic)",dict(weekday_only=True,cost=0.0010,er=0.0025)),
                ("Every day, 0.12% cost (on-chain reference)",dict(weekday_only=False,cost=0.0012,er=0.0))):
    f,h,t=sim(**kw); print(f" {name:66s} {f[0]*100:5.1f}/{f[1]*100:6.1f} | {h[0]*100:5.1f}/{h[1]*100:6.1f} | {t:.1f}")
