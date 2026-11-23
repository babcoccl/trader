import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("redditideas.py").read().split('ens=bt.signal')[0].replace('print("funding from"','#'))
ens=bt.signal("ensemble")[BE]; off=ens<=2/9+1e-9
base=bt.target_weights("ensemble",fixed=BE,cap=1.0,vol_target=0.4)[BE]
def run(w):
    full=pd.DataFrame(0.0,index=bt.C.index,columns=bt.C.columns); full[BE]=w.reindex(full.index).fillna(0.0)
    return bt.run(full,fee=FEE,slip=SLIP)
def line(name,w):
    r=run(w); h=bt.stats(r,HO); g=bt.stats(r.loc["2020-03-01":"2021-12-31"],"2020-03-01")
    print(f"  {name:46s} 2020-21 {g['cagr']*100:6.1f}% dd {g['maxdd']*100:6.1f} | 2022+ {h['cagr']*100:5.1f}% dd {h['maxdd']*100:6.1f} sharpe {h['sharpe']:.2f}")
    return r
print(list(run(base).columns))
line("trend sleeve",base)
for win in (3,7,14,30):
    n=F.rolling(win).mean()<0
    line(f"alone: 25% while {win}d funding<0, trend off",0.25*(n&off))
    line(f"trend + that",(base+0.25*(n&off)).clip(upper=0.5))
n=F.rolling(7).mean()<0
for s in BE:
    w=base.copy(); w[s]=(w[s]+0.25*(n[s]&off[s])).clip(upper=0.5); line(f"trend + 7d, {s} only",w)
line("trend + 25% on ALL trend-off days (placebo)",(base+0.25*off).clip(upper=0.5))
rng=np.random.default_rng(1); res=[]
m=(n&off)
for i in range(200):   # placebo: same number of trend-off days per asset, shifted in time by a random offset
    k=int(rng.integers(30,1500)); sh=pd.DataFrame({s:np.roll(m[s].values,k) for s in BE},index=m.index)&off
    res.append(bt.stats(run((base+0.25*sh).clip(upper=0.5)),HO)["cagr"]*100)
res=np.array(res); print(f"  placebo (signal shifted in time, 200 runs): 2022+ median {np.median(res):.1f}%, 5th-95th {np.percentile(res,5):.1f}..{np.percentile(res,95):.1f}%, share >= 16.8%: {(res>=16.8).mean()*100:.0f}%")
# episodes and per-year
r=run(0.25*m); ret=[c for c in r.columns if c in ("ret","net","r")]
eq=(1+r[ret[0]]).cumprod() if ret else None
if eq is not None:
    print("  sleeve alone by year %:",((eq.resample("YE").last()/eq.resample("YE").first()-1)*100).round(1).loc["2020":].to_dict())
for s in BE:
    x=m[s].astype(int); starts=(x.diff()==1)&x.astype(bool)
    print(f"  {s}: days on by year",m[s].groupby(m.index.year).sum().loc[2020:].to_dict(),"episodes 2022+",int(starts.loc[HO:].sum()))
