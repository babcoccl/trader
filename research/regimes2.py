import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
src=open("regimes.py").read(); exec(src[:src.index("b=bt.run(full(base)")])
D=pd.read_csv("regime_mr.csv"); M=D[D.regime=="mixed (3-6 on)"]
print(M[["trigger","exit","2018-2021 n","2018-2021 net","2018-2021 t","2022-2026 n","2022-2026 net","2022-2026 t","with trend 2022+","dd"]].round(2).to_string(index=False))
rng=np.random.default_rng(7)
def cagr(P): return bt.stats(bt.run(full((base+0.25*P).clip(upper=0.5)),fee=0.004,slip=0.0005),HO)["cagr"]*100
for tn,en in (("1.5 sd under","back to 20d avg, max 10d"),("1 sd under 20d avg","back to 20d avg, max 20d")):
    tg=TRIG[tn]; ex=EXIT[en]; P=positions(tg,mix,ex); T=trades(P).loc[HO:END]; real=(T.net.mean()*100,cagr(P))
    alone=bt.stats(bt.run(full(0.25*P),fee=0.004,slip=0.0005),HO)
    nets=[]; cs=[]
    for k in range(120):
        # placebo: same number of trigger days per asset, placed at random days inside the same regime; exit after the real trades' median length
        fake=pd.DataFrame(False,index=C.index,columns=BE)
        for s in BE:
            days=np.where(mix[s].fillna(False).values)[0]; n=int((tg[s]&mix[s]).sum()); fake.iloc[rng.choice(days,size=min(n,len(days)),replace=False),BE.index(s)]=True
        Pf=positions(fake,mix,("hold",int(max(1,T.days.median())))); Tf=trades(Pf).loc[HO:END]; nets.append(Tf.net.mean()*100); cs.append(cagr(Pf))
    nets=np.array(nets); cs=np.array(cs)
    print(f"\n{tn} / {en}: real net/trade {real[0]:.2f}% (n={len(T)}, median hold {T.days.median():.0f}d), with trend {real[1]:.1f}%, alone {alone['cagr']*100:.1f}% dd {alone['maxdd']*100:.1f}")
    print(f"   placebo net/trade median {np.median(nets):.2f}%, 5-95th {np.percentile(nets,5):.2f}..{np.percentile(nets,95):.2f}, share >= real {(nets>=real[0]).mean()*100:.0f}% | with trend median {np.median(cs):.1f}%, 5-95th {np.percentile(cs,5):.1f}..{np.percentile(cs,95):.1f}, share >= real {(cs>=real[1]).mean()*100:.0f}%")
    print("   trades by year:",T.groupby(T.index.year).net.agg(["size","mean"]).assign(mean=lambda d:(d["mean"]*100).round(2)).T.to_dict())
