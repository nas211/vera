"""One receive-time replay core for frozen offline comparisons and forward reporting."""
import argparse,hashlib,json,copy
from pathlib import Path
from .record import canonical
from .engine import Engine
from .feed import Feed,FeedError

class Chain:
    def __init__(self):self.seq=0;self.prev='0'*64;self.last=-float('inf')
    def check(self,row):
        body=dict(row);h=body.pop('hash')
        if body['seq']!=self.seq+1 or body['prev']!=self.prev or body['ts']<self.last or hashlib.sha256(canonical(body).encode()).hexdigest()!=h:raise ValueError('corrupt/noncausal recording')
        self.seq=body['seq'];self.prev=h;self.last=body['ts'];return row

def records(directory):
    chain=Chain()
    for path in sorted(directory.glob('events-*.jsonl')):
        digest=path.with_suffix('.sha256')
        if not digest.exists():raise ValueError('recording chunk is not sealed: '+path.name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest.read_text().strip():raise ValueError('chunk hash mismatch')
        with path.open() as f:
            for line in f:yield chain.check(json.loads(line))
    done=directory/'completion.json'
    if done.exists():
        d=json.loads(done.read_text())
        if d['last_hash']!=chain.prev or d['last_seq']!=chain.seq:raise ValueError('incomplete recording')

class Session:
    def __init__(self,directory):
        self.directory=directory;self.lock=json.loads((directory/'protocol.json').read_text());self.c=self.lock['config']
        self.engines={};self.first=None;self.now=None;self.next_tick=None;self.stress={};self.metadata=False;self.ended=False;self.rows=0
        self.observer=Feed(self.c['coins']);self.connected=False
    def ingest(self,row):
        self.rows+=1;now=row['ts'];kind=row['kind'];self.now=now
        if self.first is None:self.first=now;self.next_tick=now
        if kind=='metadata':
            if self.metadata:raise ValueError('metadata changed within frozen session')
            self.metadata=True
            for scenario,s in self.c['scenarios'].items():
                for strategy in ('candidate','existing_paper','existing_live_rules'):
                    key=scenario+'/'+strategy;self.engines[key]=Engine(self.c,s,row['filters'],strategy);self.stress[key]=False
        while self.engines and self.next_tick<now-1e-8:
            for key,e in self.engines.items():
                e.now=self.next_tick;s=e.s;elapsed=self.next_tick-self.first
                offline=bool(s['outage_every'] and elapsed>=s['outage_every'] and elapsed%s['outage_every']<s['outage_seconds'])
                if offline and not self.stress[key]:e.disconnect('injected_outage')
                if not offline and self.stress[key]:
                    # Reconnect snapshot is the observer state strictly before this tick.
                    # Public data cannot resolve fills that occurred during the blackout.
                    e.connected=self.connected;e.feed=copy.deepcopy(self.observer)
                    for m,book in e.feed.books.items():
                        e.history[m].append((self.next_tick,(max(book['bids'])+min(book['asks']))/2))
                        for side in ('bids','asks'):
                            for px,q in book[side].items():e.depth[m,side,px]=q*s['depth_fraction']
                    for o in e.orders.values():e.cancel(o)
                    e.log('stress_recovery_snapshot')
                self.stress[key]=offline;e.step(self.next_tick)
            self.next_tick+=self.c['timer_seconds']
        if kind=='connect':self.connected=True;self.observer.reset()
        elif kind=='disconnect':self.connected=False;self.observer.reset()
        elif kind=='data' and self.connected:
            try:self.observer.receive(row['data'],now)
            except (FeedError,KeyError,ValueError,TypeError):self.connected=False;self.observer.reset()
        for key,e in self.engines.items():
            e.now=now
            if kind=='connect':
                if not self.stress[key]:e.connect()
            elif kind=='disconnect':e.disconnect(row.get('reason',kind))
            elif kind=='end':e.log('recording_end');e.connected=False
            elif kind=='data' and not self.stress[key] and e.connected:e.receive(row['data'],now)
            e.step(now)
        if kind=='end':self.ended=True;self.connected=False
    def result(self):
        return dict(mode='PAPER_ONLY',recording=str(self.directory),recording_rows=self.rows,metadata_available=self.metadata,recording_ended=self.ended,connected=self.connected,started=self.first,market_time=self.now,protocol_sha256=hashlib.sha256((self.directory/'protocol.json').read_bytes()).hexdigest(),config=self.c,frozen_source_hashes=self.lock['files'],results={k:e.report() for k,e in self.engines.items()},verdict='No profitability conclusion. Untouched forward period and adequate real-data sample required.')

def replay(directory,output):
    session=Session(directory)
    for row in records(directory):session.ingest(row)
    output.mkdir(parents=True,exist_ok=False);result=session.result()
    (output/'comparison.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    for key,e in session.engines.items():
        with (output/(key.replace('/','--')+'.jsonl')).open('x') as f:
            for ev in e.events:f.write(canonical(ev)+'\n')
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('recording',type=Path);p.add_argument('output',type=Path);a=p.parse_args();r=replay(a.recording,a.output);print(json.dumps({'arms':len(r['results']),'rows':r['recording_rows'],'verdict':r['verdict']}))
