"""Read-only handoff from research. Routes only Yellow-validated candidates.
No credentials here; the ordinary tenant execution engine owns every order.
"""
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from .tenants import CanonicalSignal


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class AIGateway:
    def __init__(self, root='/opt/why-strategy-platform',clock=time.time):
        self.root=Path(root);self.clock=clock;self.seen=set();self.error=None
        self.live_enabled=False;self.paper_enabled=False

    def poll(self,core_markets=()):
        now=self.clock();self.error=None
        self.live_enabled=False;self.paper_enabled=False
        try:
            cfg=json.loads((self.root/'config.json').read_text())
            self.live_enabled=bool(cfg['live']['enabled'])
            self.paper_enabled=bool(cfg.get('customer_paper',{}).get('enabled',False))
            if not (self.live_enabled or self.paper_enabled):return []
            snap=json.loads((self.root/'state/signals.json').read_text())
            if snap['venue']!='yellow' or not 0<=now-snap['ts']<=15:return []
            # Wait for the core producer to finish the same completed bar. Its
            # present positions are also reservations for later journal fanout.
            core=json.loads(Path(cfg['core_snapshot']).read_text())
            if not 0<=now-core['ts']/1000<=15 or not core['feed']['connected']:return []
            reserved=set(core_markets)|{p['market'] for p in core.get('open',[])}
            con=sqlite3.connect('file:'+str(self.root/'state/platform.sqlite3')+'?mode=ro',uri=True,timeout=.2)
            con.row_factory=sqlite3.Row
            out=[]
            try:
                for sig in snap['signals']:
                    if sig['id'] in self.seen or sig['market'] in reserved:continue
                    if not cfg['core_priority_delay_seconds']<=now-sig['ts']<=90 or now>sig['expires']:continue
                    if core['ts']/1000<sig['ts']+cfg['core_priority_delay_seconds']:continue
                    if sig['id']!=digest(dict(candidate=sig['candidate'],market=sig['market'],bar=sig['bar'])):continue
                    row=con.execute("select * from candidates where id=? and stage='LIVE_ELIGIBLE'",(sig['candidate'],)).fetchone()
                    if not row:continue
                    spec=json.loads(row['spec'])
                    if sig['spec']!=spec or digest(spec)!=row['id'] or sig['side']!=spec['side']:continue
                    stats=con.execute('select count(*) n,coalesce(sum(pnl),0) pnl from trades where candidate=? and evidence_ok=1 and opened>=?',
                                      (row['id'],row['paper_started'])).fetchone()
                    if stats['n']<10 or stats['pnl']<=0:continue
                    px=Decimal(str(sig['price']))
                    if not px.is_finite() or px<=0:continue
                    strategy=f"AI-{row['id']}-{spec['tp_bps']}-{spec['sl_bps']}-{spec['hold_bars']}"
                    from execution.ai_policy import parse
                    if not parse(strategy):continue
                    out.append((float(stats['pnl']),CanonicalSignal('ai:'+sig['id'],sig['market'],sig['side'],
                               strategy,px,sig['ts'],dict(candidate=row['id'],source='validated_forward_paper'))))
            finally:
                con.close()
            result=[];markets=set()
            for _,sig in sorted(out,key=lambda x:(-x[0],x[1].signal_id)):
                # One winner per coin per batch. The engine checks account-specific
                # ownership again and reserves exposure atomically.
                if sig.market in markets:continue
                markets.add(sig.market);result.append(sig);self.seen.add(sig.signal_id[3:])
            if len(self.seen)>10000:self.seen={s.signal_id[3:] for s in result}
            return result
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
            self.error=type(exc).__name__;return []
