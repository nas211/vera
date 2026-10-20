"""Deterministic broad candle screening. Research only; never submits orders.

A configuration is a parameter variant, not an independent strategy or an AI
agent. OHLC replay is a coarse filter; fresh execution-aware paper trials remain
mandatory. Holdout folds never rank the training shortlist.
"""
import hashlib
import itertools
import math
import numpy as np
from . import rules
from .fees import FEE_RATE

VERSION=1
CONDITIONS_PER_SIDE=600
GEOMETRIES_PER_SIDE=150
BAR_MS=300000
RISK_BUDGET=1.80
ENTRY_SLIP=.0005
EXIT_SLIP=.002
CARRY_PER_HOUR=.0001
PURGE_BARS=72

def digest(x):return hashlib.sha256(rules.packed(x).encode()).hexdigest()

def catalog():
    primary=[]
    for feature,scale in [('return_1',10),('return_5',30),('return_20',60)]:
        for op,k in itertools.product(('gt','lt'),(-2,-1,0,1,2)):
            primary.append(dict(feature=feature,op=op,value=scale*k))
    secondary=[]
    for feature,thresholds in [('range_position',(.2,.4,.6,.8)),('volume_ratio',(.8,1.2,2.)),('volatility',(10.,20.,40.))]:
        for op,value in itertools.product(('gt','lt'),thresholds):secondary.append(dict(feature=feature,op=op,value=value))
    conditions=sorted(([p,s] for p,s in itertools.product(primary,secondary)),key=digest)[:CONDITIONS_PER_SIDE]
    geometries=[dict(stop_bps=s,target_bps=t,max_hold_seconds=h) for s,t,h in itertools.product((40,50,60,70,80,90,100),(80,100,120,140,160,180),(7200,10800,14400,18000,21600)) if t>=1.5*s]
    geometries=sorted(geometries,key=digest)[:GEOMETRIES_PER_SIDE]
    return conditions,geometries

def specification(market,side,conditions,geometry):
    result=dict(name='Bulk '+digest([market,side,conditions,geometry])[:12],thesis='Predefined candle-rule hypothesis from bulk research; requires new paper execution trials.',market=market,mode='swing',side=side,conditions=conditions,match='all',entry='taker',**geometry)
    return rules.validate(result,{market})

def validate_candles(rows,as_of,minimum=1000):
    a=np.asarray([r[:6] for r in rows],dtype=float)
    if a.ndim!=2 or a.shape[1]!=6 or len(a)<minimum or not np.isfinite(a).all():raise ValueError('candle_schema_or_history')
    ts=a[:,0]
    if np.any(ts%BAR_MS) or np.any(np.diff(ts)!=BAR_MS):raise ValueError('candle_gap_or_duplicate')
    if ts[-1]+BAR_MS>as_of*1000 or as_of*1000-(ts[-1]+BAR_MS)>900000:raise ValueError('candle_unclosed_or_stale')
    o,h,l,c,v=a[:,1:].T
    if np.any(np.minimum.reduce([o,h,l,c])<=0) or np.any(v<0) or np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])):raise ValueError('candle_geometry')
    return a

def features(a):
    n=len(a);close=a[:,4];f={}
    for k in (1,5,20):
        x=np.full(n,np.nan);x[k:]=(close[k:]/close[:-k]-1)*10000;f['return_'+str(k)]=x
    for k in ('range_position','volume_ratio','volatility'):f[k]=np.full(n,np.nan)
    for i in range(20,n):
        bars=a[i-19:i+1];hi=bars[:,2].max();lo=bars[:,3].min();avg=a[i-20:i,5].mean()
        f['range_position'][i]=(close[i]-lo)/(hi-lo) if hi>lo else np.nan
        f['volume_ratio'][i]=a[i,5]/avg if avg>0 else np.nan
        f['volatility'][i]=np.std(f['return_1'][i-19:i+1])
    return f

def signal_indices(f,conditions):
    mask=np.ones(len(f['return_1']),dtype=bool)
    for c in conditions:
        v=f[c['feature']];mask &= np.isfinite(v)&((v>c['value']) if c['op']=='gt' else (v<c['value']))
    # Feature observation closes at i; first possible entry is bar i+1.
    return np.flatnonzero(mask)+1

def round_array(x,tick,up=False):
    return (np.ceil(x/tick) if up else np.floor(x/tick))*tick

