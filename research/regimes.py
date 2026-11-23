import numpy as np, pandas as pd, warnings, itertools; warnings.filterwarnings("ignore")
import bt
BE=["BTCUSDT","ETHUSDT"]; HO="2022-01-01"; END="2026-11-19"; COST=0.0045
C=bt.C[BE].loc["2017-08-17":]; O=bt.O[BE].reindex(C.index); R=C.pct_change()
ens=bt.signal("ensemble")[BE].reindex(C.index)
vol=R.ewm(span=30,min_periods=20).std()*np.sqrt(365); vp=vol.rolling(365,min_periods=180).rank(pct=True)
er=(C-C.shift(30)).abs()/C.diff().abs().rolling(30).sum()      # efficiency ratio: low = sideways
erp=er.rolling(365,min_periods=180).rank(pct=True)
up=ens>=7/9-1e-9; dn=ens<=2/9+1e-9; mix=ens.notna()&~up&~dn
REG={"uptrend (7-9 rules on)":up,"mixed (3-6 on)":mix,"downtrend (0-2 on)":dn,
     "uptrend, calm":up&(vp<0.5),"uptrend, volatile":up&(vp>=0.5),"mixed, calm":mix&(vp<0.5),"mixed, volatile":mix&(vp>=0.5),
     "downtrend, calm":dn&(vp<0.5),"downtrend, volatile":dn&(vp>=0.5),
     "sideways (30d efficiency in bottom third)":erp<1/3,"sideways and mixed":(erp<1/3)&mix,"sideways and downtrend":(erp<1/3)&dn}
nxt=O.shift(-2)/O.shift(-1)-1          # return earned by holding from next open to the open after
base=bt.target_weights("ensemble",fixed=BE,cap=1.0,vol_target=0.4)[BE].reindex(C.index)
PER={"2018-2021":("2018-04-01","2021-12-31"),"2022-2026":(HO,END)}
print("== by regime: share of asset-days | holding the coin, % a year (gross) | trend sleeve position avg | sleeve gross contribution, pts a year of account")
for name,m in REG.items():
    row=f"  {name:42s}"
    for p,(a,b) in PER.items():
        mm=m.loc[a:b]; x=nxt.loc[a:b].where(mm).stack(); tot=nxt.loc[a:b].stack()
        contrib=(base.loc[a:b]*nxt.loc[a:b]).where(mm).sum().sum()/(len(mm)/365)*100
        row+=f" | {p}: {len(x)/len(tot)*100:4.0f}%  hold {x.mean()*365*100:6.0f}%/yr (t {x.mean()/x.std()*np.sqrt(len(x)):5.2f})  pos {base.loc[a:b].where(mm).stack().mean():.2f}  contrib {contrib:5.1f}"
    print(row)
# spell lengths and flips
for name in ("uptrend (7-9 rules on)","mixed (3-6 on)","downtrend (0-2 on)"):
    m=REG[name]["BTCUSDT"].loc[HO:END]; g=(m!=m.shift()).cumsum(); L=m.groupby(g).agg(["first","size"]); L=L[L["first"]]["size"]
    print(f"  BTC {name}: {len(L)} spells 2022+, median {L.median():.0f} days, shortest {L.min()}, longest {L.max()}")
# mean reversion inside regimes
def rsi(c,n):
    d=c.diff(); u=d.clip(lower=0).ewm(alpha=1/n,min_periods=n).mean(); w=(-d.clip(upper=0)).ewm(alpha=1/n,min_periods=n).mean(); return 100-100/(1+u/w)
