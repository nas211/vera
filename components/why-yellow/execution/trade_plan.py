"""Bounded, deterministic plans from completed-bar evidence. No prediction claim."""
from decimal import Decimal as D
from .loss_stop import loss_limit, stop_price

# Stress assumption, above the largest observed 18 October stop slippage (~8bp).
# This is a reserve, not a bound on future fills or funding.
EXIT_SLIPPAGE_FRACTION = D('.001')


def slippage_fraction(value=EXIT_SLIPPAGE_FRACTION):
    value = D(str(value))
    if not value.is_finite() or value < 0 or value > D('.01'):
        raise ValueError('execution_slippage_unacceptable')
    return max(EXIT_SLIPPAGE_FRACTION, value)


def observed_exit_slippage(ledger, account, market, now):
    """Use the worse of 10bp or 125% of recent adverse stop slippage.

    Seven-day account-owned fills only. Missing history uses the explicit stress
    floor; malformed recent stop economics block a new plan.
    """
    import json
    from .types import OrderState as O
    worst = EXIT_SLIPPAGE_FRACTION
    for order in ledger.orders_where([O.FILLED], account_id=account, market=market, purpose='STOP'):
        if order['state_ts'] < now-7*86400:
            continue
        meta = json.loads(order['meta'] or '{}')
        trigger, fill = D(meta['trigger_price']), D(order['avg_price'])
        if not all(v.is_finite() and v > 0 for v in (trigger, fill)) or order['side'] not in ('buy','sell'):
            raise ValueError('stop_execution_history_invalid')
        adverse = (fill-trigger)/trigger if order['side']=='buy' else (trigger-fill)/trigger
        worst = max(worst, adverse*D('1.25'))
    return slippage_fraction(worst)


def execution_reserve(notional, fee, budget, fraction=EXIT_SLIPPAGE_FRACTION):
    reserve = notional * slippage_fraction(fraction) * (1 + fee)
    trigger_budget = budget - reserve
    if not trigger_budget.is_finite() or trigger_budget <= 0:
        raise ValueError('execution_cost_exceeds_budget')
    return dict(execution_model_version=1, execution_reserve_usd=str(reserve),
                trigger_budget_usd=str(trigger_budget),
                exit_slippage_fraction=str(slippage_fraction(fraction)))


def build_plan(*, account, direction, price, filters, evidence, now, configured=None):
    if direction not in ('long', 'short') or evidence.get('version') != 1:
        raise ValueError('risk_evidence_invalid')
    age = D(str(now)) - D(str(evidence['as_of']))
    atr = D(str(evidence['hourly_atr_fraction']))
    reach = D(str(evidence['median_6h_favorable_fraction']))
    if not all(v.is_finite() for v in (age, atr, reach)) or not 0 <= age <= 120 or not 0 < atr < 1 or reach <= 0 or evidence['samples'] < 20:
        raise ValueError('risk_evidence_stale_or_insufficient')
    px = D(price); fee = max(D('.0004'), filters.taker_fee, D(str(evidence.get('confirmed_fee', 0))))
    if not px.is_finite() or px <= 0 or not fee.is_finite() or not 0 <= fee < D('.01'):
        raise ValueError('risk_price_or_fee_invalid')
    # One hourly ATR, with a 40bp noise floor. Do not squeeze oversized volatility.
    distance = max(D('.004'), atr)
    if distance > D('.02'):
        raise ValueError('volatility_exceeds_plan_limit')
    budget = min(D('1.80'), loss_limit(configured))
    slip = slippage_fraction(evidence.get('exit_slippage_fraction', EXIT_SLIPPAGE_FRACTION))
    target_net = D('2.50')
    # Reserve 5bp for entry movement in sizing; a stricter trigger leaves $0.20
    # below the universal cap but cannot guarantee a final loss under $2.
    per_unit = px * (distance + D('.0005') + slip*(1+fee) + fee*(2+distance)) + filters.tick
    qty = filters.round_qty_down(min(D(500)/(px*filters.contract_multiplier), budget/(per_unit*filters.contract_multiplier)))
    if qty <= 0 or qty < filters.min_qty:
        raise ValueError('risk_size_below_minimum')
    units = qty*filters.contract_multiplier
    paid = px*units*fee
    reserve = execution_reserve(px*units, fee, budget, slip)
    sl = stop_price(entry=px,qty=qty,multiplier=filters.contract_multiplier,direction=direction,
                    paid_fees=paid,realized_gross=0,exit_fee=fee,strategy_stop=px*(1-distance if direction=='long' else 1+distance),tick=filters.tick,configured=reserve['trigger_budget_usd'])
    tp = ((px+(target_net+paid)/units)/(1-fee) if direction=='long' else (px-(target_net+paid)/units)/(1+fee))
    tp = filters.round_price(tp,up=direction=='long')
    if min(px,sl,tp)*units < filters.min_notional or tp <= 0:
        raise ValueError('risk_protection_below_minimum')
    # Reach is recorded for review only; owner removed the 6h reach skip on
    # 2026-10-18 because it blocked nearly every trend-filtered entry.
    target_fraction = abs(tp/px-1)
    return dict(**reserve,version=1,account_id=account,direction=direction,ref_price=str(px),planned_qty=str(qty),
                stop_fraction=str(distance),risk_usd=str(budget),target_net_usd=str(target_net),
                notional_usd=str(px*units),estimated_stop=str(sl),estimated_target=str(tp),
                target_fraction=str(target_fraction),reachability=evidence,
                max_hold_seconds=21600,definition='Median observed six-hour favorable excursion; not a forecast or win-rate estimate')


