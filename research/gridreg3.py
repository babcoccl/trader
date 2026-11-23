import numpy as np, pandas as pd, warnings, itertools; warnings.filterwarnings("ignore")
import gridreg as G
from gridreg import *
rows=[]
for tf,n in (("4h",14),("1h",14),("1h",48)):
    gates={}; arr={}; pred=[]
    for s in BE:
        d=M5[s].loc[T0:T1]; arr[s]=(d.index,)+tuple(d[k].values for k in ("open","high","low","close"))
        H=M5[s].resample(tf).agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
        a=adx(H,n); w=20 if tf=="4h" else 48
        rng=rank((H.high.rolling(w).max()-H.low.rolling(w).min())/H.close) if False else ((H.high.rolling(w).max()-H.low.rolling(w).min())/H.close)
        rq=rng.rolling(24*90 if tf=="1h" else 6*90,min_periods=200).rank(pct=True)
        for gn,gv in ((f"ADX({n}) on {tf} bars under 20",a<20),(f"ADX({n}) on {tf} bars under 20 and narrow range",(a<20)&(rq<1/3))):
            g=gv.shift(1).fillna(False).astype(bool)      # bar t uses data to the close of bar t-1
            gates.setdefault(gn,{})[s]=g.reindex(d.index,method="ffill").fillna(False).values.astype(np.bool_)
    for gn in gates:
        for g,K,(mn,(grace,stop)),(cn,(fm,ft,slip)) in itertools.product((0.01,0.015,0.02,0.03,0.05),(5,10),list(EXITS.items())[:2]+list(EXITS.items())[4:],COSTS.items()):
            eqs=[]; ntr=0
            for s in BE:
                idx,o,h,l,c=arr[s]; r=grid(o,h,l,c,g,K,gates[gn][s],grace,stop,fm,ft,slip,THRU); eqs.append(pd.Series(r[0],index=idx).resample("D").last()); ntr+=r[1]
            e=sum(eqs)/2; row=dict(regime=gn,share=np.mean([gates[gn][s].mean() for s in BE]),spacing=g,lots=K,mode=mn,cost=cn,trips_yr=ntr/(len(e)/365)/2)
            for p,(a_,b_) in (("is",(T0,"2021-12-31")),("ho",(HO,T1))):
                for k,v in st(e.loc[a_:b_]).items(): row[f"{p}_{k}"]=v
            rows.append(row)
R=pd.DataFrame(rows); R.to_csv("gridreg3_results.csv",index=False); pd.set_option("display.width",250)
print(R.groupby(["regime","mode","cost"],sort=False).agg(share=("share","first"),is_med=("is_cagr","median"),ho_min=("ho_cagr","min"),ho_med=("ho_cagr","median"),ho_max=("ho_cagr","max"),dd=("ho_maxdd","min"),pos=("ho_cagr",lambda x:(x>0).sum()),n=("ho_cagr","size")).round(3).to_string())
