"""Immutable, bounded AI-authored rule language. Never evaluates Python/code."""
import hashlib
import json
import math
from .fees import effective_net

# Stop exits slip ~18bp in observed paper trades; the target must leave room for that.
MIN_REWARD_RISK=1.5
SCALP_MAX_TARGET_BPS=40
# 90s scalps rarely reached target (3 of 95 exits); replay favoured multi-hour holds.
# Positions close before the 8h funding settlement, so holds above ~6h leave little entry window.
MIN_HOLD_SECONDS=7200
MAX_HOLD_SECONDS=21600
MIN_STOP_BPS=40
# The gate rejects orders >2% from mid, so take-profits must rest inside that with room for drift.
# The 4h replay was positive with 120-200bp targets; nothing wider was tested.
MAX_TARGET_BPS=180
def geometry_ok(spec):
    return (spec['target_bps']>=MIN_REWARD_RISK*spec['stop_bps'] and spec['mode']=='swing'
            and MIN_HOLD_SECONDS<=spec['max_hold_seconds']<=MAX_HOLD_SECONDS and spec['stop_bps']>=MIN_STOP_BPS and spec['target_bps']<=MAX_TARGET_BPS)

ENTRY_STYLES=('maker','taker')
EXECUTION=dict(entry='maker',breakeven_bps=0,trail_bps=0)
def execution(spec): return {k:spec.get(k,v) for k,v in EXECUTION.items()}

FEATURES = ('return_1','return_5','return_20','volatility','range_position',
            'volume_ratio','book_imbalance','spread_bps','trade_imbalance','funding_bps')

def packed(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)

def finite(value):
    return type(value) in (int,float) and math.isfinite(value)

def validate(spec, markets):
    fields={'name','thesis','market','mode','side','conditions','match','stop_bps','target_bps','max_hold_seconds'}
    # Per-strategy entry/exit management. Optional so frozen legacy versions keep their identity.
    if not isinstance(spec,dict) or not fields<=set(spec)<=fields|set(EXECUTION): raise ValueError('strategy_schema')
    if spec.get('entry','maker') not in ENTRY_STYLES: raise ValueError('strategy_entry')
    for k,lo,hi in [('breakeven_bps',5,800),('trail_bps',5,400)]:
        if k in spec and not (finite(spec[k]) and (spec[k]==0 or lo<=spec[k]<=hi)): raise ValueError('strategy_'+k)
    if spec.get('breakeven_bps',0) and spec['breakeven_bps']>=spec['target_bps']: raise ValueError('strategy_breakeven_bps')
    if not all(isinstance(spec[k],str) and 1<=len(spec[k])<=lim for k,lim in [('name',80),('thesis',800)]): raise ValueError('strategy_text')
    if spec['market'] not in markets or spec['mode'] not in ('swing','scalp') or spec['side'] not in ('long','short') or spec['match'] not in ('all','any'): raise ValueError('strategy_market_or_mode')
    for k,lo,hi in [('stop_bps',5,400),('target_bps',5,800),('max_hold_seconds',10,43200)]:
        if not finite(spec[k]) or not lo<=spec[k]<=hi: raise ValueError('strategy_'+k)
    if spec['mode']=='scalp': raise ValueError('scalp_disabled')
    if not MIN_HOLD_SECONDS<=spec['max_hold_seconds']<=MAX_HOLD_SECONDS: raise ValueError('hold_window')
    if spec['stop_bps']<MIN_STOP_BPS: raise ValueError('stop_too_tight')
    if spec['target_bps']<MIN_REWARD_RISK*spec['stop_bps']: raise ValueError('reward_risk')
    if spec['target_bps']>MAX_TARGET_BPS: raise ValueError('target_too_wide')
    if not isinstance(spec['conditions'],list) or not 1<=len(spec['conditions'])<=8: raise ValueError('strategy_conditions')
    for c in spec['conditions']:
        if not isinstance(c,dict) or set(c)!={'feature','op','value'} or c['feature'] not in FEATURES or c['op'] not in ('gt','lt') or not finite(c['value']) or abs(c['value'])>1e6: raise ValueError('strategy_condition')
    # Default execution settings are omitted so equivalent specs keep one identity.
    return json.loads(packed({k:v for k,v in spec.items() if k not in EXECUTION or v!=EXECUTION[k]}))

def same_settings(a,b):
    settings=lambda s:dict({k:v for k,v in s.items() if k not in ('name','thesis')},**execution(s))
    return settings(a)==settings(b)

def identity(spec): return hashlib.sha256(packed(spec).encode()).hexdigest()

def executable_identity(spec):
    value={k:v for k,v in spec.items() if k not in ('name','thesis') and (k not in EXECUTION or v!=EXECUTION[k])}
    value['conditions']=sorted({packed(c) for c in spec['conditions']})
    return identity(value)

def triggered(spec, features):
    values=[]
    for c in spec['conditions']:
        v=features.get(c['feature'])
        if not finite(v): return False
        values.append(v>c['value'] if c['op']=='gt' else v<c['value'])
    return all(values) if spec['match']=='all' else any(values)

def metrics(trades):
    eligible=[t for t in trades if t.get('reason')!='OWNER_CLOSE' and t.get('valid',True)]
    first=eligible[:10]
    wins=sum(effective_net(t)>0 for t in first)
    return dict(closed=len(first),wins=wins,net=sum(effective_net(t) for t in first),
                win_rate=wins/len(first) if first else None,
                qualified=len(first)==10 and wins>=6 and sum(effective_net(t) for t in first)>0,
                complete=len(first)==10,
                excluded=sum(t.get('reason')=='OWNER_CLOSE' or not t.get('valid',True) for t in trades))
