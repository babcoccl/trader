import numpy as np, pandas as pd, warnings, io, contextlib; warnings.filterwarnings("ignore")
src=open("regimes.py").read()
with contextlib.redirect_stdout(io.StringIO()): exec(src[:src.index("b=bt.run(full(base)")])
vt=lambda s,v=0.4: (s/2)*(v/vol).clip(upper=1.0).fillna(0.0)
P=positions(TRIG["2 sd under"],mix,EXIT["hold 5d"])
V={"B uptrend only":vt(ens.where(up,0)),"G trend+dip in mixed":(base+0.25*P).clip(upper=0.5),"H switch":(vt(ens.where(up,0))+0.25*P.where(~up,0)).clip(upper=0.5)}
ra=bt.run(full(base),fee=0.004,slip=0.0005).equity.pct_change()
for n,w in V.items():
    r=bt.run(full(w),fee=0.004,slip=0.0005).equity.pct_change(); d=(r-ra)
    for a,b in (("2018-04-01","2021-12-31"),(HO,END)):
        x=d.loc[a:b].dropna(); print(f"{n:22s} {a[:4]}: diff {x.mean()*365*100:5.1f} pts/yr, t {x.mean()/x.std()*np.sqrt(len(x)):5.2f}", end=" | ")
    y=r.loc[HO:END]; print(" by year:",{k:round(((1+v).prod()-1)*100,1) for k,v in y.groupby(y.index.year)})
y=ra.loc[HO:END]; print("A by year:",{k:round(((1+v).prod()-1)*100,1) for k,v in y.groupby(y.index.year)})
# today's regime
print(ens.iloc[-3:].round(2).to_string(), vp.iloc[-1].round(2).to_dict(), C.index[-1])
