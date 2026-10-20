"""Pause entries after two net losses, with an optional local-day halt.

The timer and its boundary survive restarts. Existing positions and exits never
use this gate. Losses use complete position economics including partial exits
and trading fees, plus funding when matched exchange history is available.
"""
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal as D
from .types import PositionState as P, OrderState as O

COOLDOWN_SECONDS = 30 * 60
LOSSES_BEFORE_COOLDOWN = 2


def key(account):
    return 'entry_loss_cooldown:' + account


def protection_key(account):
    return 'entry_protection_halt:' + account


def halt_after_protection_failure(engine, pos, reason):
    """Persistent account halt; only an operator review may clear this latch."""
    k = protection_key(pos['account_id'])
    if engine.ledger.kv_get(k) is None:
        engine.ledger.kv_set(k, dict(position_id=pos['position_id'],
                                   reason=reason, triggered_at=engine.clock()))


def restore_protection_halt(engine, account):
    """Migrate definitive failures recorded before persistent halts existed.

    A closed position (even a profitable close) does not resolve a protection
    failure. Transient readback flags alone are not definitive failures. Write
    the latch before the migration marker so a crash cannot skip protection.
    No automatic release is performed; reviewed operator recovery may clear the
    latch while retaining this completed migration marker.
    """
    marker = 'entry_protection_history_v1:' + account
    completed = engine.ledger.kv_get(marker)
    if completed is not None:
        if completed is not True:
            raise ValueError('invalid protection history migration marker')
        return
    failures = [f for f in engine.ledger.flags(code='POSITION_WITHOUT_PROTECTION')
                if f['account_id'] == account]
    if failures:
        failure = failures[-1]
        engine.ledger.kv_set(protection_key(account), dict(
            reason='historical_protection_failure', flag_id=failure['id'],
            triggered_at=failure['ts'], restored_at=engine.clock()))
    else:
        for order in engine.ledger.orders_where(O, account_id=account, purpose='CLOSE'):
            meta = json.loads(order['meta'] or '{}')
            if meta.get('reason') == 'PROTECTION_FAILED' or order['reason'] == 'PROTECTION_FAILED':
                engine.ledger.kv_set(protection_key(account), dict(
                    reason='historical_protection_failure', position_id=order['position_id'],
                    client_order_id=order['client_order_id'],
                    triggered_at=order['created_ts'], restored_at=engine.clock()))
                break
    engine.ledger.kv_set(marker, True)


def day_bounds(ts, timezone):
    zone = ZoneInfo(timezone)
    day = datetime.fromtimestamp(ts, zone).date()
    return (datetime.combine(day, time.min, zone).timestamp(),
            datetime.combine(day + timedelta(days=1), time.min, zone).timestamp())


def closed_net(engine, pos):
    """Reject incomplete economics rather than treating a missing close as zero."""
    entry = engine.ledger.get_order(pos['entry_coid']) if pos.get('entry_coid') else None
    if not entry or entry['account_id'] != pos['account_id']:
        raise ValueError('owned entry missing')
    orders = {o['client_order_id']: o for o in engine.ledger.orders_for_position(pos['position_id'])}
    orders[entry['client_order_id']] = entry
    eq = D(entry['filled_qty'])
    ep = D(entry['avg_price'])
    if not eq.is_finite() or not ep.is_finite() or min(eq, ep) <= 0:
        raise ValueError('invalid entry economics')
    xq, xv, fees = D(0), D(0), D(0)
    for order in orders.values():
        if order['account_id'] != pos['account_id']:
            raise ValueError('foreign order')
        qty, price, paid = (D(order[k]) for k in ('filled_qty', 'avg_price', 'fees'))
        fill_fees = [D(f['fee']) for f in engine.ledger.fills_for(order['client_order_id'])]
        if (not all(v.is_finite() for v in [qty, price, paid, *fill_fees])
                or min(qty, price, paid, *fill_fees) < 0 or (qty > 0 and price <= 0)):
            raise ValueError('invalid exit economics')
        fees += max(paid, sum(fill_fees, D(0)))
        if order['purpose'] != 'ENTRY':
            xq += qty
            xv += qty * price
    if xq != eq or pos['direction'] not in ('long', 'short'):
        raise ValueError('closed position fills incomplete')
    multiplier = engine.meta.filters(pos['market']).contract_multiplier
    if not multiplier.is_finite() or multiplier <= 0:
        raise ValueError('invalid contract multiplier')
    return (xv - eq * ep) * multiplier * (1 if pos['direction'] == 'long' else -1) - fees


