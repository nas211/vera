"""Exchange-minimum Scalp clips, bounded by actual account equity."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING


def minimum_quantity(price, filters):
    price = Decimal(str(price))
    step = Decimal(str(filters['step']))
    minimum = max(Decimal(str(filters['min_qty'])), Decimal(str(filters['min_notional'])) / price)
    return (minimum / step).to_integral_value(rounding=ROUND_CEILING) * step


def budget_for_equity(equity):
    from .strategy import CONFIG, RULES
    try:
        equity = Decimal(str(equity))
        if not equity.is_finite() or equity < 0:
            raise ValueError('invalid_scalp_equity')
    except (InvalidOperation, ValueError, TypeError):
        equity = Decimal(0)
    gross = min(Decimal(str(CONFIG['gross_notional_cap'])), equity * CONFIG['leverage'])
    return dict(equity_usd=equity, gross_notional_cap_usd=gross,
                per_market_notional_cap_usd=min(Decimal(str(CONFIG['per_market_cap'])), gross),
                per_side_notional_cap_usd=min(Decimal(str(RULES['side_notional_limit'])), gross))
