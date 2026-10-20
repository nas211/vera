"""Automatic default sizing for brand-new accounts.

Today, an account with no trade_margin_pct and no fixed_entry_notional override
trades at the venue's exchange-minimum lot no matter how much the owner
deposited (see tenants.py submit()). That is safe but ignores the money the
person actually put in. This agent picks a one-time, risk-tiered starting
trade_margin_pct from the deposited balance the moment an account activates,
so a new user gets a sane default sized to their own capital instead of the
smallest possible trade. It never runs again and never overrides a value the
account already has (manual or previously agent-set) -- see accounts.activate().
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Optional

from .config import ALLOWED_POSITION_PCT

# (balance floor usd, trade_margin_pct) -- first matching floor wins, highest first.
# Smaller accounts get a larger % of balance per trade so a position still
# clears venue minimums comfortably; larger accounts get a smaller % so
# dollar risk per trade does not scale up linearly with capital.
_TIERS = (
    (Decimal(8000), 1),
    (Decimal(3000), 2),
    (Decimal(1000), 5),
    (Decimal(400), 10),
    (Decimal(150), 15),
    (Decimal(0), 20),
)
assert all(pct in ALLOWED_POSITION_PCT for _, pct in _TIERS)
assert [f for f, _ in _TIERS] == sorted((f for f, _ in _TIERS), reverse=True)


def recommend_trade_margin_pct(balance_equity) -> Optional[int]:
    """Risk-tiered default % of balance per trade for a freshly verified
    balance. Returns None (caller must then leave sizing untouched) when the
    balance is missing or not a finite, non-negative number -- this function
    never guesses and never raises."""
    if balance_equity is None:
        return None
    try:
        equity = Decimal(str(balance_equity))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not equity.is_finite() or equity < 0:
        return None
    for floor, pct in _TIERS:
        if equity >= floor:
            return pct
    return None  # unreachable: the lowest floor is 0
