"""Append-only public Yellow recorder. Only public GET, connect/subscribe/heartbeat."""
import argparse, hashlib, json, os, signal, time, shutil, urllib.request
from pathlib import Path
from .feed import Feed, FeedError
ROOT=Path(__file__).resolve().parents[1]
INFO='https://trade.api.yellow.pro/perpetual/exchangeInfo'
WS='wss://trade.api.yellow.pro/ws'

def canonical(d):return json.dumps(d,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(root=ROOT):
    paths=list((root/'paper').glob('*.py'))+list((root/'vendor').glob('*.py'))+[root/'config.json',root/'vendor/sha256.json']
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
def freeze(directory,config):
    directory.mkdir(parents=True,exist_ok=False)
    lock=dict(mode='PAPER_ONLY',created=time.time(),duration=config['forward_seconds'],config=config,files=fingerprint(),status='frozen_before_first_observation',review_rule='No tuning, selection or stopping on interim P&L. Operational faults invalidate affected periods; any strategy change requires a new version and new untouched window.')
    (directory/'protocol.json').write_text(json.dumps(lock,indent=2));return lock
class Writer:
    def __init__(self,directory):
        self.root=directory;self.seq=0;self.prev='0'*64;self.file=None;self.chunk=0;self.bytes=0;self.last_sync=0
        self.start_mono=time.monotonic();self.start_wall=time.time();self.last_ts=self.start_wall
    def write(self,kind,**fields):
        # Monotonic-derived UTC avoids wall-clock reversal. Actual wall time retained.
        mono=time.monotonic();ts=self.start_wall+mono-self.start_mono
        if self.file is None or self.bytes>=64*1024*1024:
            if self.file:self.close_chunk()
            self.chunk+=1;self.path=self.root/f'events-{self.chunk:05d}.jsonl';self.file=self.path.open('x');self.bytes=0
        self.seq+=1
        row=dict(schema=1,seq=self.seq,ts=ts,wall_ts=time.time(),mono_ns=time.monotonic_ns(),kind=kind,prev=self.prev,**fields)
        row['hash']=hashlib.sha256(canonical(row).encode()).hexdigest();self.prev=row['hash']
        data=canonical(row)+'\n';self.file.write(data);self.bytes+=len(data.encode());self.file.flush();self.last_ts=ts
        if mono-self.last_sync>=1:os.fsync(self.file.fileno());self.last_sync=mono
        return row
    def close_chunk(self):
        self.file.flush();os.fsync(self.file.fileno());self.file.close()
        digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.path.with_suffix('.sha256').write_text(digest+'\n');self.file=None
    def close(self):
        if self.file:self.close_chunk()

def get_metadata(coins):
    # urllib uses configured proxy and CA trust, but never loads exchange credentials.
    import requests
    response=requests.get(INFO,timeout=20,auth=lambda request:request)
    response.raise_for_status();raw=response.text
    info=json.loads(raw);filters={}
    for sym in info['symbols']:
        if sym['symbol'] not in coins:continue
        f={v['filter_type']:v['config'] for v in sym['filters']}
        filters[sym['symbol']]=dict(tick=float(f['PRICE_FILTER']['tick_size']),step=float(f['LOT_SIZE']['step_size']),min_qty=float(f['LOT_SIZE']['min_qty']),min_notional=float(f['MIN_NOTIONAL']['min_notional']))
    if set(filters)!=set(coins):raise ValueError('missing market metadata')
    return filters,raw

def record(directory,seconds=None):
    import websocket
    lock=json.loads((directory/'protocol.json').read_text());config=lock['config']
    if config['mode']!='PAPER_ONLY' or lock['files']!=fingerprint():raise ValueError('frozen code/config hash mismatch')
    if list(directory.glob('events-*.jsonl')):raise ValueError('run already started; never overwrite or restart a frozen window')
    w=Writer(directory);deadline=time.monotonic()+(seconds if seconds is not None else lock['duration']);stop=False;metadata_done=False;feed=Feed(config['coins']);error=None;counts={'data':0,'disconnects':0}
    def shutdown(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    w.write('start',protocol_sha256=hashlib.sha256((directory/'protocol.json').read_bytes()).hexdigest(),requested_seconds=seconds if seconds is not None else lock['duration'])
    status_at=0
    try:
        while not stop and time.monotonic()<deadline:
            sock=None
            try:
                if not metadata_done:
                    filters,raw=get_metadata(config['coins']);w.write('metadata',filters=filters,raw=raw,url=INFO);metadata_done=True
                sock=websocket.create_connection(WS,timeout=10)
                sock.send(canonical({'id':1,'connect':{}}));w.write('connect',endpoint=WS);feed.reset()
                for i,(m,ch) in enumerate(((m,ch) for m in config['coins'] for ch in ('orderbook','trades')),2):
                    sock.send(canonical({'id':i,'subscribe':{'channel':f'public.{ch}.increment.{m}'}}))
                sock.settimeout(.5);last_received=time.monotonic()
                while not stop and time.monotonic()<deadline:
                    if shutil.disk_usage(directory).free<1024**3:raise OSError('disk reserve reached')
                    if time.monotonic()-last_received>20:raise TimeoutError('public websocket silent for 20s')
                    try:
                        raw=sock.recv()
                        if not raw:raise ConnectionError('socket closed')
                        last_received=time.monotonic()
                        if isinstance(raw,bytes):raw=raw.decode()
                        for line in raw.splitlines():
                            msg=json.loads(line)
                            if msg=={}:sock.send('{}');w.write('heartbeat',raw=line);continue
                            if 'error' in msg:raise FeedError('public subscription error: '+str(msg['error']))
                            d=msg.get('subscribe',{}).get('data') or msg.get('push',{}).get('pub',{}).get('data')
                            event=w.write('data' if d else 'control',data=d,raw=line)
                            if d:feed.receive(d,event['ts']);counts['data']+=1
                    except websocket.WebSocketTimeoutException:pass
                    now=time.monotonic()
                    if now-status_at>=1:
                        w.write('timer');status=dict(mode='PAPER_ONLY',updated=time.time(),counts=counts,last_error=error,remaining_seconds=max(0,deadline-now),frozen=True)
                        tmp=directory/'status.tmp';tmp.write_text(json.dumps(status,indent=2));tmp.replace(directory/'status.json');status_at=now
            except Exception as exc:
                error=type(exc).__name__+': '+str(exc);counts['disconnects']+=1
                w.write('disconnect',reason=error);feed.reset()
                tmp=directory/'status.tmp';tmp.write_text(json.dumps(dict(mode='PAPER_ONLY',updated=time.time(),counts=counts,last_error=error,remaining_seconds=max(0,deadline-time.monotonic()),frozen=True),indent=2));tmp.replace(directory/'status.json')
                if isinstance(exc,OSError) and str(exc)=='disk reserve reached':stop=True
                # Bounded retry; stop signals checked every 100ms.
                until=min(deadline,time.monotonic()+3)
                while not stop and time.monotonic()<until:time.sleep(.1)
            finally:
                if sock:sock.close()
        w.write('end',reason='interrupted' if stop else 'duration_complete',counts=counts)
    finally:w.close()
    (directory/'completion.json').write_text(json.dumps(dict(ended=time.time(),reason='interrupted' if stop else 'duration_complete',counts=counts,last_hash=w.prev,last_seq=w.seq),indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--seconds',type=float);p.add_argument('--freeze',action='store_true');a=p.parse_args()
    if a.freeze:freeze(a.directory,json.loads((ROOT/'config.json').read_text()))
    record(a.directory,a.seconds)
if __name__=='__main__':main()
