"""Resource-bounded offline screening worker. Public reads, no order interface."""
import argparse,concurrent.futures,fcntl,gzip,hashlib,json,os,shutil,time
from pathlib import Path
import numpy as np
from . import rules
from .bulk_screen import (catalog,specification,validate_candles,features,signal_indices,next_entries,outcomes,portfolios,portfolio,training_pass,training_rank,select_training,fold_pass,PURGE_BARS)
from .bulk_history import history,save_snapshot,SOURCE
from .market import get,universe

def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,separators=(',',':'),allow_nan=False));tmp.replace(path)

def screen_market(args):
    market,limits,snapshot,folder,as_of=args;folder=Path(folder);start=time.monotonic()
    with gzip.open(snapshot,'rt') as f:rows=json.load(f)
    a=validate_candles(rows,as_of,minimum=8640);conditions,geometries=catalog();f=features(a)
    indices=[signal_indices(f,c) for c in conditions];nexts=next_entries(indices,len(a))
    cut1=int(len(a)*.6);cut2=int(len(a)*.8);count=passed=0;shortlist=[];finalists=[]
    output=folder/(market+'.results.jsonl.gz');tmp=output.with_suffix('.tmp')
    with gzip.open(tmp,'wt',compresslevel=3) as file:
        for side in ('long','short'):
            best={}
            for gi,g in enumerate(geometries):
                path=outcomes(a,side,g,limits);scores=portfolios(nexts,path,20,cut1-PURGE_BARS)
                for ci,score in enumerate(scores):
                    spec=specification(market,side,conditions[ci],g)
                    row=dict(fingerprint=rules.executable_identity(spec),market=market,side=side,condition_index=ci,geometry_index=gi,train=score)
                    file.write(rules.packed(row)+'\n');count+=1
                    if training_pass(score):
                        passed+=1
                        if ci not in best or training_rank(row)<training_rank(best[ci]):best[ci]=row
                if gi%10==0:atomic(folder/(market+'.progress.json'),dict(evaluated=count,training_passed=passed))
            # Freeze the shortlist using only training results, then inspect two
            # chronological later folds with max-hold purges between periods.
            frozen=select_training(list(best.values()),5)
            for row in frozen:
                ci=row['condition_index'];g=geometries[row['geometry_index']];path=outcomes(a,side,g,limits)
                folds=[portfolio(indices[ci],path,cut1,cut2-PURGE_BARS),portfolio(indices[ci],path,cut2,len(a))]
                item=dict(row,spec=specification(market,side,conditions[ci],g),later_folds=folds,passed=all(fold_pass(m) for m in folds))
                finalists.append(item)
                if item['passed']:shortlist.append(item)
    tmp.replace(output)
    summary=dict(market=market,evaluated=count,training_passed=passed,finalists=finalists,shortlist=shortlist,seconds=time.monotonic()-start,results_file=output.name,results_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),train_end_ms=int(a[cut1-PURGE_BARS-1,0]),later_fold_starts_ms=[int(a[cut1,0]),int(a[cut2,0])])
    atomic(folder/(market+'.summary.json'),summary);atomic(folder/(market+'.progress.json'),dict(evaluated=count,training_passed=passed,complete=True))
    return summary

def run(root,input_dir=None,workers=2):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    lock=(root/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if shutil.disk_usage(root).free<2_000_000_000:raise RuntimeError('research_disk_reserve')
    as_of=time.time();run_id=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime(as_of));folder=root/'runs'/run_id;folder.mkdir(parents=True)
    conditions,geometries=catalog();manifest=dict(version=1,run_id=run_id,as_of=as_of,source=SOURCE,conditions=conditions,geometries=geometries,snapshots={},errors={})
    status=dict(version=1,run_id=run_id,status='FETCHING',started=as_of,evaluated=0,planned=0,shortlisted=0,live_enabled=False,definition='Distinct parameter configurations in this run; not independent strategies or live trades',source=SOURCE)
    atomic(root/'status.json',status)
    if input_dir:
        old=json.loads((Path(input_dir)/'manifest.json').read_text());as_of=old['as_of'];manifest['as_of']=as_of
        if not 0<=time.time()-as_of<=7200:raise ValueError('seed_snapshot_stale')
        markets=old['markets']
        for m,s in old['snapshots'].items():
            src=Path(input_dir)/s['file'];shutil.copy2(src,folder/src.name);manifest['snapshots'][m]=s
        manifest['errors']=old['errors']
    else:
        markets=universe(get('/perpetual/exchangeInfo'))
        def fetch(m):
            rows=history(m,as_of);return m,save_snapshot(folder/(m+'.json.gz'),rows,m,as_of)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending={pool.submit(fetch,m):m for m in markets}
            for future in concurrent.futures.as_completed(pending):
                m=pending[future]
                try:_,snapshot=future.result();manifest['snapshots'][m]=snapshot
                except Exception as exc:manifest['errors'][m]=type(exc).__name__+':'+str(exc)[:150]
    manifest['markets']=markets;atomic(folder/'manifest.json',manifest)
    planned=len(markets)*2*len(conditions)*len(geometries);status.update(status='RUNNING',planned=planned)
    atomic(root/'status.json',status);summaries=[]
    args=[]
    for m,s in manifest['snapshots'].items():
        path=folder/s['file']
        with gzip.open(path,'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==s['sha256']
        args.append((m,markets[m],str(path),str(folder),as_of))
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(screen_market,a):a[0] for a in args}
        while futures:
            done,_=concurrent.futures.wait(futures,timeout=2,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                m=futures.pop(future)
                try:summaries.append(future.result())
                except Exception as exc:manifest['errors'][m]=type(exc).__name__+':'+str(exc)[:150]
            progress=[json.loads(p.read_text()) for p in folder.glob('*.progress.json')]
            status.update(evaluated=sum(p['evaluated'] for p in progress),training_passed=sum(p['training_passed'] for p in progress),markets_complete=len(summaries),heartbeat=time.time(),shortlisted=sum(len(s['shortlist']) for s in summaries))
            atomic(root/'status.json',status)
    status.update(status='COMPLETED' if not manifest['errors'] else 'COMPLETED_WITH_ERRORS',completed=time.time(),evaluated=sum(s['evaluated'] for s in summaries),training_passed=sum(s['training_passed'] for s in summaries),errors=manifest['errors'])
    shortlist=[dict(x,run_id=run_id,data_as_of=as_of,source=SOURCE) for s in summaries for x in s['shortlist']]
    status['shortlisted']=len(shortlist)
    latest=dict(status,shortlist=shortlist,limitations=['Historical OHLC proxy, not actual fills or account P&L.','Large correlated search; later folds are screening, not proof of profitability.','Fees 4bp per side; entry slippage 5bp; exit slippage 20bp; carry reserve 1bp/hour. Stress adds 5bp entry and 20bp exit.','Stops first if both barriers touch within one candle; no overlapping trades within a configuration.','Fresh Yellow forward paper evidence and independent agent review remain separate.'])
    atomic(folder/'summary.json',latest);atomic(root/'latest.json',latest);atomic(root/'status.json',status)
    print(json.dumps(status),flush=True)
    if manifest['errors']:raise RuntimeError('bulk_market_failures')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='/var/lib/why-discovery/bulk');p.add_argument('--input-dir');p.add_argument('--workers',type=int,choices=(1,2),default=2);a=p.parse_args()
    run(a.root,a.input_dir,a.workers)
