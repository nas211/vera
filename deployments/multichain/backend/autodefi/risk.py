"""Validated research settings, never production-safe lending recommendations."""
import json
from decimal import Decimal as D
from .config import ROOT

def load_risk():
    value=json.loads((ROOT/'config/risk.json').read_text())
    for name,x in value.items():
        if name=='mode':continue
        n=D(str(x))
        if not n.is_finite() or n<=0:raise ValueError('Invalid research risk parameter')
    if not D(value['maxLtv'])<D(value['liquidationThreshold'])<1:raise ValueError('Invalid LTV ordering')
    if not 1<D(value['blockHealthBelow'])<D(value['reduceHealthBelow']):raise ValueError('Invalid health thresholds')
    for name in ('annualInterest','reserveRatio','liquidationDiscount','maxCapitalDrawdown'):
        if D(value[name])>=1:raise ValueError('Invalid fractional research parameter')
    if value['chainFreshSeconds']>30 or value['quoteFreshSeconds']>300:raise ValueError('Freshness may not be loosened')
    return value
RISK=load_risk()
