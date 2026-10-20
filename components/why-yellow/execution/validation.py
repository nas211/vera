"""Strict local validation. Everything must pass here before an OrderRequest may
reach any transport. Decimal only; floats are converted via repr and non-finite
values are rejected explicitly."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Optional
from .metadata import MarketFilters, MetadataSource, UnknownMarket
from .types import DIRECTIONS, ORDER_TYPES, SIDES, TIFS, OrderRequest, D


class ValidationError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _finite_positive(x, name: str) -> Decimal:
    try:
        d = D(x)
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValidationError(f"invalid_{name}", repr(x)) from e
    if not d.is_finite():
        raise ValidationError(f"non_finite_{name}", str(d))
    if d <= 0:
        raise ValidationError(f"non_positive_{name}", str(d))
    return d


def validate_order(req: OrderRequest, filters: MarketFilters, ref_price: Optional[Decimal] = None,
                   max_leverage_policy: Optional[int] = None) -> None:
    if req.market != filters.symbol:
        raise ValidationError("symbol_mismatch", f"{req.market} vs {filters.symbol}")
    if not filters.tradeable:
        raise ValidationError("market_not_trading", filters.status)
    if req.side not in SIDES:
        raise ValidationError("invalid_side", str(req.side))
    if req.direction not in DIRECTIONS:
        raise ValidationError("invalid_direction", str(req.direction))
    if req.order_type not in ORDER_TYPES:
        raise ValidationError("invalid_order_type", str(req.order_type))
    if req.time_in_force not in TIFS:
        raise ValidationError("invalid_time_in_force", str(req.time_in_force))
    # side/direction consistency: entries buy-long / sell-short, reduce-only the inverse
    expect_buy = (req.direction == "long") != req.reduce_only
    if (req.side == "buy") != expect_buy:
        raise ValidationError("side_direction_inconsistent", f"{req.side}/{req.direction}/reduce_only={req.reduce_only}")
    if not req.client_order_id or not isinstance(req.client_order_id, str):
        raise ValidationError("missing_client_order_id")
    qty = _finite_positive(req.qty, "qty")
    if qty < filters.min_qty:
        raise ValidationError("below_min_qty", f"{qty} < {filters.min_qty}")
    if not filters.on_step(qty):
        raise ValidationError("qty_off_step", f"{qty} step {filters.step}")
    if filters.max_qty is not None and qty > filters.max_qty:
        raise ValidationError("above_max_qty", f"{qty} > {filters.max_qty}")
    try:
        lev = int(req.leverage)
    except (TypeError, ValueError) as e:
        raise ValidationError("invalid_leverage", repr(req.leverage)) from e
    if lev < 1 or Decimal(lev) > filters.max_leverage:
        raise ValidationError("leverage_out_of_range", f"{lev} not in [1, {filters.max_leverage}]")
    if max_leverage_policy is not None and lev > max_leverage_policy:
        raise ValidationError("leverage_above_policy", f"{lev} > {max_leverage_policy}")
    price: Optional[Decimal] = None
    if req.order_type.endswith("limit") or req.order_type == "post_only":
        if req.price is None:
            raise ValidationError("limit_price_required")
        price = _finite_positive(req.price, "price")
        if not filters.on_tick(price):
            raise ValidationError("price_off_tick", f"{price} tick {filters.tick}")
    elif req.price is not None:
        raise ValidationError("price_not_allowed_for_market_order")
    if req.order_type.startswith("trigger"):
        if req.trigger_price is None:
            raise ValidationError("trigger_price_required")
        tp = _finite_positive(req.trigger_price, "trigger_price")
        if not filters.on_tick(tp):
            raise ValidationError("trigger_price_off_tick")
        if req.trigger_type not in (None, "stop_loss", "take_profit"):
            raise ValidationError("invalid_trigger_type")
    elif req.trigger_price is not None or req.trigger_type is not None:
        raise ValidationError("trigger_fields_not_allowed")
    notional_ref = price if price is not None else ref_price
    if notional_ref is None:
        raise ValidationError("reference_price_required", "market order needs a reference price for min-notional")
    notional_ref = _finite_positive(notional_ref, "reference_price")
    notional = qty * notional_ref * filters.contract_multiplier
    if notional < filters.min_notional:
        raise ValidationError("below_min_notional", f"{notional} < {filters.min_notional}")


def validate_market_known(metadata: MetadataSource, market: str) -> MarketFilters:
    try:
        return metadata.filters(market)
    except UnknownMarket as e:
        raise ValidationError("unknown_market", market) from e
