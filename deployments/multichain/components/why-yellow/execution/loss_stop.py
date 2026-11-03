"""Universal per-position loss protection, independent of entry sizing.

The trigger budgets trading fees and realized partial exits. Execution slippage,
funding and market gaps can still move final P&L beyond the trigger budget.
"""
from decimal import Decimal as D

MAX_LOSS_USD = D('2')


def loss_limit(configured=None):
    if configured is None:
        return MAX_LOSS_USD
    value = D(str(configured))
    if not value.is_finite() or value <= 0:
        raise ValueError('invalid dollar loss limit')
    return min(MAX_LOSS_USD, value)


def stop_price(*, entry, qty, multiplier, direction, paid_fees, realized_gross,
               exit_fee, strategy_stop, tick, configured=None, previous_stop=None):
    entry, qty, multiplier, paid_fees, realized_gross, exit_fee, strategy_stop, tick = (
        D(str(v)) for v in (entry, qty, multiplier, paid_fees, realized_gross,
                           exit_fee, strategy_stop, tick))
    values = (entry, qty, multiplier, paid_fees, realized_gross, exit_fee, strategy_stop, tick)
    if (not all(v.is_finite() for v in values)
            or min(entry, qty, multiplier, strategy_stop, tick) <= 0
            or paid_fees < 0 or not D(0) <= exit_fee < D('.01')
            or direction not in ('long', 'short')):
        raise ValueError('invalid dollar stop inputs')
    fee = max(exit_fee, D('.0004'))
    available = loss_limit(configured) + realized_gross - paid_fees
    units = qty * multiplier
    long = direction == 'long'
    bound = ((entry - available / units) / (1 - fee) if long
             else (entry + available / units) / (1 + fee))
    price = max(strategy_stop, bound) if long else min(strategy_stop, bound)
    if previous_stop is not None:
        old = D(str(previous_stop))
        if not old.is_finite() or old <= 0:
            raise ValueError('invalid previous stop')
        price = max(price, old) if long else min(price, old)
    # Round inward: up for long stops and down for short stops.
    from decimal import ROUND_CEILING, ROUND_FLOOR
    price = (price / tick).to_integral_value(ROUND_CEILING if long else ROUND_FLOOR) * tick
    if price <= 0:
        raise ValueError('dollar stop has no valid positive price')
    return price
