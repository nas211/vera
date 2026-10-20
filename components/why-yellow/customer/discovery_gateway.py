"""Read-only, owner-scoped handoff of freshly filled qualified Discovery trials.

No credentials, no order submission and no fallback to the older AI feed. A
hybrid account must be selected explicitly. Unsupported frozen execution rules
are rejected rather than silently translated.
"""
import hashlib,json,math,sqlite3,time
from decimal import Decimal as D
from pathlib import Path
from .tenants import CanonicalSignal

def fingerprint(spec):
    defaults=dict(entry='maker',breakeven_bps=0,trail_bps=0)
    pack=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
    value={k:v for k,v in spec.items() if k not in ('name','thesis') and (k not in defaults or v!=defaults[k])}
    value['conditions']=sorted({pack(c) for c in spec['conditions']})
    return hashlib.sha256(pack(value).encode()).hexdigest()

def qualified(trades, side=None):
    # Recompute from fee-model-v2 records; historical proxy runs cannot qualify.
    from .paper_validation import evaluate
    return evaluate(trades, side)['qualified']

def candidate_signal(candidate,account_id,user_id,now):
    c=candidate;s=c['spec'];p=c.get('position');r=c.get('review',{})
    if c['state']!='APPROVED_PAPER' or not p or not p.get('valid') or p.get('exit_reason') or p.get('phase')!='FORWARD_PAPER':return None
    if not 0<=now-p['opened']<=30 or not 0<=now-p.get('diagnostics',{}).get('last_observed_at',0)<=5:return None
    if not 0<=now-r.get('at',0)<=90:return None
    if (r.get('news') or {}).get('verdict')!='confirm' or (r.get('direction') or {}).get('action')!='open' or (r.get('direction') or {}).get('side')!=s['side'] or (r.get('gate') or {}).get('approved') is not True:return None
    if not qualified(c['trades'],s['side']):return None
    # Live adapter currently supports only taker entries and fixed stop/target.
    if s.get('entry','maker')!='taker' or s.get('breakeven_bps',0)!=0 or s.get('trail_bps',0)!=0 or s.get('mode')!='swing':return None
    sl=D(str(s['stop_bps']));tp=D(str(s['target_bps']));hold=s['max_hold_seconds']
    if not sl.is_finite() or not tp.is_finite() or not 40<=sl<=120 or not sl*D('1.5')<=tp<=180 or not 7200<=hold<=21600 or hold%300:return None
    price=D(str(p['entry']))
    if not price.is_finite() or price<=0:return None
    fp=fingerprint(s);strategy=f'AI-{fp}-{tp:f}-{sl:f}-{int(hold)//300}'
    evidence=dict(source='discovery_agents',account_id=account_id,user_id=user_id,candidate_id=c['id'],fingerprint=fp,
        reviewed_at=r['at'],paper_opened_at=p['opened'],forward_trades=len([t for t in c['trades'] if t.get('phase')=='FORWARD_PAPER']))
    signal_id=hashlib.sha256(f"discovery:{user_id}:{c['id']}:{p['opened']}".encode()).hexdigest()
    return CanonicalSignal(signal_id,s['market'],s['side'],strategy,price,p['opened'],dict(discovery=evidence))

class DiscoveryGateway:
    def __init__(self,path='/var/lib/why-discovery/paper.sqlite3',clock=time.time):
        self.path=Path(path);self.clock=clock;self.last_error=None
        self.by_account={};self.checked_at=None

    def status(self):
        return dict(checked_at=self.checked_at,last_error=self.last_error,by_account=dict(self.by_account))
    def poll(self,contexts):
        signals=[]
        self.checked_at=self.clock()
        self.by_account={}
        if not contexts:return signals
        try:
            with sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
                for ctx in contexts:
                    row=db.execute('SELECT payload FROM paper_accounts WHERE user_id=?',(str(ctx.user_id),)).fetchone()
                    info=dict(reason='paper_account_missing',candidates=0,eligible_signals=0)
                    self.by_account[str(ctx.account_id)]=info
                    if not row:continue
                    if len(row[0])>8_000_000:
                        info['reason']='paper_account_too_large';continue
                    account=json.loads(row[0])
                    info.update(running=bool(account.get('running')),kill=bool(account.get('kill')),
                                candidates=len(account.get('candidates',{})))
                    if account.get('kill'):
                        info['reason']='paper_risk_locked';continue
                    if not account.get('running'):
                        info['reason']='paper_research_paused';continue
                    info['reason']='awaiting_qualified_fresh_setup'
                    for c in account.get('candidates',{}).values():
                        try:
                            sig=candidate_signal(c,str(ctx.account_id),str(ctx.user_id),self.clock())
                            if sig:
                                signals.append(sig);info['eligible_signals']+=1;info['reason']='eligible_signal'
                        except (ValueError,TypeError,KeyError,ArithmeticError):continue
            self.last_error=None
        except (OSError,sqlite3.Error,ValueError,TypeError,AttributeError):
            self.last_error='discovery_evidence_unavailable'
            signals=[]
            self.by_account={str(ctx.account_id):dict(reason=self.last_error) for ctx in contexts}
        return signals
