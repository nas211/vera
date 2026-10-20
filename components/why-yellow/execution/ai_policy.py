"""Deterministic account-local arbitration for the new strategy sleeve.
No network, models, source fetching or external code execution.
"""
from decimal import Decimal as D
import json
import re
from .types import OrderState as O, PositionState as P
from .state_machine import ORDER_LIVE, ORDER_AMBIGUOUS

AI_RE=re.compile(r'^AI-([a-f0-9]{64})-([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)-([0-9]+)$')
OPEN=[P.PROTECTION_PENDING,P.PROTECTED,P.CLOSE_PENDING,P.RECONCILIATION_REQUIRED]
PENDING=list(ORDER_LIVE|ORDER_AMBIGUOUS|{O.INTENT,O.VALIDATED})


def parse(strategy):
    match=AI_RE.fullmatch(strategy)
    if not match:return None
    sid,tp,sl,hold=match.groups();tp=D(tp);sl=D(sl);hold=int(hold)
    if not 15<=tp<=410 or not 15<=sl<=410 or not 2<=hold<=144:return None
    return dict(id=sid,tp_bps=tp,sl_bps=sl,hold_bars=hold)


def risk_check(engine,sig,account,coid):
    """Called under the engine lock before persisting an intent. Pending orders
    reserve capacity, including sends whose outcome is unknown.
    """
    is_ai=parse(sig.strategy) is not None
    positions=engine.ledger.positions_where(OPEN,account_id=sig.account_id)
    pending=[o for o in engine.ledger.orders_where(PENDING,account_id=sig.account_id,purpose='ENTRY')
             if o['client_order_id']!=coid]
    ai_positions=[p for p in positions if parse(p['strategy'])]
    ai_pending=[o for o in pending if parse(o['strategy'])]
    if not (is_ai or ai_positions or ai_pending):return ''
    if not is_ai:
        # Existing filled AI positions retain ownership, regardless of side.
        if any(p['market']==sig.market for p in ai_positions):return 'ai_position_owns_market'
        if any(o['market']==sig.market for o in ai_pending):return 'ai_entry_not_cancelled'
        return ''
    if not engine.t.capabilities.venue_stop_orders:return 'ai_requires_exchange_stops'
    if not 0<=engine.clock()-sig.ts<=90:return 'ai_signal_stale'
    if any(p['market']==sig.market for p in positions) or any(o['market']==sig.market for o in pending):
        return 'market_already_owned'
    owned_entries={p['entry_coid'] for p in positions}
    reserved_slots=sum(o['client_order_id'] not in owned_entries for o in pending)
    if len(positions)+reserved_slots>=engine.cfg.limits.max_open_positions:return 'shared_position_limit'
    if len(ai_positions)+sum(o['client_order_id'] not in owned_entries for o in ai_pending)>=2:
        return 'ai_two_position_limit'
    equity=account.equity
    if not equity.is_finite() or equity<=0:return 'ai_equity_unavailable'
    cap=equity*D('.10');used=D(0)
    for p in ai_positions:
        fresh=engine.fresh.markets.get(p['market'])
        if not fresh or not engine.fresh.mark_fresh(p['market']):return 'ai_portfolio_mark_stale'
        px=max(D(p['entry_price']),D(str(fresh.last_mark)))
        used+=D(p['qty'])*px*engine.meta.filters(p['market']).contract_multiplier
    for o in ai_pending:
        meta=json.loads(o['meta'] or '{}')
        px=D(str(meta.get('ref_price',o.get('avg_price') or '0')))
        qty=D(o['qty'])-(D(o['filled_qty']) if o['client_order_id'] in owned_entries else D(0))
        if not px.is_finite() or px<=0:return 'ai_pending_price_unknown'
        used+=max(D(0),qty)*px*engine.meta.filters(o['market']).contract_multiplier
    # 1% headroom for a market fill differing from the checked reference price.
    if used+sig.notional_usd*D('1.01')>cap:return 'ai_ten_percent_equity_cap'
    performance=live_performance(engine, sig.strategy)
    if performance['unknown']:return 'ai_live_results_unconfirmed'
    if performance['paused']:return 'ai_strategy_loss_budget'
    return ''


def live_performance(engine,strategy):
    """Filled-order P&L with booked fees and a conservative funding reserve.
    Loss budget is three initial stop losses plus estimated costs, frozen once
    the strategy first fills for this account. Pausing is persistent.
    """
    info=parse(strategy)
    if not info:return dict(closed=0,pnl=D(0),unknown=False,paused=False)
    pos=engine.ledger.positions_where(list(P),account_id=None)
    pos=sorted((p for p in pos if p['strategy']==strategy),key=lambda p:p['opened_ts'])
    closed=0;pnl=D(0);unknown=False;initial_risk=None
    for p in pos:
        orders=engine.ledger.orders_for_position(p['position_id'])
        entry=engine.ledger.get_order(p['entry_coid']) if p['entry_coid'] else None
        if entry and not any(o['client_order_id']==entry['client_order_id'] for o in orders):orders.append(entry)
        entries=[o for o in orders if o['purpose']=='ENTRY']
        exits=[o for o in orders if o['purpose']!='ENTRY']
        eq=sum((D(o['filled_qty']) for o in entries),D(0))
        ev=sum((D(o['filled_qty'])*D(o['avg_price']) for o in entries),D(0))
        xq=sum((D(o['filled_qty']) for o in exits),D(0))
        xv=sum((D(o['filled_qty'])*D(o['avg_price']) for o in exits),D(0))
        fee=sum((D(o['fees']) for o in orders),D(0))
        if eq<=0:unknown=True;continue
        if initial_risk is None:initial_risk=ev*(info['sl_bps']+D(10))/D(10000)
        sign=D(1) if p['direction']=='long' else D(-1)
        end=p['state_ts'] if p['state']=='CLOSED' else engine.clock()
        reserve=ev*D('.0003')*D(str(max(0,end-p['opened_ts'])))/D(28800)
        if p['state']=='CLOSED':
            if xq!=eq:unknown=True;continue
            closed+=1;pnl+=sign*(xv-ev)-fee-reserve
        else:
            fresh=engine.fresh.markets.get(p['market'])
            if not fresh or not engine.fresh.mark_fresh(p['market']):unknown=True;continue
            remain=max(D(0),eq-xq)
            mark=D(str(fresh.last_mark))
            pnl+=sign*(xv+remain*mark-ev)-fee-reserve-remain*mark*D('.00035')
    key='ai_paused:'+strategy
    with engine.ledger._lock:
        paused=engine.ledger.db.execute('select v from kv where k=?',(key,)).fetchone() is not None
        if initial_risk is not None and pnl<=-3*initial_risk and not paused:
            with engine.ledger._tx():
                engine.ledger.db.execute('insert or replace into kv values(?,?)',(key,str(engine.clock())))
            paused=True
    return dict(closed=closed,pnl=pnl,unknown=unknown,paused=paused)