def outcomes(a,side,geometry,limits):
    """Vectorized independent entry paths; portfolio() removes overlapping trades.

    Both stop and target in the same candle => stop first. Gaps through a stop
    use the worse open, with adverse exit slippage. Losses are never clipped.
    """
    long=side=='long';sign=1 if long else -1
    hold=int(geometry['max_hold_seconds']//300);n=len(a);idx=np.arange(n-hold)
    tick=limits['tick'];step=limits['step']
    entry=round_array(a[idx,1]*(1+sign*ENTRY_SLIP),tick,up=long)
    stop=round_array(entry*(1-sign*geometry['stop_bps']/10000),tick,up=long)
    target=round_array(entry*(1+sign*geometry['target_bps']/10000),tick,up=long)
    per_unit=abs(entry-stop)+(entry+stop)*FEE_RATE+entry*EXIT_SLIP+tick
    qty=np.floor(np.minimum(RISK_BUDGET/per_unit,500/entry)/step)*step
    valid=(qty>=limits['min_qty'])&(np.minimum.reduce([entry,stop,target])*qty>=limits['min_notional'])&(stop>0)&(target>0)&((entry-stop)*sign>0)
    end=idx+hold-1;raw=a[end,4].copy();reason=np.full(len(idx),2,dtype=np.int8);done=np.zeros(len(idx),dtype=bool)
    for offset in range(hold):
        bar=idx+offset;op=a[bar,1];hi=a[bar,2];lo=a[bar,3]
        hit_stop=(lo<=stop if long else hi>=stop)&~done
        hit_target=(hi>=target if long else lo<=target)&~done&~hit_stop
        raw[hit_stop]=np.minimum(op,stop)[hit_stop] if long else np.maximum(op,stop)[hit_stop]
        raw[hit_target]=target[hit_target]
        hit=hit_stop|hit_target;end[hit]=bar[hit];reason[hit_stop]=0;reason[hit_target]=1;done|=hit
    exit_price=round_array(raw*(1-sign*EXIT_SLIP),tick,up=not long)
    gross=(exit_price-entry)*qty*sign;fees=(entry+exit_price)*qty*FEE_RATE
    carry=entry*qty*CARRY_PER_HOUR*(end-idx+1)/12
    net=gross-fees-carry
    # Additional 5bp entry + 20bp exit cost stress, without changing decisions.
    stress=net-entry*qty*.0005-exit_price*qty*.002
    stop_net=(stop-entry)*qty*sign-(entry+stop)*qty*FEE_RATE
    if np.any(stop_net[valid]<-RISK_BUDGET-1e-7):raise AssertionError('stop_budget_exceeded')
    return dict(hold=hold,end=end,net=net,stress=stress,valid=valid,reason=reason,qty=qty,entry=entry,stop=stop,target=target,fees=fees,carry=carry,stop_net=stop_net)

def portfolio(indices,path,start,end,include_trades=False):
    i=int(np.searchsorted(indices,start));chosen=[]
    while i<len(indices):
        entry=int(indices[i])
        if entry>=min(end-path['hold']+1,len(path['end'])):break
        if not path['valid'][entry] or path['end'][entry]>=end:i+=1;continue
        chosen.append(entry)
        i=int(np.searchsorted(indices,path['end'][entry]+1,side='left'))
    net=path['net'][chosen];stress=path['stress'][chosen]
    wins=net[net>0];losses=net[net<0];curve=np.r_[0,np.cumsum(net)]
    r=dict(trades=len(chosen),wins=len(wins),net=float(net.sum()),stress_net=float(stress.sum()),gross_profit=float(wins.sum()),gross_loss=float(-losses.sum()),profit_factor=float(wins.sum()/-losses.sum()) if len(losses) else (None if not len(wins) else 999.),max_drawdown=float(np.max(np.maximum.accumulate(curve)-curve)),mean=float(net.mean()) if len(net) else 0.)
    if include_trades:r['entries']=chosen;r['exits']=[int(path['end'][i]) for i in chosen]
    return r

def training_pass(m):return m['trades']>=30 and m['net']>0 and m['stress_net']>0 and (m['profit_factor'] or 0)>=1.2 and m['max_drawdown']<=20

def fold_pass(m):return m['trades']>=10 and m['net']>0 and m['stress_net']>0 and (m['profit_factor'] or 0)>=1.2 and m['max_drawdown']<=10

def training_rank(row):
    m=row['train'];return (-m['stress_net']/math.sqrt(max(1,m['trades'])),row['fingerprint'])

def select_training(rows,limit=5):
    # Called before reading any later-period outcomes. Avoid nearly identical
    # finalists by allowing one configuration per exact entry condition set.
    chosen=[];seen=set()
    for row in sorted((x for x in rows if training_pass(x['train'])),key=training_rank):
        key=row['condition_index']
        if key in seen:continue
        chosen.append(row);seen.add(key)
        if len(chosen)>=limit:break
    return chosen


def next_entries(indices,n):
    matrix=np.full((len(indices),n+1),n,dtype=np.int32)
    for row,values in enumerate(indices):
        values=values[values<n];matrix[row,values]=values
    return np.minimum.accumulate(matrix[:,::-1],axis=1)[:,::-1].copy()


def portfolios(nexts,path,start,end):
    """Batch equivalent of portfolio(); one non-overlapping position per row."""
    rows=np.arange(len(nexts));cursor=nexts[:,start].copy();limit=min(end-path['hold']+1,len(path['end']))
    count=np.zeros(len(rows),dtype=int);wins=count.copy()
    sums=np.zeros(len(rows));stress=sums.copy();gp=sums.copy();gl=sums.copy();peak=sums.copy();draw=sums.copy()
    while True:
        active=rows[cursor<limit]
        if not len(active):break
        at=cursor[active];ok=path['valid'][at]&(path['end'][at]<end)
        good=active[ok];entries=at[ok]
        net=path['net'][entries]
        count[good]+=1;wins[good]+=(net>0);sums[good]+=net;stress[good]+=path['stress'][entries]
        gp[good]+=np.maximum(net,0);gl[good]+=np.maximum(-net,0)
        peak[good]=np.maximum(peak[good],sums[good]);draw[good]=np.maximum(draw[good],peak[good]-sums[good])
        after=np.where(ok,path['end'][at]+1,at+1)
        cursor[active]=nexts[active,after]
    return [dict(trades=int(count[i]),wins=int(wins[i]),net=float(sums[i]),stress_net=float(stress[i]),gross_profit=float(gp[i]),gross_loss=float(gl[i]),profit_factor=float(gp[i]/gl[i]) if gl[i]>0 else (999. if gp[i]>0 else None),max_drawdown=float(draw[i]),mean=float(sums[i]/count[i]) if count[i] else 0.) for i in rows]