def outcome(engine, pos, history):
    cached = history.get(pos['position_id'])
    if cached is None:
        return closed_net(engine, pos)
    entry = engine.ledger.get_order(pos['entry_coid']) if pos.get('entry_coid') else None
    if not entry or entry['account_id'] != pos['account_id']:
        raise ValueError('owned entry missing')
    ep, hp = D(entry['avg_price']), D(cached['entry_price'])
    eq, hq = D(entry['filled_qty']), D(cached['max_held'])
    gross, fees, funding = (D(cached[k]) for k in ('realized_pnl', 'total_trading_fee', 'net_funding_fee'))
    if (not all(v.is_finite() for v in (ep, hp, eq, hq, gross, fees, funding))
            or min(ep, hp, eq, hq) <= 0 or fees < 0 or eq != hq
            or abs(ep - hp) > max(ep, hp) * D('1e-16')
            or cached['market'] != pos['market'] or cached['direction'] != pos['direction']
            or not cached.get('closed_at') or not cached.get('id')):
        raise ValueError('exchange history identity unconfirmed')
    return gross - fees + funding


def status(engine, account):
    """Evaluate under the engine lock. A win cannot end an active timer early.

    Results closed during the waiting period do not start another cycle. After
    expiry the next cycle starts with zero losses, using only later closes.
    Reconstruct from the ledger on first use, and persist each triggered timer.
    """
    try:
        now = engine.clock()
        if engine.ledger.kv_get(protection_key(account)) is None:
            restore_protection_halt(engine, account)
        if engine.ledger.kv_get(protection_key(account)) is not None:
            return dict(blocked=True, reason='protection_failure_halt', until=None,
                        seconds_remaining=None, requires_review=True)
        if engine.cfg.entry_hold_reason:
            return dict(blocked=True, reason=engine.cfg.entry_hold_reason, until=None,
                        seconds_remaining=None, requires_review=True)
        timezone = engine.cfg.loss_halt_timezone
        daily = timezone is not None
        store_key = 'entry_daily_loss_halt:' + account if daily else key(account)
        reason = 'consecutive_losses_day_halt' if daily else 'consecutive_losses_cooldown'
        saved = engine.ledger.kv_get(store_key)
        until = float(saved['until']) if saved else 0.0
        if not D(str(until)).is_finite() or until < 0:
            raise ValueError('invalid cooldown timestamp')
        if until > now:
            return dict(blocked=True, reason=reason, until=until,
                        seconds_remaining=until-now, consecutive_losses=2,
                        trigger_position_id=saved['trigger_position_id'])
        try:
            with open(engine.ledger.path + '.history.json') as handle:
                history = json.load(handle)
            if not isinstance(history, dict):
                raise ValueError('invalid exchange history')
        except FileNotFoundError:
            history = {}
        streak = 0
        boundary = max(until, day_bounds(now, timezone)[0]) if daily else until
        positions = sorted(engine.ledger.positions_where([P.CLOSED], account_id=account),
                           key=lambda p: (p['state_ts'], p['position_id']))
        for pos in positions:
            if pos['state_ts'] <= until or (daily and pos['state_ts'] < boundary):
                continue
            if not D(str(pos['state_ts'])).is_finite() or pos['state_ts'] > now:
                raise ValueError('invalid close timestamp')
            pnl = outcome(engine, pos, history)
            streak = streak + 1 if pnl < 0 else 0
            if streak >= LOSSES_BEFORE_COOLDOWN:
                until = pos['state_ts'] + COOLDOWN_SECONDS
                if daily:
                    until = max(until, day_bounds(pos['state_ts'], timezone)[1])
                saved = dict(until=until, triggered_at=pos['state_ts'],
                             trigger_position_id=pos['position_id'], net_pnl=str(pnl))
                engine.ledger.kv_set(store_key, saved)
                engine.ledger.add_flag('P1', 'ENTRY_LOSS_COOLDOWN',
                                      'Two consecutive losses; new entries paused until ' + str(until), account, pos['market'])
                streak = 0
        blocked = until > now
        # Preserve a pre-upgrade cooldown which runs past local midnight.
        legacy = engine.ledger.kv_get(key(account)) if daily else None
        if legacy and (not D(str(legacy['until'])).is_finite() or float(legacy['until']) < 0):
            raise ValueError('invalid legacy cooldown timestamp')
        if legacy and float(legacy['until']) > max(now, until):
            until = float(legacy['until']); saved = legacy; blocked = True
        return dict(blocked=blocked, reason=reason if blocked else '',
                    until=until or None, seconds_remaining=max(0, until-now),
                    consecutive_losses=2 if blocked else streak,
                    trigger_position_id=saved['trigger_position_id'] if saved else None)
    except Exception as exc:
        return dict(blocked=True, reason='entry_risk_history_unconfirmed', error_type=type(exc).__name__)
