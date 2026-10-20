"""Bounded, account-local empirical memory and forward-paper evidence."""
from .fees import effective_net
import collections
from . import rules

FORWARD_MIN_TRADES=50
FORWARD_MIN_SECONDS=86400
FORWARD_MIN_PROFIT_FACTOR=1.2
FORWARD_MAX_DRAWDOWN=10.0

def valid(trades):
    return [t for t in trades if t.get('valid',True) and t.get('reason')!='OWNER_CLOSE']

def stats(trades):
    ts=valid(trades);values=[effective_net(t) for t in ts]
    positive=[v for v in values if v>0];negative=[v for v in values if v<0]
    total=peak=drawdown=0.
    for v in values:
        total+=v;peak=max(peak,total);drawdown=max(drawdown,peak-total)
    return dict(closed=len(ts),wins=len(positive),net=total,
        win_rate=len(positive)/len(ts) if ts else None,
        average_win=sum(positive)/len(positive) if positive else None,
        average_loss=sum(negative)/len(negative) if negative else None,
        expectancy=total/len(ts) if ts else None,
        profit_factor=sum(positive)/-sum(negative) if negative else None,
        no_losses=bool(positive) and not negative,max_drawdown=drawdown,
        exit_reasons=dict(collections.Counter(t['reason'] for t in ts)))

def validation(trades):
    ts=valid([t for t in trades if t.get('phase')=='FORWARD_PAPER']);result=stats(ts)
    elapsed=max(t['closed'] for t in ts)-min(t.get('opened',t['closed']) for t in ts) if ts else 0
    enough=len(ts)>=FORWARD_MIN_TRADES and elapsed>=FORWARD_MIN_SECONDS
    # Evidence label only; never changes position sizing or enables live execution.
    profitable=result['net']>0 and (result['no_losses'] or (result['profit_factor'] or 0)>=FORWARD_MIN_PROFIT_FACTOR)
    passed=enough and profitable and result['max_drawdown']<=FORWARD_MAX_DRAWDOWN
    return dict(**result,elapsed_seconds=elapsed,minimum_trades=FORWARD_MIN_TRADES,
        minimum_seconds=FORWARD_MIN_SECONDS,minimum_profit_factor=FORWARD_MIN_PROFIT_FACTOR,
        maximum_drawdown=FORWARD_MAX_DRAWDOWN,
        status='PAPER_QUALIFIED' if passed else 'NOT_QUALIFIED' if enough else 'COLLECTING',qualified=passed)

def summary(c):
    screen=[t for t in c['trades'] if t.get('phase')!='FORWARD_PAPER']
    forward=[t for t in c['trades'] if t.get('phase')=='FORWARD_PAPER']
    measured=[t['diagnostics'] for t in valid(c['trades']) if t.get('diagnostics')]
    diagnostics=dict(measured_trials=len(measured),
        mean_mfe_bps=sum(t.get('mfe_bps',0) for t in measured)/len(measured) if measured else None,
        mean_mae_bps=sum(t.get('mae_bps',0) for t in measured)/len(measured) if measured else None)
    return dict(id=c['id'],version=c['version'],created=c['created'],state=c['state'],spec=c['spec'],
        fingerprint=rules.executable_identity(c['spec']),screen=stats(screen),forward=stats(forward),
        diagnostics=diagnostics,retirement=c.get('retirement'),lineage=c.get('lineage'))

def memory(candidates,archived,markets):
    rows={x['id']:x for x in archived}
    rows.update({c['id']:summary(c) for c in candidates})
    relevant=[r for r in rows.values() if r['spec']['market'] in markets and (r['screen']['closed'] or r['forward']['closed'])]
    relevant.sort(key=lambda r:(r['state']=='RETIRED',r['created']),reverse=True)
    return dict(scope='this paper account only',versions=relevant[:24],
        omitted_versions=max(0,len(relevant)-24),
        interpretation='Observed valid trials, not causal proof. Do not repeat identical executable settings. Compare failures and successes; give no-trade when evidence is weak. Missing diagnostics are unknown, not zero movement.')
