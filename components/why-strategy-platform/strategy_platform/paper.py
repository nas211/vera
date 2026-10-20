"""Forward-only simulated trades. Historical outcomes never enter this ledger."""
import json
import math
import time
from pathlib import Path
from .features import BAR
from .research import mask
from .store import identity, packed, event, atomic_json


def quotes(source, now):
    try:
        snap=json.loads((Path(source)/'snapshot.json').read_text())
        if not 0<=now-snap['ts']/1000<=15 or not snap['feed']['connected']:return {}
        out={}
        for market,row in snap.get('forming',{}).items():
            if not row:continue
            px=float(row['c']);ts=float(row['last_ms'])/1000
            if math.isfinite(px) and px>0 and 0<=now-ts<=15:out[market]=(px,ts)
        return out
    except (OSError,ValueError,KeyError,TypeError):return {}


def close(db,p,px,ts,reason,cfg):
    sign=1 if p['side']=='long' else -1
    exit_px=px*(1-sign*cfg['slippage_bps']/1e4)
    gross=sign*(exit_px/p['entry']-1)*p['notional']
    costs=p['notional']*(cfg['taker_bps']/1e4*(1+exit_px/p['entry'])+
          cfg['funding_reserve_bps_per_8h']/1e4*max(0,ts-p['opened'])/28800)
    db.execute('insert or ignore into trades values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (p['id'],p['candidate'],p['market'],p['side'],p['opened'],ts,
                p['entry'],exit_px,p['notional'],gross,costs,gross-costs,reason,p['evidence_ok']))
    db.execute('delete from positions where id=?',(p['id'],))


def manage(db, q, now, cfg):
    for r in db.execute('select * from positions').fetchall():
        p=dict(r);quote=q.get(p['market'])
        if quote is None:
            if now-p['last_quote']>cfg['quote_max_age_seconds']:
                db.execute('update positions set evidence_ok=0 where id=?',(p['id'],))
            continue
        px,ts=quote
        if ts<=p['last_quote']:continue
        if ts-p['last_quote']>cfg['quote_max_age_seconds']:p['evidence_ok']=0
        db.execute('update positions set last_quote=?,evidence_ok=? where id=?',
                   (ts,p['evidence_ok'],p['id']))
        sign=1 if p['side']=='long' else -1
        reason=('STOP' if sign*(px-p['stop'])<=0 else
                'TARGET' if sign*(px-p['target'])>=0 else
                'TIMEOUT' if ts>=p['expires'] else None)
        if reason:close(db,p,px,ts,reason,cfg)
    db.commit()


def enter(db,bars,feats,q,now,cfg):
    rows=db.execute("select * from candidates where stage in ('PAPER','LIVE_ELIGIBLE')").fetchall()
    for row in rows:
        spec=json.loads(row['spec']);sid=row['id']
        count=db.execute('select count(*) from positions where candidate=?',(sid,)).fetchone()[0]
        # Rotate equal-signal market preference by candidate, not by dictionary order.
        for market in sorted(bars,key=lambda m:identity(dict(candidate=sid,market=m))):
            if count>=cfg['max_paper_positions_per_strategy']:break
            b=bars[market];bar=int(b[-1,0]);closed=(bar+BAR)/1000
            if not row['paper_started']<closed<=now or now-closed>cfg['max_signal_age_seconds']:continue
            if market not in q or not mask(spec,feats[market])[-1]:continue
            if db.execute('select 1 from positions where candidate=? and market=?',(sid,market)).fetchone():continue
            if db.execute('select 1 from seen where candidate=? and market=? and bar=?',(sid,market,bar)).fetchone():continue
            px,ts=q[market];sign=1 if spec['side']=='long' else -1
            entry=px*(1+sign*cfg['slippage_bps']/1e4)
            target=entry*(1+sign*spec['tp_bps']/1e4)
            stop=entry*(1-sign*spec['sl_bps']/1e4)
            pid=identity(dict(candidate=sid,market=market,bar=bar))
            db.execute('insert into positions values(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pid,sid,market,spec['side'],entry,cfg['paper_notional'],target,stop,
                        ts,ts+spec['hold_bars']*BAR/1000,ts,1))
            db.execute('insert into seen values(?,?,?)',(sid,market,bar));count+=1
    db.commit()