def stored_plan(order):
    import json
    meta=json.loads(order.get('meta') or '{}')
    plan=meta.get('detail',{}).get('risk_trade_plan')
    if plan is None:return None
    if plan.get('version') not in (1,2) or plan.get('account_id')!=order['account_id'] or plan.get('direction')!=order['direction']:
        raise ValueError('risk_plan_identity_invalid')
    values={k:D(plan[k]) for k in ('planned_qty','stop_fraction','risk_usd','target_net_usd','notional_usd','ref_price')}
    if not all(v.is_finite() and v>0 for v in values.values()) or values['stop_fraction']>D('.02') or values['risk_usd']>D(2):
        raise ValueError('risk_plan_values_invalid')
    hold=plan.get('max_hold_seconds',0)
    if (plan['version']==1 and hold!=21600) or (plan['version']==2 and (type(hold) is not int or not 7200<=hold<=21600 or hold%300)) or values['notional_usd']>500:
        raise ValueError('risk_plan_limits_invalid')
    if any(k in plan for k in ('execution_model_version','trigger_budget_usd','execution_reserve_usd','exit_slippage_fraction')):
        trigger, reserve, fraction = (D(plan[k]) for k in ('trigger_budget_usd','execution_reserve_usd','exit_slippage_fraction'))
        if (plan.get('execution_model_version') != 1
                or not all(v.is_finite() and v > 0 for v in (trigger,reserve,fraction))
                or fraction < EXIT_SLIPPAGE_FRACTION
                or reserve < values['notional_usd']*fraction*(1+D('.0004'))
                or trigger+reserve > min(D('1.80'),values['risk_usd'])):
            raise ValueError('risk_execution_reserve_invalid')
    return plan


def build_agent_plan(*, account,direction,price,filters,ai,equity,now,configured=None,confirmed_fee=0,
                     exit_slippage_fraction=EXIT_SLIPPAGE_FRACTION):
    """Preserve frozen AI geometry, with the existing 10%-equity exposure ceiling."""
    px=D(price);equity=D(equity);fee=max(D('.0004'),filters.taker_fee,D(confirmed_fee or 0))
    distance=ai['sl_bps']/10000;target_distance=ai['tp_bps']/10000
    if direction not in ('long','short') or not all(x.is_finite() and x>0 for x in (px,equity,distance,target_distance)) or distance>D('.02') or not 7200<=ai['hold_bars']*300<=21600:
        raise ValueError('agent_plan_invalid')
    budget=min(D('1.80'),loss_limit(configured));cap=min(D(500),equity*D('.10')/D('1.01'))
    slip=slippage_fraction(exit_slippage_fraction)
    cost=px*(distance+D('.002')+slip*(1+fee)+fee*(2+distance))+filters.tick
    qty=filters.round_qty_down(min(cap/(px*filters.contract_multiplier),budget/(cost*filters.contract_multiplier)))
    if qty<=0 or qty<filters.min_qty:raise ValueError('agent_size_below_minimum')
    units=qty*filters.contract_multiplier;paid=px*units*fee;sign=1 if direction=='long' else -1
    reserve=execution_reserve(px*units,fee,budget,slip)
    sl=stop_price(entry=px,qty=qty,multiplier=filters.contract_multiplier,direction=direction,
        paid_fees=paid,realized_gross=0,exit_fee=fee,strategy_stop=px*(1-sign*distance),tick=filters.tick,configured=reserve['trigger_budget_usd'])
    tp=filters.round_price(px*(1+sign*target_distance),up=direction=='long')
    loss=paid+sl*units*fee-sign*(sl-px)*units
    target=sign*(tp-px)*units-paid-tp*units*fee
    if loss<=0 or target<loss*D('1.5'):raise ValueError('agent_net_reward_risk_too_low')
    if min(sl,tp,px)*units<filters.min_notional:raise ValueError('agent_protection_below_minimum')
    return dict(**reserve,version=2,account_id=account,direction=direction,ref_price=str(px),planned_qty=str(qty),
        stop_fraction=str(distance),risk_usd=str(budget),target_net_usd=str(target),notional_usd=str(px*units),
        estimated_stop=str(sl),estimated_target=str(tp),max_hold_seconds=ai['hold_bars']*300,
        definition='Frozen Discovery geometry with fees and stricter exposure limits; no fixed-dollar profit promise')
