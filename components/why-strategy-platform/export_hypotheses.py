"""Export bounded model hypotheses, never lab credentials or private memory."""
import json
from pathlib import Path
import sqlite3
import time

source='/opt/why-shadow-lab/state/lab.sqlite3'
target=Path('/opt/why-strategy-platform/imports/hypotheses.json')
while True:
    try:
        con=sqlite3.connect('file:'+source+'?mode=ro',uri=True,timeout=2)
        rows=con.execute("select id,body from messages where sender='research' and topic='candidate_proposed' order by id desc limit 2000").fetchall()
        con.close()
        out=[]
        for mid,body in rows:
            try:
                spec=json.loads(body)['spec']
                if isinstance(spec,dict) and len(json.dumps(spec))<2000:
                    out.append(dict(message_id=mid,spec=spec))
            except (ValueError,KeyError,TypeError):continue
        tmp=target.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(ts=time.time(),hypotheses=out),allow_nan=False)+'\n')
        tmp.chmod(0o644);tmp.replace(target)
    except Exception as exc:print(type(exc).__name__,str(exc)[:200],flush=True)
    time.sleep(60)