ma20=C.rolling(20).mean(); sd20=C.rolling(20).std(); z=(C-ma20)/sd20
TRIG={"1 sd under 20d avg":z<-1,"1.5 sd under":z<-1.5,"2 sd under":z<-2,"RSI(14)<35":rsi(C,14)<35,"RSI(5)<25":rsi(C,5)<25}
EXIT={"back to 20d avg, max 10d":("ma",10),"back to 20d avg, max 20d":("ma",20),"hold 5d":("hold",5)}
def positions(trig,reg,ex,stay=False):
    P=pd.DataFrame(0.0,index=C.index,columns=BE)
    for s in BE:
        t=(trig[s]&reg[s]).fillna(False).values; rg=reg[s].fillna(False).values; c=C[s].values; m=ma20[s].values; p=np.zeros(len(c)); on=False; held=0
        for i in range(len(c)):
            if on:
                held+=1
                out=(held>=ex[1]) or (ex[0]=="ma" and c[i]>=m[i]) or (stay and not rg[i])
                if out: on=False
            if not on and t[i]: on=True; held=0
            p[i]=1.0 if on else 0.0
        P[s]=p
    return P
def trades(P):
    out=[]
    for s in BE:
        p=P[s].shift(1).fillna(0).values; o=O[s].values; i=0; n=len(p)
        while i<n:
            if p[i]==1 and (i==0 or p[i-1]==0):
                j=i
                while j<n and p[j]==1: j+=1
                if j<n: out.append((C.index[i],s,o[j]/o[i]*(1-COST)/(1+COST)-1,o[j]/o[i]-1,j-i))
                i=j
            else: i+=1
    return pd.DataFrame(out,columns=["date","sym","net","gross","days"]).set_index("date").sort_index()
def full(w):
    W=pd.DataFrame(0.0,index=bt.C.index,columns=bt.C.columns); W[BE]=w.reindex(bt.C.index).fillna(0); return W
b=bt.run(full(base),fee=0.004,slip=0.0005); bs=bt.stats(b,HO)
print(f"\n== mean reversion inside a regime (buy the dip, sell at the average), 0.45%/side. trend sleeve alone 2022+: {bs['cagr']*100:.1f}%")
rows=[]
MR={"mixed (3-6 on)":mix,"sideways":erp<1/3,"sideways and mixed":(erp<1/3)&mix,"mixed, calm":mix&(vp<0.5),"downtrend":dn,"uptrend":up,"any":ens.notna()}
for (rn,rg),(tn,tg),(en,ex) in itertools.product(MR.items(),TRIG.items(),EXIT.items()):
    P=positions(tg,rg,ex); T=trades(P); r={"regime":rn,"trigger":tn,"exit":en}
    for p,(a,bb) in PER.items():
        t=T.loc[a:bb]; r[p+" n"]=len(t); r[p+" net"]=t.net.mean()*100 if len(t) else np.nan; r[p+" gross"]=t.gross.mean()*100 if len(t) else np.nan
        r[p+" t"]=t.net.mean()/t.net.std()*np.sqrt(len(t)) if len(t)>5 else np.nan
    w=(base+0.25*P).clip(upper=0.5); c=bt.stats(bt.run(full(w),fee=0.004,slip=0.0005),HO); r["with trend 2022+"]=c["cagr"]*100; r["dd"]=c["maxdd"]*100
    rows.append(r)
D=pd.DataFrame(rows); D.to_csv("regime_mr.csv",index=False)
pd.set_option("display.width",250)
g=D.groupby("regime",sort=False).agg(settings=("trigger","size"),fit_net_med=("2018-2021 net","median"),ho_net_med=("2022-2026 net","median"),ho_gross_med=("2022-2026 gross","median"),ho_net_min=("2022-2026 net","min"),ho_net_max=("2022-2026 net","max"),
   ho_pos=("2022-2026 net",lambda x:(x>0).sum()),both_pos=("2022-2026 net",lambda x:0),ho_t2=("2022-2026 t",lambda x:(x>2).sum()),n_med=("2022-2026 n","median"),comb_min=("with trend 2022+","min"),comb_med=("with trend 2022+","median"),comb_max=("with trend 2022+","max"),beat=("with trend 2022+",lambda x:(x>bs['cagr']*100).sum()))
g["both_pos"]=[((D[D.regime==r]["2022-2026 net"]>0)&(D[D.regime==r]["2018-2021 net"]>0)).sum() for r in g.index]
print(g.round(2).to_string())
