import urllib.request, json, time, os
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HYPEUSDT"]
end=int(time.time()*1000)
for s in SYMS:
    out=[]; e=end
    for _ in range(9):  # 9*1000 bars of 5m ≈ 31 days
        url=f"https://api.bybit.com/v5/market/kline?category=linear&symbol={s}&interval=5&end={e}&limit=1000"
        d=json.load(urllib.request.urlopen(url,timeout=15))
        rows=d.get("result",{}).get("list",[])
        if not rows: break
        out.extend(rows); e=int(rows[-1][0])-1
        time.sleep(0.15)
    out.sort(key=lambda r:int(r[0]))
    with open(f"{s}.json","w") as f: json.dump(out,f)
    print(s,len(out),"bars", time.strftime("%m-%d",time.gmtime(int(out[0][0])/1000)),"->",time.strftime("%m-%d",time.gmtime(int(out[-1][0])/1000)))
