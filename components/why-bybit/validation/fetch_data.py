"""Fetch Bybit public history (5m last-price klines + funding). Raw rows kept in Bybit's own string format."""
import json, os, sys, time, urllib.request
D = "/opt/why-bybit/validation/data_bybit"; DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 180
SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HYPEUSDT"]
def get(u):
    for i in range(4):
        try: return json.load(urllib.request.urlopen(u, timeout=20))["result"]
        except Exception as e: last = e; time.sleep(1 + i)
    raise last
now = int(time.time() * 1000); start = now - DAYS * 86400000
for s in SYMS:
    out = {}; e = now
    while e > start:
        l = get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={s}&interval=5&end={e}&limit=1000")["list"]
        if not l: break
        for r in l: out[int(r[0])] = r
        e = min(int(r[0]) for r in l) - 1; time.sleep(0.1)
    rows = [out[k] for k in sorted(out)][:-1]                 # drop the still-forming bar
    json.dump(rows, open(f"{D}/k5_{s}.json", "w"))
    fr = {}; e = now
    while e > start - 86400000:
        l = get(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={s}&endTime={e}&limit=200")["list"]
        if not l: break
        for r in l: fr[int(r["fundingRateTimestamp"])] = float(r["fundingRate"])
        e = min(int(r["fundingRateTimestamp"]) for r in l) - 1; time.sleep(0.1)
    json.dump(fr, open(f"{D}/fund_{s}.json", "w"))
    print(s, len(rows), "bars", time.strftime("%Y-%m-%d", time.gmtime(int(rows[0][0])/1000)), "->", time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(rows[-1][0])/1000)), "| funding pts", len(fr), flush=True)
