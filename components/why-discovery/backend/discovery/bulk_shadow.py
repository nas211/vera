"""Independent ongoing candle simulations for the full research grid.

No account balance, order book fills, leverage, exchange credentials or orders.
Every configuration starts flat at registration; only later completed Yellow
candles can generate simulated trades. Snapshot restart is idempotent.
"""
import argparse,fcntl,json,os,time
from pathlib import Path
import numpy as np
from .bulk_screen import catalog,features,round_array,RISK_BUDGET,FEE_RATE,ENTRY_SLIP,EXIT_SLIP,CARRY_PER_HOUR,validate_candles,digest
from .bulk_worker import atomic
from .market import get,universe


def arrays():
    conditions,geometries=catalog();n=len(conditions)*len(geometries)*2
    ci=np.tile(np.repeat(np.arange(len(conditions)),len(geometries)),2)
    gi=np.tile(np.arange(len(geometries)),len(conditions)*2)
    sign=np.repeat(np.array([1.,-1.]),len(conditions)*len(geometries))
    return conditions,geometries,ci,gi,sign

def initial(last_bar,conditions,geometries):
    n=len(conditions)*len(geometries)*2
    s={k:np.zeros(n) for k in ('qty','entry','stop','target','opened','net','gross_profit','gross_loss','peak','drawdown')}
    s.update({k:np.zeros(n,dtype=np.int64) for k in ('trades','wins','invalidated')})
    s.update(pending=np.zeros(n,dtype=bool),last_bar=np.array(last_bar),registered_at=np.array(time.time()),bars_seen=np.array(0),catalog_hash=np.array(digest([conditions,geometries])))
    return s

def observe(state,bar,feature,limits,layout):
    conditions,geometries,ci,gi,sign=layout;ts=int(bar[0]);last=int(state['last_bar'])
    if ts<=last:return
    if ts-last!=300000:
        state['invalidated']+=(state['qty']>0);state['qty'][:]=0;state['pending'][:]=False
        raise ValueError('shadow_candle_gap')
    op,high,low,close=map(float,bar[1:5]);tick=limits['tick'];step=limits['step']
    hold=np.array([g['max_hold_seconds'] for g in geometries])[gi]
    stop_bps=np.array([g['stop_bps'] for g in geometries])[gi];target_bps=np.array([g['target_bps'] for g in geometries])[gi]
    long=sign>0;entry_raw=op*(1+sign*ENTRY_SLIP)
    entry=np.where(long,round_array(entry_raw,tick,True),round_array(entry_raw,tick,False))
    stop_raw=entry*(1-sign*stop_bps/10000);target_raw=entry*(1+sign*target_bps/10000)
    stop=np.where(long,round_array(stop_raw,tick,True),round_array(stop_raw,tick,False))
    target=np.where(long,round_array(target_raw,tick,True),round_array(target_raw,tick,False))
    cost=abs(entry-stop)+(entry+stop)*FEE_RATE+entry*EXIT_SLIP+tick
    qty=np.floor(np.minimum(RISK_BUDGET/cost,500/entry)/step)*step
    valid=(qty>=limits['min_qty'])&(np.minimum.reduce([entry,stop,target])*qty>=limits['min_notional'])&((entry-stop)*sign>0)
    enter=state['pending']&(state['qty']==0)&valid
    for key,value in [('qty',qty),('entry',entry),('stop',stop),('target',target)]:state[key][enter]=value[enter]
    state['opened'][enter]=ts
    q=state['qty'];active=q>0;sl=state['stop'];tp=state['target'];ep=state['entry']
    stop_hit=active&np.where(long,low<=sl,high>=sl)
    target_hit=active&~stop_hit&np.where(long,high>=tp,low<=tp)
    timeout=active&~stop_hit&~target_hit&(ts+300000-state['opened']>=hold*1000)
    exits=stop_hit|target_hit|timeout
    raw=np.full(len(q),close)
    raw[stop_hit]=np.where(long,np.minimum(op,sl),np.maximum(op,sl))[stop_hit]
    raw[target_hit]=tp[target_hit]
    px=raw*(1-sign*EXIT_SLIP);px=np.where(long,round_array(px,tick,False),round_array(px,tick,True))
    carry=ep*q*CARRY_PER_HOUR*(ts+300000-state['opened'])/3600000
    net=(px-ep)*q*sign-(px+ep)*q*FEE_RATE-carry
    stop_net=(sl-ep)*q*sign-(sl+ep)*q*FEE_RATE
    if np.any(stop_net[active]<-RISK_BUDGET-1e-7):raise AssertionError('shadow_stop_budget')
    state['trades'][exits]+=1;state['wins'][exits]+=(net[exits]>0)
    state['net'][exits]+=net[exits];state['gross_profit'][exits]+=np.maximum(net[exits],0);state['gross_loss'][exits]+=np.maximum(-net[exits],0)
    state['peak']=np.maximum(state['peak'],state['net']);state['drawdown']=np.maximum(state['drawdown'],state['peak']-state['net']);q[exits]=0
    # Current close creates only a pending NEXT-bar entry, never a same-bar fill.
    hits=[]
    for cs in conditions:
        ok=True
        for c in cs:
            v=feature.get(c['feature']);ok &= v is not None and np.isfinite(v) and (v>c['value'] if c['op']=='gt' else v<c['value'])
        hits.append(ok)
    state['pending']=np.array(hits,dtype=bool)[ci]&(q==0)
    state['last_bar']=np.array(ts);state['bars_seen']=state['bars_seen']+1

