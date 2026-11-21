import bt, pandas as pd, numpy as np, time
pd.set_option("display.width",250); pd.set_option("display.max_columns",20); pd.set_option("display.float_format", lambda x: f"{x:.3f}")
t=time.time()
rows={}
rows["hodl_btc"]=bt.stats(bt.run(bt.target_weights("hold",fixed=["BTCUSDT"],cap=1),fee=0,slip=0,band=0.9))
rows["hodl_btc_eth_M"]=bt.stats(bt.run(bt.target_weights("hold",fixed=["BTCUSDT","ETHUSDT"],cap=1),rebalance="M",band=0))
rows["ew_top20_M"]=bt.stats(bt.run(bt.target_weights("hold",n=20,cap=1),rebalance="M",band=0))
for u,kw in [("btc",dict(fixed=["BTCUSDT"],cap=1)),("btc_eth",dict(fixed=["BTCUSDT","ETHUSDT"],cap=1)),("top10",dict(n=10)),("top20",dict(n=20))]:
    w=bt.target_weights("ensemble",**kw)
    rows[f"ens_{u}_perp_taker"]=bt.stats(bt.run(w,fee=0.0005,perp=True))
    rows[f"ens_{u}_cbspot_taker"]=bt.stats(bt.run(w,fee=0.009))
print(pd.DataFrame(rows).T); print(time.time()-t)
# manual check: BTC hodl multiple
C=bt.C["BTCUSDT"]; print(C.loc[bt.END]/bt.O["BTCUSDT"].loc[bt.START])
f=bt.funding(); print("mean annual funding BTC/ETH/SOL:", (f[["BTCUSDT","ETHUSDT","SOLUSDT"]].loc["2020":].mean()*365).round(3).to_dict())
