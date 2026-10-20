import json
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HYPEUSDT"]
def load(s):
    rows=json.load(open(f"{s}.json"))
    return [(int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4])) for r in rows]
def ema(vals,n):
    k=2/(n+1); out=[]; e=vals[0]
    for v in vals: e=v*k+e*(1-k); out.append(e)
    return out
def rsi(closes,n=14):
    out=[50.0]*len(closes); g=0.0; l=0.0
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]; gain=max(d,0); loss=max(-d,0)
        if i<=n: g+=gain/n; l+=loss/n
        else: g=(g*(n-1)+gain)/n; l=(l*(n-1)+loss)/n
        out[i]=100.0 if l==0 else 100-100/(1+g/l)
    return out
def atr(bars,n=14):
    out=[0.0]*len(bars); a=bars[0][2]-bars[0][3]
    for i in range(1,len(bars)):
        t,o,h,lo,c=bars[i]; pc=bars[i-1][4]
        a=(a*(n-1)+max(h-lo,abs(h-pc),abs(lo-pc)))/n; out[i]=a
    return out
data={}
for s in SYMS:
    b=load(s); closes=[x[4] for x in b]
    data[s]=dict(bars=b,e20=ema(closes,20),e50=ema(closes,50),rsi=rsi(closes),atr=atr(b))

def run(tp,sl,timeout,strat,cost,depth=0.5,rlo=28,rhi=72,tg=True):
    wins=0;n=0;net=0.0
    for s in SYMS:
        d=data[s];b=d["bars"]; i=250; pos=None
        while i<len(b)-1:
            t,o,h,lo,c=b[i]
            if pos:
                side,entry,ei=pos
                if side=="L":
                    hit_s=lo<=entry*(1-sl/100); hit_t=h>=entry*(1+tp/100)
                    pnl=(-sl/100-cost) if hit_s else (tp/100-cost) if hit_t else ((c-entry)/entry-cost) if i-ei>=timeout else None
                else:
                    hit_s=h>=entry*(1+sl/100); hit_t=lo<=entry*(1-tp/100)
                    pnl=(-sl/100-cost) if hit_s else (tp/100-cost) if hit_t else ((entry-c)/entry-cost) if i-ei>=timeout else None
                if pnl is not None:
                    n+=1; net+=pnl
                    if pnl>0: wins+=1
                    pos=None
                i+=1; continue
            sig=None; up=d["e20"][i]>d["e50"][i]
            if strat=="PB":
                if up and c < d["e20"][i]-depth*d["atr"][i]: sig="L"
                elif (not up) and c > d["e20"][i]+depth*d["atr"][i]: sig="S"
            else:
                if d["rsi"][i]<rlo and (up or not tg): sig="L"
                elif d["rsi"][i]>rhi and ((not up) or not tg): sig="S"
            if sig: pos=(sig,b[i+1][1],i+1); i+=1
            i+=1
    return n,wins/max(n,1)*100,net*100,n/31

print("PB variants (cost 10bps / 3bps):")
for depth in [1.0,1.5,2.0]:
    for tp,sl in [(0.5,2.0),(0.6,2.0),(0.8,2.0),(0.6,2.4)]:
        n,w,net,td=run(tp,sl,144,"PB",0.0010,depth=depth)
        n2,w2,net2,td2=run(tp,sl,144,"PB",0.0003,depth=depth)
        print(f"  d{depth} tp{tp} sl{sl}: {n:5d} tr {td:5.1f}/d win {w:5.1f}% net10 {net:+7.1f}% | net3 {net2:+7.1f}%")
print("RV variants (cost 10bps / 3bps):")
for rlo,rhi in [(28,72),(30,70),(32,68),(25,75)]:
    for tg in [True,False]:
        for tp,sl in [(0.3,1.6),(0.4,1.6),(0.4,2.0)]:
            n,w,net,td=run(tp,sl,144,"RV",0.0010,rlo=rlo,rhi=rhi,tg=tg)
            n2,w2,net2,td2=run(tp,sl,144,"RV",0.0003,rlo=rlo,rhi=rhi,tg=tg)
            print(f"  rsi{rlo}/{rhi} tg={int(tg)} tp{tp} sl{sl}: {n:5d} tr {td:5.1f}/d win {w:5.1f}% net10 {net:+6.1f}% | net3 {net2:+6.1f}%")
