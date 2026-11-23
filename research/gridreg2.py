import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
import bt, gridreg as G
from gridreg import *
pd.set_option("display.width",260)
R=pd.read_csv("gridreg_results.csv"); B=R[R.cost.str.startswith("Bitstamp")]; Z=R[R.cost=="No fees"]
REG={s:regimes(s) for s in BE}
# 1. does a sideways reading predict a sideways fortnight?
print("== share of days followed by a narrow 14-day range (hindsight label), 2022-2026 | 14-day range median | next-14d return mean")
hk=[k for k in REG[BE[0]] if k.startswith("hindsight")][0]
for k in REG[BE[0]]:
    num=den=0; rr=[]; fw=[]
    for s in BE:
        d=DAY[s]; g=REG[s][k].loc[HO:"2026-10-17"]; h=REG[s][hk].reindex(g.index); num+=(g&h).sum(); den+=g.sum()
        fr=(d.high[::-1].rolling(14).max()[::-1]/d.low[::-1].rolling(14).min()[::-1]-1).reindex(g.index); rr.append(fr[g]); fw.append((d.close.shift(-14)/d.open-1).reindex(g.index)[g])
    print(f"  {k:66s} {num/den*100:5.1f}%  range {pd.concat(rr).median()*100:5.1f}%  fwd14 {pd.concat(fw).mean()*100:5.2f}%  days {den/2:.0f}")
# 2. settings chosen on 2018-2021, scored on 2022-2026 (Bitstamp fees), excluding hindsight and 'no stop' bag-holding shown separately
real=B[~B.regime.str.startswith("hindsight")]
print("\n== best 10 on 2018-2021 (Bitstamp fees), and what they did after")
print(real.sort_values("is_cagr",ascending=False).head(10)[["regime","mode","spacing","lots","time_in","trips_yr","is_cagr","is_maxdd","ho_cagr","ho_maxdd","ho_sharpe"]].round(3).to_string(index=False))
print("\n== best 10 on 2022-2026 (hindsight pick)")
print(real.sort_values("ho_cagr",ascending=False).head(10)[["regime","mode","spacing","lots","time_in","trips_yr","is_cagr","is_maxdd","ho_cagr","ho_maxdd","ho_sharpe"]].round(3).to_string(index=False))
flat=real[~real["mode"].str.contains("no stop")&(real.regime!="any time")]
print(f"\nregime-gated grids that end flat (not bag-holding): {len(flat)} settings; positive 2022-2026: {(flat.ho_cagr>0).sum()}; positive both periods: {((flat.ho_cagr>0)&(flat.is_cagr>0)).sum()}; median {flat.ho_cagr.median()*100:.1f}%; best {flat.ho_cagr.max()*100:.1f}%")
zf=Z[~Z.regime.str.startswith("hindsight")&~Z["mode"].str.contains("no stop")&(Z.regime!="any time")]
print(f"same at no fees: positive {(zf.ho_cagr>0).sum()} of {len(zf)}; median {zf.ho_cagr.median()*100:.1f}%; best {zf.ho_cagr.max()*100:.1f}%")
bag=real[real["mode"].str.contains("no stop")]
print(f"bag-holding (no stop) settings: {len(bag)}; positive HO {(bag.ho_cagr>0).sum()}; median {bag.ho_cagr.median()*100:.1f}%; best {bag.ho_cagr.max()*100:.1f}%; median worst loss {bag.ho_maxdd.median()*100:.0f}%; median time in market {bag.time_in.median()*100:.0f}%")
h=B[B.regime.str.startswith("hindsight")]
print("\n== hindsight regime, Bitstamp fees, by spacing"); print(h.groupby(["mode","spacing"]).agg(ho=("ho_cagr","mean"),dd=("ho_maxdd","mean"),trips=("trips_yr","mean")).round(3).to_string())
print("\n== ADX<20 by spacing, sell all at regime end and grace"); a=B[B.regime=="ADX(14) under 20"]; print(a.groupby(["mode","spacing"]).agg(is_=("is_cagr","mean"),ho=("ho_cagr","mean"),dd=("ho_maxdd","mean"),trips=("trips_yr","mean"),time_in=("time_in","mean")).round(3).to_string())