def run(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    lock=(root/'shadow.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    now=time.time();markets=universe(get('/perpetual/exchangeInfo'));layout=arrays();conditions,geometries,*_=layout
    report=dict(version=1,as_of=now,status='RUNNING',source='Yellow completed 5-minute candles',configurations=0,completed_simulated_trades=0,open_simulated_positions=0,markets={},errors={},live_enabled=False,definition='Independent counterfactual candle portfolios, not execution-validated paper fills or account P&L')
    for m,limits in sorted(markets.items()):
        path=root/(m+'.npz')
        try:
            rows=get('/klines',dict(symbol=m,interval='5m',limit=1000))
            rows=[r for r in rows if int(r[0])+300000<=now*1000];a=validate_candles(rows,now,minimum=250);f=features(a)
            if path.exists():
                with np.load(path,allow_pickle=False) as z:state={k:z[k].copy() for k in z.files}
                if str(state['catalog_hash'])!=digest([conditions,geometries]):raise ValueError('shadow_catalog_changed')
            else:state=initial(int(a[-1,0]),conditions,geometries)
            new=np.flatnonzero(a[:,0]>int(state['last_bar']))
            if len(new) and a[new[0],0]!=int(state['last_bar'])+300000:raise ValueError('shadow_history_gap')
            for i in new:observe(state,a[i],{k:v[i] for k,v in f.items()},limits,layout)
            tmp=path.with_suffix('.tmp')
            with tmp.open('wb') as handle:np.savez_compressed(handle,**state)
            tmp.replace(path)
            count=len(state['qty']);report['configurations']+=count;report['completed_simulated_trades']+=int(state['trades'].sum());report['open_simulated_positions']+=int((state['qty']>0).sum())
            report['markets'][m]=dict(configurations=count,registered_at=float(state['registered_at']),last_bar=int(state['last_bar']),bars_seen=int(state['bars_seen']),completed_simulated_trades=int(state['trades'].sum()),open_simulated_positions=int((state['qty']>0).sum()))
        except Exception as exc:report['errors'][m]=type(exc).__name__+':'+str(exc)[:120]
    report['status']='RUNNING' if not report['errors'] else 'DEGRADED';atomic(root/'status.json',report);print(json.dumps(report),flush=True)
    if report['errors']:raise RuntimeError('shadow_market_errors')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='/var/lib/why-discovery/bulk/shadow');a=p.parse_args();run(a.root)
