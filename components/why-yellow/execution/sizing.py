"""Execution-side sizing safety (not customer sizing design). Distinguishes
position notional, required margin, leverage, available balance and quantity.
Every number is Decimal; rounding is always DOWN on quantity."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from typing import Optional
from .metadata import MarketFilters
from .types import VenueAccount, D


class SizingError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class ExposureLimits:
    max_notional_per_order: Decimal
    max_total_notional: Decimal
    max_open_positions: int
    margin_buffer: Decimal = Decimal("1.00")   # required_margin * buffer must fit in available balance


@dataclass(frozen=True)
class SizedOrder:
    qty: Decimal
    notional: Decimal
    required_margin: Decimal
    leverage: int
    ref_price: Decimal


def size_order(notional_usd, ref_price, leverage: int, filters: MarketFilters, account: VenueAccount,
               limits: ExposureLimits, open_notional: Decimal = Decimal(0), open_positions: int = 0) -> SizedOrder:
    notional_req = D(notional_usd)
    px = D(ref_price)
    for name, v in (("notional", notional_req), ("ref_price", px)):
        if not v.is_finite() or v <= 0:
            raise SizingError(f"invalid_{name}", str(v))
    if not isinstance(leverage, int) or leverage < 1:
        raise SizingError("invalid_leverage", repr(leverage))
    if Decimal(leverage) > filters.max_leverage:
        raise SizingError("leverage_above_market_max", f"{leverage} > {filters.max_leverage}")
    if not filters.tradeable:
        raise SizingError("market_not_trading", filters.status)
    if open_positions >= limits.max_open_positions:
        raise SizingError("max_open_positions", f"{open_positions} >= {limits.max_open_positions}")
    if notional_req > limits.max_notional_per_order:
        raise SizingError("above_max_notional_per_order", f"{notional_req} > {limits.max_notional_per_order}")
    if open_notional + notional_req > limits.max_total_notional:
        raise SizingError("above_max_total_notional", f"{open_notional}+{notional_req} > {limits.max_total_notional}")
    raw_qty = notional_req / (px * filters.contract_multiplier)
    qty = filters.round_qty_down(raw_qty)          # never rounds exposure UP
    if qty < filters.min_qty:
        raise SizingError("below_min_qty_after_rounding", f"{qty} < {filters.min_qty}")
    if filters.max_qty is not None and qty > filters.max_qty:
        raise SizingError("above_max_qty", f"{qty} > {filters.max_qty}")
    notional = qty * px * filters.contract_multiplier
    if notional < filters.min_notional:
        raise SizingError("below_min_notional_after_rounding", f"{notional} < {filters.min_notional}")
    if notional > notional_req:
        raise SizingError("rounding_increased_exposure", f"{notional} > {notional_req}")   # defensive; cannot happen with ROUND_DOWN
    required_margin = (notional / Decimal(leverage)).quantize(Decimal("0.00000001"), rounding=ROUND_UP)
    if not account.available_balance.is_finite() or account.available_balance <= 0:
        raise SizingError("no_available_balance", str(account.available_balance))
    if required_margin * limits.margin_buffer > account.available_balance:
        raise SizingError("insufficient_available_balance", f"need {required_margin} x {limits.margin_buffer}, have {account.available_balance}")
    return SizedOrder(qty=qty, notional=notional, required_margin=required_margin, leverage=leverage, ref_price=px)


# Venue rejections on 2026-10-06 required 1.05076--1.05210 times
# qty * signal_price / leverage. This is an observed envelope, not a
# documented Yellow formula. Reserve 6% plus the existing 1% price cushion.
YELLOW_MARGIN_RESERVE = Decimal("1.06")
PRICE_CUSHION = Decimal("1.01")

# Additional conservative ENTRY SIZING for the original incident account.
# Every account's actual stop is independently capped by execution.loss_stop;
# this older sizing rule can only reduce exposure further, never waive that cap.
HARD_LOSS_LIMIT_ACCOUNT = "yellow:8f3efa87-f7c0-4c21-ba1e-46b207613f96"
HARD_MAX_PLANNED_LOSS_USD = Decimal("2.00")


def entry_loss_limit(account_id, configured=None):
    hard = HARD_MAX_PLANNED_LOSS_USD if account_id == HARD_LOSS_LIMIT_ACCOUNT else None
    if configured is None:
        return hard
    configured = D(configured)
    if not configured.is_finite() or configured <= 0:
        raise SizingError('invalid_loss_budget_inputs')
    return min(hard, configured) if hard is not None else configured


def loss_budget_notional(notional_usd, ref_price, stop_pct, target_pct, direction, filters,
                         max_loss_usd, taker_fee):
    """Cap NEW entry quantity by stop loss, round-trip fees and execution reserve.

    The 1% price cushion covers reference-to-fill movement; a separate 1%
    notional reserve covers adverse exit execution. These are estimates, not
    a guarantee against market gaps or exchange failure. Never round size up.
    Existing positions and protective order prices are not changed here.
    """
    values = [D(v) for v in (notional_usd, ref_price, stop_pct, target_pct, max_loss_usd,
                             taker_fee, filters.tick, filters.step,
                             filters.contract_multiplier)]
    requested, px, pct, tp_pct, budget, fee, tick, step, multiplier = values
    if (any(not v.is_finite() for v in values) or
            any(v <= 0 for v in (requested, px, pct, tp_pct, budget, tick, step, multiplier)) or
            max(pct, tp_pct) >= 100 or not Decimal(0) <= fee <= Decimal('.01') or
            direction not in ('long', 'short')):
        raise SizingError('invalid_loss_budget_inputs')
    # Keep the public fee floor even if metadata reports a lower/zero fee.
    fee = max(fee, Decimal('.0004'))
    entry = px * PRICE_CUSHION
    stop = filters.round_price(entry * (1 - pct / 100 if direction == 'long'
                                        else 1 + pct / 100), up=direction == 'short')
    if stop <= 0:
        raise SizingError('invalid_loss_budget_stop')
    exit_reserve = max(entry, stop) * (PRICE_CUSHION - 1)
    fees_per_unit = (entry + stop + exit_reserve) * fee
    # A full tick also bounds outward rounding at intermediate fill prices.
    stop_distance = entry * pct / 100 + tick
    risk_per_qty = (stop_distance + exit_reserve + fees_per_unit) * multiplier
    qty = filters.round_qty_down(min(requested / (px * multiplier), budget / risk_per_qty))
    # Decimal division/rounding must never allow a value above the budget.
    if qty * risk_per_qty > budget:
        qty -= step
    # Ensure protection can also meet venue minimums after a 1% entry move.
    low_entry = px / PRICE_CUSHION
    low_stop = filters.round_price(low_entry * (1 - pct / 100 if direction == 'long'
                                               else 1 + pct / 100), up=direction == 'short')
    low_target = filters.round_price(low_entry * (1 + tp_pct / 100 if direction == 'long'
                                                 else 1 - tp_pct / 100), up=direction == 'long')
    minimum_px = min(px, low_entry, low_stop, low_target)
    if qty < filters.min_qty or qty <= 0 or qty * minimum_px * multiplier < filters.min_notional:
        raise SizingError('loss_budget_below_coin_minimum')
    return qty * px * multiplier, {
        'max_planned_loss_usd': str(budget),
        'planned_loss_with_reserve_usd': str(qty * risk_per_qty),
        'stop_loss_usd': str(qty * stop_distance * multiplier),
        'fee_reserve_usd': str(qty * fees_per_unit * multiplier),
        'exit_slippage_reserve_usd': str(qty * exit_reserve * multiplier),
        'entry_price_cushion': str(PRICE_CUSHION),
    }


def percentage_order_reservation(margin, notional, fee):
    return (D(margin) * YELLOW_MARGIN_RESERVE + D(notional) * 2 * D(fee)) * PRICE_CUSHION


def margin_percentage_notional(available, percentage, leverage, taker_fee, minimum_notional, *, confirmed_taker_fee=None):
    """Budget venue margin reserve plus entry/exit fees and 1% price movement.
    Uses the public 4-bps floor unless an account-confirmed fee is supplied.
    """
    if type(percentage) is not int or not 1 <= percentage <= 100:
        raise SizingError("invalid_trade_margin_pct")
    balance, fee, minimum = D(available), D(taker_fee), D(minimum_notional)
    if not balance.is_finite() or balance <= 0:
        raise SizingError("no_available_balance")
    if type(leverage) is not int or leverage < 1:
        raise SizingError("invalid_leverage")
    if not fee.is_finite() or fee < 0 or not minimum.is_finite() or minimum <= 0:
        raise SizingError("invalid_sizing_inputs")
    if confirmed_taker_fee is None:
        fee = max(fee, Decimal("0.0004"))
    else:
        fee = D(confirmed_taker_fee)
        if not fee.is_finite() or not Decimal(0) <= fee <= Decimal("0.01"):
            raise SizingError("invalid_confirmed_taker_fee")
    budget = balance * Decimal(percentage) / 100
    notional = budget / (YELLOW_MARGIN_RESERVE / leverage + 2 * fee) / PRICE_CUSHION
    if notional < minimum:
        raise SizingError("trade_budget_below_coin_minimum")
    return notional
