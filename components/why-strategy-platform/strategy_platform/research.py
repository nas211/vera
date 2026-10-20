"""Enumerated hypotheses and read-only import of model-created lab hypotheses.
The enumerator is numerical search, not an LLM. Both use identical evidence gates.
"""
import itertools
import json
import math
import sqlite3
import time
from pathlib import Path
import random
import numpy as np
from .features import BAR, LOOKBACKS, REGIMES, summarize
from .store import identity, packed, event

FAMILIES = ('momentum', 'breakout', 'mean_reversion', 'range_rejection', 'acceleration')


def validate_spec(spec):
    required = {'family','lookback','threshold','side','regime','hold_bars','tp_bps','sl_bps'}
    if set(spec) != required: raise ValueError('invalid spec keys')
    if spec['family'] not in FAMILIES or spec['lookback'] not in LOOKBACKS:
        raise ValueError('unsupported feature')
    if spec['side'] not in ('long','short') or spec['regime'] not in (*REGIMES,'any'):
        raise ValueError('invalid direction or regime')
    for key, lo, hi in [('threshold',.1,3),('tp_bps',15,410),('sl_bps',15,410),('hold_bars',2,144)]:
        x=spec[key]
        if type(x) not in (int,float) or not math.isfinite(x) or not lo<=x<=hi:
            raise ValueError('invalid '+key)
    if type(spec['hold_bars']) is not int: raise ValueError('integer hold required')
    return spec


def add(db, spec, origin):
    validate_spec(spec)
    sid=identity(spec)
    db.execute('insert or ignore into candidates(id,spec,origin,created) values(?,?,?,?)',
               (sid,packed(spec),origin,time.time()))
    return sid


def seed(db):
    # 5*6*4*2*3*3 = 2,160 candidates. Frozen exit sets avoid unbounded fitting.
    for family,look,threshold,side,hold,exit_pair in itertools.product(
            FAMILIES, LOOKBACKS, (.3,.7,1.2,1.8), ('long','short'),
            (6,12,24), ((30,40),(60,80),(100,120))):
        add(db,dict(family=family,lookback=look,threshold=threshold,side=side,
                    regime='any',hold_bars=hold,tp_bps=exit_pair[0],sl_bps=exit_pair[1]),
            'systematic_generator_v1')
    db.commit()


def import_ai(db, path):
    if not path: return 0
    count=0
    if str(path).endswith('.json'):
        payload=json.loads(Path(path).read_text())
        for row in payload.get('hypotheses',[]):
            try:
                before=db.total_changes
                add(db,row['spec'],'ai_lab_message:'+str(row['message_id']))
                count+=db.total_changes-before
            except (ValueError,TypeError,KeyError):continue
        db.commit();return count
    with sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=2) as source:
        # Import original model hypotheses as leads, never old verdicts/trades.
        rows=source.execute("select id,body from messages where sender='research' and topic='candidate_proposed'")
        for mid,body in rows:
            try:
                spec=json.loads(body)['spec'];validate_spec(spec)
                before=db.total_changes
                add(db,spec,'ai_lab_message:'+str(mid));count+=db.total_changes-before
            except (ValueError,TypeError,KeyError,json.JSONDecodeError): continue
    db.commit()
    return count


