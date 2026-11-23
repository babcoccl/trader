import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
src=open("regimes.py").read(); exec(src[:src.index("b=bt.run(full(base)")])
FIT=("2018-04-01","2021-12-31")
vt=lambda s,v=0.4: (s/2)*(v/vol).clip(upper=1.0).fillna(0.0)
def line(name,w):
    out=f"{name:62s}"
    for a,b in (FIT,(HO,END)):
        r=bt.run(full(w),fee=0.004,slip=0.0005); s=bt.stats(r,a,b); z=bt.stats(bt.run(full(w),fee=0,slip=0),a,b)
        rr=r.loc[a:b]; td=(rr.turnover>0).sum()/(len(rr)/365); cash=(rr.exposure<0.005).mean()*100
        out+=f" | {s['cagr']*100:6.1f}% dd {s['maxdd']*100:5.1f} sh {s['sharpe']:.2f} zero-fee {z['cagr']*100:5.1f}% trades {td:4.0f}/yr cash {cash:3.0f}%"
    print(out)
print(f"{'':62s} | 2018-2021 {'':58s} | 2022-2026")
line("A trend sleeve as is (one strategy, all regimes)",base)
line("B trend only in uptrend regime (7-9 on), else cash",vt(ens.where(up,0)))
line("C full size in uptrend (signal=1), as is elsewhere",vt(ens.where(~up,1.0)))
line("D cash while mixed+calm, as is elsewhere",base.where(~(mix&(vp<0.5)),0))
line("E 60% vol target in calm uptrend, 40% elsewhere",vt(ens,0.4).where(~(up&(vp<0.5)),vt(ens,0.6)))
line("F half size while volatile (top half of year)",base.where(vp<0.5,base*0.5))
# mean-reversion sleeve in mixed regime: settings chosen on 2018-2021 only
D=pd.read_csv("regime_mr.csv")
for rn,rg in (("mixed (3-6 on)",mix),("sideways",erp<1/3),("sideways and mixed",(erp<1/3)&mix)):
    M=D[(D.regime==rn)&(D["2018-2021 n"]>=15)].sort_values("2018-2021 net",ascending=False); top=M.iloc[0]
    P=positions(TRIG[top.trigger],rg,EXIT[top["exit"]])
    print(f"   [{rn}] best on 2018-2021 with >=15 trades: {top.trigger} / {top['exit']}: fit {top['2018-2021 net']:.2f}%/trade n={top['2018-2021 n']:.0f}; held-out {top['2022-2026 net']:.2f}%/trade n={top['2022-2026 n']:.0f} t={top['2022-2026 t']:.2f}")
    line(f"G trend + dip-buying sleeve (25%/coin) while {rn}",(base+0.25*P).clip(upper=0.5))
    line(f"H switch: trend in uptrend, dip-buying while {rn}, else cash",(vt(ens.where(up,0))+0.25*P.where(~up,0)).clip(upper=0.5))
    line(f"  dip-buying sleeve alone",0.25*P)
# all 15 settings as an ensemble (no selection)
for rn,rg in (("mixed (3-6 on)",mix),("sideways",erp<1/3)):
    Pm=sum(positions(tg,rg,ex) for tg in TRIG.values() for ex in EXIT.values())/15
    line(f"I trend + average of all 15 dip settings while {rn}",(base+0.25*Pm).clip(upper=0.5))
    line(f"  average of 15 alone",0.25*Pm)
