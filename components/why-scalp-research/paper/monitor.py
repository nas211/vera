"""Read-only incremental replay publisher; never writes old paper state or live APIs."""
import argparse,json,time,os
from pathlib import Path
from .replay import Session,Chain
from .record import fingerprint

def publish(session,destination):
    result=session.result();result['published_at']=time.time()
    result['code_matches_frozen_protocol']=session.lock['files']==fingerprint()
    status=session.directory/'status.json'
    result['recorder_status']=json.loads(status.read_text()) if status.exists() else None
    # Public output contains no private account data or local directory layout.
    result['recording']=session.directory.name
    for r in result['results'].values():
        r['offline_unknown_order_count']=len(r.pop('offline_fill_uncertainty'))
        r['pending_order_count']=len(r.pop('pending_orders'))
    destination.parent.mkdir(parents=True,exist_ok=True)
    tmp=destination.with_suffix('.tmp');tmp.write_text(json.dumps(result,allow_nan=False));os.replace(tmp,destination)
    return result

def monitor(directory,destination):
    session=Session(directory);chain=Chain();part=1;file=None;last_pub=0
    while True:
        if file is None:
            path=directory/f'events-{part:05d}.jsonl'
            if path.exists():file=path.open()
        consumed=0
        if file:
            while consumed<10000:
                pos=file.tell();line=file.readline()
                if not line or not line.endswith('\n'):file.seek(pos);break
                row=chain.check(json.loads(line));session.ingest(row);consumed+=1
            if consumed==0 and (directory/f'events-{part+1:05d}.jsonl').exists():file.close();file=None;part+=1;continue
        if time.monotonic()-last_pub>5:
            publish(session,destination);last_pub=time.monotonic()
        if session.ended:
            publish(session,destination)
            # Durable final detailed artifacts are generated with the offline CLI;
            # no interim tuning or automated promotion is performed.
            return
        if not consumed:time.sleep(.5)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('recording',type=Path);p.add_argument('destination',type=Path);a=p.parse_args();monitor(a.recording,a.destination)
