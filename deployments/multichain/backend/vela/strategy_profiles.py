"""Vela PAPER strategy horizons. User selection never changes monetary risk caps."""
import copy
import json
from .reuse import rules, model_module

PROFILES={
    'swing':dict(id='swing',name='Swing',description='Look for sustained moves and pullbacks using closed 5-minute candles.',
        holdMin=7200,holdMax=21600,stopMinBps=40,stopMaxBps=120,targetMinBps=60,targetMaxBps=180,
        horizon='2–6 hours',researchInterval=180),
    'scalp':dict(id='scalp',name='Scalp',description='Look for shorter momentum or reversal opportunities using closed 5-minute candles.',
        holdMin=300,holdMax=1800,stopMinBps=20,stopMaxBps=60,targetMinBps=30,targetMaxBps=120,
        horizon='5–30 minutes',researchInterval=60),
}

def profile(name='swing'):
    if not isinstance(name,str) or name not in PROFILES:raise ValueError('Choose Scalp or Swing')
    return dict(PROFILES[name])

def research_schema(name):
    profile(name)
    schema=copy.deepcopy(model_module.RESEARCH)
    schema['properties']['strategies']['items']['properties']['mode']['enum']=[name]
    return schema

def validate(spec,markets,name):
    p=profile(name)
    fields={'name','thesis','market','mode','side','conditions','match','stop_bps','target_bps','max_hold_seconds'}
    execution={'entry','breakeven_bps','trail_bps'}
    if not isinstance(spec,dict) or not fields<=set(spec)<=fields|execution:raise ValueError('strategy_schema')
    if not all(isinstance(spec[k],str) and 1<=len(spec[k])<=limit for k,limit in [('name',80),('thesis',800)]):raise ValueError('strategy_text')
    if spec['market'] not in markets or spec['mode']!=name or spec['side'] not in ('long','short') or spec['match'] not in ('all','any'):raise ValueError('strategy_market_or_mode')
    for field,lo,hi in [('stop_bps',p['stopMinBps'],p['stopMaxBps']),('target_bps',p['targetMinBps'],p['targetMaxBps']),('max_hold_seconds',p['holdMin'],p['holdMax'])]:
        if not rules.finite(spec[field]) or not lo<=spec[field]<=hi:raise ValueError('strategy_'+field)
    if spec['target_bps']<1.5*spec['stop_bps']:raise ValueError('reward_risk')
    if spec.get('entry','taker')!='taker' or spec.get('breakeven_bps',0)!=0 or spec.get('trail_bps',0)!=0:raise ValueError('Unsupported paper execution style')
    if not isinstance(spec['conditions'],list) or not 1<=len(spec['conditions'])<=8:raise ValueError('strategy_conditions')
    for condition in spec['conditions']:
        if not isinstance(condition,dict) or set(condition)!={'feature','op','value'} or condition['feature'] not in rules.FEATURES or condition['op'] not in ('gt','lt') or not rules.finite(condition['value']) or abs(condition['value'])>1e6:raise ValueError('strategy_condition')
    return json.loads(rules.packed(dict(spec,entry='taker',breakeven_bps=0,trail_bps=0)))