def eligibility(db,q,now,cfg):
    for row in db.execute("select * from candidates where stage in ('PAPER','LIVE_ELIGIBLE')").fetchall():
        stats=db.execute('select count(*) n,coalesce(sum(pnl),0) pnl from trades where candidate=? and evidence_ok=1',
                         (row['id'],)).fetchone()
        all_pnl=db.execute('select coalesce(sum(pnl),0) from trades where candidate=?',(row['id'],)).fetchone()[0]
        open_pnl=0.;healthy=True
        for p in db.execute('select * from positions where candidate=?',(row['id'],)):
            quote=q.get(p['market'])
            if quote is None or not p['evidence_ok']:healthy=False;continue
            sign=1 if p['side']=='long' else -1
            exit_px=quote[0]*(1-sign*cfg['slippage_bps']/1e4)
            open_pnl+=p['notional']*(sign*(exit_px/p['entry']-1)-
                       cfg['taker_bps']/1e4*(1+exit_px/p['entry'])-
                       cfg['funding_reserve_bps_per_8h']/1e4*max(0,now-p['opened'])/28800)
        passed=stats['n']>=cfg['minimum_paper_trades'] and stats['pnl']>0 and all_pnl+open_pnl>0 and healthy
        stage='LIVE_ELIGIBLE' if passed else 'PAPER'
        reason='ten_forward_trades_positive_net' if passed else 'collecting_forward_evidence'
        if all_pnl+open_pnl<=-cfg['paper_loss_budget']:stage='RETIRED';reason='paper_loss_budget'
        if stage!=row['stage']:
            db.execute('update candidates set stage=?,reason=? where id=?',(stage,reason,row['id']))
            event(db,'stage_changed',row['id'],from_stage=row['stage'],to_stage=stage,
                  count=stats['n'],closed_pnl=stats['pnl'],open_pnl=open_pnl)
    db.commit()


def publish(db,bars,feats,q,now,cfg,state):
    # Signals are current opportunities, not a replay of the ten paper entries.
    entries=[];models={}
    rows=db.execute("select * from candidates where stage='LIVE_ELIGIBLE'").fetchall()
    for row in rows:
        spec=json.loads(row['spec']);models[row['id']]=spec
        for market,b in bars.items():
            closed=(int(b[-1,0])+BAR)/1000
            if market not in q or not 0<=now-closed<=cfg['max_signal_age_seconds']:continue
            if not mask(spec,feats[market])[-1]:continue
            sigid=identity(dict(candidate=row['id'],market=market,bar=int(b[-1,0])))
            db.execute('insert or ignore into signals values(?,?,?,?,?,?,?,?)',
                       (sigid,row['id'],market,spec['side'],int(b[-1,0]),closed,
                        closed+cfg['max_signal_age_seconds'],q[market][0]))
            entries.append(dict(id=sigid,candidate=row['id'],market=market,side=spec['side'],
                                bar=int(b[-1,0]),ts=closed,expires=closed+cfg['max_signal_age_seconds'],
                                price=q[market][0],spec=spec))
    db.commit()
    atomic_json(Path(state)/'signals.json',dict(ts=now,venue=cfg['venue'],signals=entries))
    stages={r[0]:r[1] for r in db.execute('select stage,count(*) from candidates group by stage')}
    totals=dict(db.execute('select count(*) closed_trades,coalesce(sum(pnl),0) simulated_pnl from trades').fetchone())
    totals['open_positions']=db.execute('select count(*) from positions').fetchone()[0]
    totals['valid_forward_trades']=db.execute('select count(*) from trades where evidence_ok=1').fetchone()[0]
    atomic_json(Path(state)/'status.json',dict(ts=now,stages=stages,paper=totals,
        fresh_markets=len(q),mode='FORWARD_PAPER_AND_PROMOTION',
        live=cfg['live'],minimum_paper_trades=cfg['minimum_paper_trades'],minimum_days=0,
        source='Yellow public mark prices; modeled costs and fills',
        note='Numerical variations plus existing AI-lab hypotheses. Paper totals across separate strategy trials are not one portfolio return.'))