def expand(db, now):
    """Bounded ongoing parameter exploration, independent of model availability."""
    from .store import get,put
    hour=int(now//3600)
    if get(db,'generation_hour')==hour:return
    if db.execute("select count(*) from candidates where stage='QUEUED'").fetchone()[0]>1000:return
    if db.execute('select count(*) from candidates').fetchone()[0]>=20000:return
    rng=random.Random(hour)
    for _ in range(256):
        add(db,dict(family=rng.choice(FAMILIES),lookback=rng.choice(LOOKBACKS),
                    threshold=round(rng.uniform(.2,2.5),2),side=rng.choice(('long','short')),
                    regime=rng.choice((*REGIMES,'any')),hold_bars=rng.choice((6,12,24,48)),
                    tp_bps=rng.choice((20,30,50,80,120)),sl_bps=rng.choice((30,50,80,120,160))),
            'systematic_generator_hour:'+str(hour))
    put(db,'generation_hour',hour);db.commit()


def mask(spec, f):
    side=1 if spec['side']=='long' else -1
    out=f['usable'] & (side*f['signals'][(spec['family'],spec['lookback'])]>=spec['threshold'])
    if spec['regime']!='any': out=out & (f['regime']==REGIMES.index(spec['regime']))
    return out


def replay(spec, bars, feats, cfg, begin, end):
    pnls=[]; fired=[]
    side=1 if spec['side']=='long' else -1
    for market,b in bars.items():
        left=max(193,int(len(b)*begin));right=int(len(b)*end)
        hits=np.flatnonzero(mask(spec,feats[market]))
        available=left
        for i in hits:
            if i<available or i<left or i+spec['hold_bars']+1>=right: continue
            start=i+1;final=start+spec['hold_bars']-1
            if not np.all(b[start:final+1,5]) or not np.all(np.diff(b[i:final+1,0])==BAR):
                continue
            entry=b[start,1]*(1+side*cfg['slippage_bps']/1e4)
            tp=entry*(1+side*spec['tp_bps']/1e4)
            sl=entry*(1-side*spec['sl_bps']/1e4)
            px=b[final,4];closed=final
            for k in range(start,final+1):
                if side*(b[k,1]-sl)<=0: px=b[k,1];closed=k;break
                stopped=b[k,3]<=sl if side==1 else b[k,2]>=sl
                target=b[k,2]>=tp if side==1 else b[k,3]<=tp
                if stopped: px=sl;closed=k;break
                if target: px=tp;closed=k;break
            px*=1-side*cfg['slippage_bps']/1e4
            gross=side*(px/entry-1)*cfg['paper_notional']
            costs=cfg['paper_notional']*(cfg['taker_bps']/1e4*(1+px/entry)+
                cfg['funding_reserve_bps_per_8h']/1e4*(closed-start+1)*BAR/28800000)
            pnls.append(gross-costs)
            fired.append((market,int(b[i,0]),int(b[closed,0])))
            available=closed+1
    return summarize(pnls),fired


def screen(db,bars,feats,cfg):
    if not bars:return 0
    batch=db.execute("select * from candidates where stage='QUEUED' order by case when origin like 'ai_lab%' then 0 else 1 end,created,id limit ?",(cfg['screen_batch'],)).fetchall()
    admitted=db.execute("select count(*) from candidates where stage in ('PAPER','LIVE_ELIGIBLE')").fetchone()[0]
    # Reserve testing capacity across families; equivalent trade paths are duplicates.
    for row in batch:
        spec=json.loads(row['spec'])
        train,_=replay(spec,bars,feats,cfg,0,.6)
        valid,fired=replay(spec,bars,feats,cfg,.6,1)
        fingerprint=identity(dict(side=spec['side'],trades=fired))
        passed=train['trades']>=10 and valid['trades']>=3 and train['pnl']>0 and valid['pnl']>0
        duplicate=db.execute("select id from candidates where fingerprint=? and stage in ('PAPER','LIVE_ELIGIBLE') limit 1",(fingerprint,)).fetchone()
        stage='PAPER' if passed and not duplicate and admitted<cfg['max_paper_strategies'] else 'REJECTED'
        reason=('historical_net_result' if not passed else 'duplicate_trade_path' if duplicate else
                'paper_capacity' if admitted>=cfg['max_paper_strategies'] else 'historical_pass')
        db.execute('update candidates set stage=?,screened=?,paper_started=?,historical=?,fingerprint=?,reason=? where id=?',
                   (stage,time.time(),time.time() if stage=='PAPER' else None,
                    packed(dict(train=train,validation=valid,data_kind='Yellow mark OHLC; modeled execution costs',
                                selected_repeatedly=True,dataset=cfg.get('_dataset_hash'),
                                costs={k:cfg[k] for k in ('taker_bps','slippage_bps','funding_reserve_bps_per_8h')},
                                notional=cfg['paper_notional'])),fingerprint,reason,row['id']))
        if stage=='PAPER':admitted+=1;event(db,'paper_admitted',row['id'])
        db.commit()
    db.commit();return len(batch)
