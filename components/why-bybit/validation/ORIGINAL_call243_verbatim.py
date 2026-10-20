import json, math
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HYPEUSDT"]
COST=0.0010  # round trip: 4bps taker x2 + ~2bps spread/slippage

def load(s):
    rows=json.load(open(f"{s}.json"))
    # bybit row: [start,open,high,low,close,volume,turnover]
    return [(int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4])) for r in rows]

def ema(vals,n):
    k=2/(n+1); out=[]; e=vals[0]
    for v in vals: e=v*k+e*(1-k); out.append(e)
    return out

def rsi(closes,n=14):
    out=[50.0]*len(closes); g=0.0; l=0.0
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]
        gain=max(d,0); loss=max(-d,0)
        if i<=n: g+=gain/n; l+=loss/n
        else:
            g=(g*(n-1)+gain)/n; l=(l*(n-1)+loss)/n
        out[i]=100.0 if l==0 else 100-100/(1+g/l)
    return out

def atr(bars,n=14):
    out=[0.0]*len(bars); a=bars[0][2]-bars[0][3]
    for i in range(1,len(bars)):
        t,o,h,lo,c=bars[i]; pc=bars[i-1][4]
        tr=max(h-lo,abs(h-pc),abs(lo-pc))
        a=(a*(n-1)+tr)/n; out[i]=a
    return out

data={}
for s in SYMS:
    b=load(s); closes=[x[4] for x in b]
    data[s]=dict(bars=b,e20=ema(closes,20),e50=ema(closes,50),rsi=rsi(closes),atr=atr(b))

def run(tp,sl,timeout,strat,depth=0.5,rlo=28,rhi=72,trend_gate=True):
    wins=0;n=0;net=0.0
    for s in SYMS:
        d=data[s];b=d["bars"]
        i=250; pos=None
        while i<len(b)-1:
            t,o,h,lo,c=b[i]
            if pos:
                side,entry,ei=pos
                if side=="L":
                    stop=entry*(1-sl/100); tgt=entry*(1+tp/100)
                    hit_s=lo<=stop; hit_t=h>=tgt
                    if hit_s: pnl=-sl/100-COST
                    elif hit_t: pnl=tp/100-COST
                    elif i-ei>=timeout: pnl=(c-entry)/entry-COST
                    else: pnl=None
                else:
                    stop=entry*(1+sl/100); tgt=entry*(1-tp/100)
                    hit_s=h>=stop; hit_t=lo<=tgt
                    if hit_s: pnl=-sl/100-COST
                    elif hit_t: pnl=tp/100-COST
                    elif i-ei>=timeout: pnl=(entry-c)/entry-COST
                    else: pnl=None
                if pnl is not None:
                    n+=1; net+=pnl
                    if pnl>0: wins+=1
                    pos=None
                i+=1; continue
            sig=None
            up=d["e20"][i]>d["e50"][i]
            if strat=="PB":
                if up and c < d["e20"][i]-depth*d["atr"][i]: sig="L"
                elif (not up) and c > d["e20"][i]+depth*d["atr"][i]: sig="S"
            elif strat=="RV":
                if d["rsi"][i]<rlo and (up or not trend_gate): sig="L"
                elif d["rsi"][i]>rhi and ((not up) or not trend_gate): sig="S"
            if sig:
                nxt=b[i+1]; pos=(sig,nxt[1],i+1); i+=1
            i+=1
    days=31
    return n,wins/max(n,1)*100,net*100,n/days

print(f"{'strat':5}{'tp':>5}{'sl':>5}{'to':>4} | {'trades':>6}{'win%':>7}{'netΣ%':>8}{'tr/day':>7}")
best=[]
for strat in ["PB","RV"]:
    for tp in [0.3,0.4,0.5,0.6,0.8]:
        for sl in [1.2,1.6,2.0,2.4]:
            for to in [96,144]:
                n,w,net,td=run(tp,sl,to,strat)
                if n>100: best.append((w,net,strat,tp,sl,to,n,td))
                print(f"{strat:5}{tp:5.1f}{sl:5.1f}{to:4d} | {n:6d}{w:7.1f}{net:8.1f}{td:7.1f}")
print("\n== cells with win>=68 sorted by net ==")
for w,net,strat,tp,sl,to,n,td in sorted([x for x in best if x[0]>=68],key=lambda x:-x[1])[:12]:
    print(f"{strat} tp{tp} sl{sl} to{to}: win {w:.1f}% net {net:+.1f}% trades {n} ({td:.1f}/day)")
