"""Grid bot that runs only while a sideways regime is detected. 5-minute Binance spot bars, BTC and ETH, long only.
Regime for day D uses daily data up to the close of day D-1. Limit orders fill only if the bar trades through the price by 1 bp.
Grid: when the regime switches on, levels are set at spacing g around that moment's price. One lot (1/K of the sleeve) is bought each time
price trades down through an empty level and sold one level higher. When the regime switches off: mode 0 sells everything at market,
mode 1 stops buying and leaves the sell orders working."""
import numpy as np, pandas as pd, warnings, itertools; warnings.filterwarnings("ignore")
from numba import njit
import bt
BE=["BTCUSDT","ETHUSDT"]; T0,T1,HO="2018-04-01","2026-10-31","2022-01-01"; THRU=1e-4
M5={s:pd.read_parquet(f"{bt.D}/m5/{s}.parquet") for s in BE}
DAY={s:M5[s].resample("D").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna() for s in BE}
ENS=bt.signal("ensemble")
def rank(x): return x.rolling(365,min_periods=180).rank(pct=True)
def adx(d,n=14):
    h,l,c=d.high,d.low,d.close; up=h.diff(); dn=-l.diff()
    pdm=up.where((up>dn)&(up>0),0.0); mdm=dn.where((dn>up)&(dn>0),0.0)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/n,min_periods=n).mean(); pdi=100*pdm.ewm(alpha=1/n,min_periods=n).mean()/atr; mdi=100*mdm.ewm(alpha=1/n,min_periods=n).mean()/atr
    return (100*(pdi-mdi).abs()/(pdi+mdi)).ewm(alpha=1/n,min_periods=n).mean()
def regimes(s):
    d=DAY[s]; c=d.close; e=ENS[s].reindex(d.index)
    er=(c-c.shift(30)).abs()/c.diff().abs().rolling(30).sum(); a=adx(d)
    bbw=c.rolling(20).std()/c.rolling(20).mean(); rng=(d.high.rolling(20).max()-d.low.rolling(20).min())/c
    mixed=(e>2/9+1e-9)&(e<7/9-1e-9); notdown=e>2/9+1e-9
    fr=(d.high[::-1].rolling(14).max()[::-1]/d.low[::-1].rolling(14).min()[::-1]-1)
    R={"any time":pd.Series(True,index=d.index),
       "low 30-day efficiency (bottom third of the year)":rank(er)<1/3,
       "ADX(14) under 20":a<20,
       "ADX(14) under 25":a<25,
       "narrow Bollinger band (bottom third)":rank(bbw)<1/3,
       "narrow 20-day range (bottom third)":rank(rng)<1/3,
       "trend rules mixed (3-6 of 9)":mixed,
       "ADX under 20 and not in a downtrend":(a<20)&notdown,
       "ADX under 20 and narrow range":(a<20)&(rank(rng)<1/3),
       "ADX(28) under 20":adx(d,28)<20,
       "hindsight: the next 14 days stay in a narrow range (not tradable)":fr<fr.loc[:"2021"].quantile(1/3)}
    out={}
    for k,v in R.items():
        g=v.fillna(False).astype(bool)
        if not k.startswith("hindsight"): g=g.shift(1).fillna(False).astype(bool)   # day D uses data to D-1
        out[k]=g
    return out
@njit(cache=True)
def grid(o,h,l,c,g,K,gate,grace,stop,fm,ft,slip,thru):
    n=len(o); eq=np.empty(n); ex=np.zeros(n); cash=1.0; lg=np.log(1+g); p0=o[0]; off=0; nstop=0
    bpx=np.zeros(K); un=np.zeros(K); lv=np.full(K,-10**9); ntr=0; gsum=0.0; inpos=0; fees=0.0; prev=False
    for i in range(n):
        on=gate[i]
        if on and not prev:
            p0=o[i]
            for k in range(K): lv[k]=-10**9          # lots left over from an earlier spell keep their own sell price
        if on: off=0
        else: off+=1
        if (not on) and grace>=0 and off>grace:
            for k in range(K):
                if un[k]>0:
                    x=o[i]*(1-slip); cash+=un[k]*x*(1-ft); fees+=un[k]*x*ft; gsum+=x/bpx[k]-1; ntr+=1; un[k]=0.0; lv[k]=-10**9
        if stop>0:
            for k in range(K):
                if un[k]>0:
                    sp=bpx[k]*(1-stop)
                    if l[i]<=sp:
                        x=min(o[i],sp)*(1-slip); cash+=un[k]*x*(1-ft); fees+=un[k]*x*ft; gsum+=x/bpx[k]-1; ntr+=1; nstop+=1; un[k]=0.0; lv[k]=-10**9
        for k in range(K):                              # sells first, for lots held before this bar
            if un[k]>0:
                sp=bpx[k]*(1+g)
                if h[i]>sp*(1+thru):
                    cash+=un[k]*sp*(1-fm); fees+=un[k]*sp*fm; gsum+=g; ntr+=1; un[k]=0.0; lv[k]=-10**9
        if on:
            e=cash
            for k in range(K): e+=un[k]*o[i]
            j=int(np.floor(np.log(o[i]/p0)/lg))
            while True:
                bp=p0*np.exp(j*lg)
                if not (l[i]<bp*(1-thru)): break
                free=-1; dup=False
                for k in range(K):
                    if un[k]==0.0 and free<0: free=k
                    if un[k]>0 and lv[k]==j: dup=True
                if free<0: break
                if not dup:
                    amt=min(e/K,cash)
                    if amt<=1e-9: break
                    un[free]=amt*(1-fm)/bp; fees+=amt*fm; lv[free]=j; bpx[free]=bp; cash-=amt
                j-=1
        v=cash
        for k in range(K):
            v+=un[k]*c[i]
            if un[k]>0: inpos+=1
        eq[i]=v; ex[i]=1-cash/v; prev=on
    return eq,ntr,gsum,fees,inpos,ex,nstop
