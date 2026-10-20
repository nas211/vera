import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from .store import connect, put, atomic_json
from .features import load_bars, features
from .research import seed, import_ai, screen, expand
from .paper import quotes, manage, enter, eligibility, publish


def archive_dataset(state,bars,cfg):
    import numpy as np
    h=hashlib.sha256()
    for market,b in sorted(bars.items()):h.update(market.encode());h.update(b.tobytes())
    key=h.hexdigest();directory=Path(state)/'datasets';directory.mkdir(exist_ok=True)
    file=directory/(key+'.npz')
    if not file.exists():np.savez_compressed(file,**bars)
    cfg['_dataset_hash']=key


def run(role,config,state,once=False):
    cfg=json.loads(Path(config).read_text());state=Path(state);state.mkdir(parents=True,exist_ok=True)
    lock=open(state/(role+'.lock'),'w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    db=connect(state/'platform.sqlite3')
    if role=='research':seed(db)
    bars={};feats={};last_load=0;last_import=0
    while True:
        try:
            now=time.time()
            if now-last_load>=30:
                # Paper needs only the bounded feature window, keeping price/stop
                # observation responsive under the service's CPU quota.
                bars=load_bars(cfg['source'],limit=400 if role=='paper' else 4000)
                feats={m:features(b) for m,b in bars.items()};last_load=now
            if role=='research':
                if now-last_import>=600:
                    try:import_ai(db,cfg['ai_hypotheses'])
                    except Exception as exc: print('AI hypothesis import unavailable:',type(exc).__name__,flush=True)
                    last_import=now
                if db.execute("select 1 from candidates where stage='QUEUED' limit 1").fetchone():
                    archive_dataset(state,bars,cfg)
                processed=screen(db,bars,feats,cfg)
                expand(db,time.time())
                atomic_json(state/'research.json',dict(ts=time.time(),state='screening' if processed else 'waiting_for_new_hypotheses',processed=processed,markets=len(bars)))
            else:
                now=time.time()
                q=quotes(cfg['source'],now)
                manage(db,q,now,cfg);eligibility(db,q,now,cfg)
                enter(db,bars,feats,q,now,cfg);publish(db,bars,feats,q,now,cfg,state)
            if once:return
        except Exception as exc:
            db.rollback()
            atomic_json(state/(role+'-error.json'),dict(ts=time.time(),error=type(exc).__name__,detail=str(exc)[:500]))
            print(role, type(exc).__name__,str(exc)[:500],flush=True)
            if once:raise
        time.sleep(cfg['screen_interval_seconds'] if role=='research' else cfg['poll_seconds'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['research','paper'])
    p.add_argument('--config',default='config.json');p.add_argument('--state',default='state');p.add_argument('--once',action='store_true')
    a=p.parse_args();run(a.role,a.config,a.state,a.once)
