"""AI paper risk and exit rules; no exchange client or live order path."""
from decimal import Decimal as D
from execution.ai_policy import parse


def entry_budget(account, positions, signal, now, mark_times):
    ai = parse(signal.strategy)
    same = [p for p in positions if p['market'] == signal.market]
    if same and (ai or any(parse(p['strategy']) for p in same)):
        return 'BLOCKED:market_owned', None
    if not ai:
        return '', None
    if not 0 <= now - signal.ts <= 90:
        return 'BLOCKED:ai_stale_signal', None
    if not 0 <= now - mark_times.get(signal.market, 0) <= 30:
        return 'BLOCKED:ai_stale_mark', None
    opened = [p for p in positions if parse(p['strategy'])]
    if len(opened) >= 2:
        return 'BLOCKED:ai_two_position_limit', None
    if any(not 0 <= now - mark_times.get(p['market'], 0) <= 30 for p in opened):
        return 'BLOCKED:ai_portfolio_mark_stale', None
    equity = D(account['equity'])
    if not equity.is_finite() or equity <= 0:
        return 'BLOCKED:ai_equity_unavailable', None
    used = sum((D(p['qty']) * max(D(p['entry_price']), D(p['mark_price'])) for p in opened), D(0))
    # Two slots share 10% of equity. Each new trade takes at most 5%,
    # also bounded by the customer's ordinary size/allocation and free margin.
    budget = min(equity * D('.05'), equity * D('.10') - used) / D('1.01')
    if budget <= 0:
        return 'BLOCKED:ai_ten_percent_equity_cap', None
    return '', budget


def exit_reason(position, price, now):
    spec = parse(position['strategy'])
    if not spec:
        return None
    entry = D(position['entry_price'])
    move = (D(price) / entry - 1) * (1 if position['direction'] == 'long' else -1) * 10000
    if move <= -spec['sl_bps']:
        return 'AI_STOP'
    if move >= spec['tp_bps']:
        return 'AI_TARGET'
    if now - position['opened_at'].timestamp() >= spec['hold_bars'] * 300:
        return 'AI_TIMEOUT'
    return None