EXITS={"sell all when the regime ends":(0,0.0),"keep sell orders, sell the rest after 7 days":(7*288,0.0),"keep sell orders, 10% stop per lot, rest after 7 days":(7*288,0.10),"keep sell orders, 10% stop per lot":(-1,0.10),"keep sell orders, no stop":(-1,0.0)}
COSTS={"Bitstamp (0.30% resting, 0.40% immediate)":(0.003,0.004,0.0005),"No fees":(0.0,0.0,0.0)}
def st(x):
    r=x.pct_change().dropna(); yrs=len(x)/365
    return dict(cagr=(x.iloc[-1]/x.iloc[0])**(1/yrs)-1,maxdd=(x/x.cummax()-1).min(),sharpe=r.mean()/r.std()*np.sqrt(365) if r.std()>0 else 0.0)
if __name__=="__main__":
    REG={s:regimes(s) for s in BE}
    arr={}; gates={}
    for s in BE:
        d=M5[s].loc[T0:T1]; arr[s]=(d.index,)+tuple(d[k].values for k in ("open","high","low","close"))
        gates[s]={k:v.reindex(d.index.floor("D")).fillna(False).values.astype(np.bool_) for k,v in REG[s].items()}
    rows=[]; curves={}
    for rn in REG[BE[0]]:
        share=np.mean([gates[s][rn].mean() for s in BE])
        fl=[]
        for s in BE:
            v=REG[s][rn].loc[HO:T1]; grp=(v!=v.shift()).cumsum(); L=v.groupby(grp).agg(["first","size"]); fl.append(L[L["first"]]["size"])
        fl=pd.concat(fl)
        for g,K,(mn,(grace,stop)),(cn,(fm,ft,slip)) in itertools.product((0.005,0.01,0.015,0.02,0.03,0.05),(5,10),EXITS.items(),COSTS.items()):
            eqs=[]; xs=[]; ntr=0; gs=0.0; fees=0.0; ip=0; nho=0
            for s in BE:
                idx,o,h,l,c=arr[s]; r=grid(o,h,l,c,g,K,gates[s][rn],grace,stop,fm,ft,slip,THRU)
                eqs.append(pd.Series(r[0],index=idx).resample("D").last()); xs.append(pd.Series(r[5],index=idx).resample("D").mean()); ntr+=r[1]; gs+=r[2]; fees+=r[3]; ip+=r[4]
            e=sum(eqs)/2; yrs=len(e)/365
            row=dict(regime=rn,share=share,spells_ho=len(fl)/2,spell_med=fl.median(),spacing=g,lots=K,mode=mn,cost=cn,
                     trips_yr=ntr/yrs/2,gross_pct=gs/ntr*100 if ntr else 0,time_in=ip/sum(len(arr[s][0]) for s in BE)/K)
            for p,(a,b) in (("is",(T0,"2021-12-31")),("ho",(HO,T1))):
                for k,v in st(e.loc[a:b]).items(): row[f"{p}_{k}"]=v
            rows.append(row); curves[(rn,g,K,mn,cn)]=(e,sum(xs)/2)
    R=pd.DataFrame(rows); R.to_csv("gridreg_results.csv",index=False); pd.to_pickle(curves,"gridreg_curves.pkl")
    pd.set_option("display.width",260); pd.set_option("display.max_rows",600)
    B=R[R.cost.str.startswith("Bitstamp")]
    print(B.groupby(["regime","mode"],sort=False).agg(share=("share","first"),spells=("spells_ho","first"),spell_med=("spell_med","first"),is_min=("is_cagr","min"),is_med=("is_cagr","median"),is_max=("is_cagr","max"),ho_min=("ho_cagr","min"),ho_med=("ho_cagr","median"),ho_max=("ho_cagr","max"),ho_dd_worst=("ho_maxdd","min"),pos_ho=("ho_cagr",lambda x:(x>0).sum()),n=("ho_cagr","size")).round(3).to_string())
    Z=R[R.cost=="No fees"]
    print(Z.groupby(["regime","mode"],sort=False).agg(ho_min=("ho_cagr","min"),ho_med=("ho_cagr","median"),ho_max=("ho_cagr","max"),is_med=("is_cagr","median")).round(3).to_string())
