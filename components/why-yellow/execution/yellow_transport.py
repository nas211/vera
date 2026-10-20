"""Thin Transport implementation over yellow_exec.YellowClient.

Safety properties preserved from the adapter: every trading call is still gated by
YELLOW_ARMED=1 + key file (YellowClient._guard) and by the entries switch mirror for
entries. Order POSTs are never retried (yellow_exec._req idempotent=False raises
TransportAmbiguous). Venue field/state names below are taken from the public Yellow
docs (2026-09-22); items marked UNVERIFIED must be confirmed with Yellow before
production use (audit YELLOW_OPEN_QUESTIONS.md Q-03, Q-07, Q-09)."""
from __future__ import annotations
from decimal import Decimal
from typing import List, Optional
from .transport import TransportAmbiguous, VenueRejected, VenueUnavailable
from .types import (Capabilities, OrderAck, OrderRequest, VenueAccount, VenueOrder, VenuePosition, VenueTrade,
                    VENUE_CANCELLED, VENUE_FILLED, VENUE_OPEN, VENUE_REJECTED, D)

# docs (websocket-api order_update): pending | wait | filled | done | canceled
_STATE = {"pending": VENUE_OPEN, "wait": VENUE_OPEN, "open": VENUE_OPEN, "new": VENUE_OPEN,
          "trigger_wait": VENUE_OPEN, "trigger_triggered": VENUE_OPEN,
          "filled": VENUE_FILLED, "canceled": VENUE_CANCELLED, "cancelled": VENUE_CANCELLED,
          "trigger_canceled": VENUE_CANCELLED,
          "rejected": VENUE_REJECTED, "trigger_rejected": VENUE_REJECTED}


def _dec(v, default="0") -> Decimal:
    try:
        return D(v if v not in (None, "") else default)
    except Exception:
        return Decimal(default)


def normalise_order(o: dict) -> VenueOrder:
    raw = str(o.get("state") or o.get("status") or "").lower()
    filled = _dec(o.get("fill_amount", o.get("filled_amount", o.get("executed_amount", o.get("filled", "0")))))
    if raw == "done":            # UNVERIFIED: 'done' may mean fully filled or finished-cancelled
        state = VENUE_FILLED if filled > 0 else VENUE_CANCELLED
    else:
        state = _STATE.get(raw, "unknown")
    typ = str(o.get("type", ""))
    trigger_type = o.get("trigger_type")
    if typ in ("stop_loss", "stop_market"):
        typ, trigger_type = "trigger_market", "stop_loss"
    elif typ == "stop_limit":
        typ, trigger_type = "trigger_limit", "stop_loss"
    elif typ in ("take_profit", "take_market"):
        typ, trigger_type = "trigger_market", "take_profit"
    elif typ == "take_limit":
        typ, trigger_type = "trigger_limit", "take_profit"
    return VenueOrder(venue_order_id=str(o.get("order_id") or o.get("order_uuid") or ""), client_order_id=o.get("client_order_id"),
                      market=str(o.get("market", "")), side=str(o.get("side", "")), direction=str(o.get("direction", "")),
                      order_type=typ, qty=_dec(o.get("origin_amount", o.get("amount"))), filled_qty=filled,
                      price=(_dec(o["price"]) if o.get("price") and _dec(o["price"]) > 0 else None),
                      state=state, reduce_only=o.get("reduce_only") in (True, "true", "True", 1),
                      trigger_price=(_dec(o["trigger_price"]) if o.get("trigger_price") else None), trigger_type=trigger_type)


def normalise_trade(t: dict, account_id: str) -> VenueTrade:
    tid = str(t.get("id") or t.get("trade_id") or t.get("trade_uuid") or f"{t.get('order_uuid')}|{t.get('created_at')}|{t.get('amount')}|{t.get('price')}")
    return VenueTrade(trade_id=tid, venue_order_id=str(t.get("order_uuid") or t.get("order_id") or ""), client_order_id=t.get("client_order_id"),
                      market=str(t.get("market", "")), qty=_dec(t.get("amount")), price=_dec(t.get("price")), fee=_dec(t.get("fee")),
                      fee_currency=str(t.get("fee_currency") or t.get("fee_asset") or "USDT"), ts=float(t.get("ts") or t.get("created_at_ms") or 0))


class YellowTransport:
    capabilities = Capabilities(venue_stop_orders=True, client_order_id_dedup=False, conditional_take_profit=True)

    def __init__(self, client):
        self.c = client

    def _wrap(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except TransportAmbiguous:
            raise
        except Exception as e:  # YellowError / RuntimeError from the adapter
            status = getattr(e, "status", None)
            code = getattr(e, "code", "error")
            if status in (429,):
                raise VenueUnavailable(429, str(e)) from e
            if status is not None and status >= 500:
                raise VenueUnavailable(status, str(e)) from e
            if status is not None:
                raise VenueRejected(str(code), getattr(e, "message", str(e)), status) from e
            raise TransportAmbiguous(str(e)) from e

    def place_order(self, req: OrderRequest) -> OrderAck:
        price = None if req.price is None else str(req.price)
        trigger = {}
        if req.order_type.startswith("trigger"):
            trigger = dict(trigger_price=None if req.trigger_price is None else str(req.trigger_price),
                           trigger_type=req.trigger_type)
        try:
            vid = self.c.place(req.market, req.side == "buy", req.direction, str(req.qty), typ=req.order_type, price=price,
                               tif=req.time_in_force, reduce_only=req.reduce_only, leverage=req.leverage, coid=req.client_order_id,
                               ref_price=None, validate=False, **trigger)   # validated by the engine already
        except TransportAmbiguous:
            raise
        except Exception as e:
            status = getattr(e, "status", None)
            if status == 429:
                raise VenueUnavailable(429, str(e)) from e
            if status is not None and status < 500:
                raise VenueRejected(str(getattr(e, "code", "error")), getattr(e, "message", str(e)), status) from e
            raise TransportAmbiguous(str(e)) from e
        return OrderAck(str(vid))

    def cancel_order(self, account_id: str, market: str, venue_order_id: str) -> None:
        try:
            self.c._req("DELETE", "/perpetual/order", {"app_session_id": self.c.asid, "market": market, "order_uuid": venue_order_id}, idempotent=True)
        except TransportAmbiguous:
            raise
        except Exception as e:
            status = getattr(e, "status", None)
            if status == 404 or "not found" in str(e).lower():
                raise VenueRejected("order_not_found", str(e), 404) from e
            if status is not None and status < 500:
                raise VenueRejected(str(getattr(e, "code", "error")), str(e), status) from e
            raise TransportAmbiguous(str(e)) from e

    def get_open_orders(self, account_id: str, market: Optional[str] = None, cursor: Optional[str] = None):
        j = self._wrap(self.c.open_orders_page, market, cursor)
        if j.get("degraded") or j.get("stale"):
            raise VenueUnavailable(503, "Yellow order snapshot is degraded or stale")
        return [normalise_order(o) for o in j.get("orders", [])], (j.get("next_cursor") or None) if j.get("has_more") else None

    def get_orders(self, account_id: str, market: Optional[str] = None, since: Optional[float] = None, cursor: Optional[str] = None):
        j = self._wrap(self.c.orders_page, market, cursor)
        if j.get("degraded") or j.get("stale"):
            raise VenueUnavailable(503, "Yellow order snapshot is degraded or stale")
        return [normalise_order(o) for o in j.get("orders", [])], (j.get("next_cursor") or None) if j.get("has_more") else None

    def get_positions(self, account_id: str) -> List[VenuePosition]:
        out = []
        for p in self._wrap(self.c.positions) or []:
            qty = _dec(p.get("amount", p.get("size", p.get("qty"))))
            if qty > 0:
                out.append(VenuePosition(str(p.get("market", "")), str(p.get("direction", "")), qty, _dec(p.get("entry_price", p.get("avg_entry_price", "0")))))
        return out

    def get_trades(self, account_id: str, market: Optional[str] = None, since: Optional[float] = None, cursor: Optional[str] = None):
        j = self._wrap(self.c.trades_page, market, cursor)
        return [normalise_trade(t, account_id) for t in j.get("trades", [])], (j.get("next_cursor") or None) if j.get("has_more") else None

    def get_account(self, account_id: str) -> VenueAccount:
        a = self._wrap(self.c.account)
        return VenueAccount(_dec(a.get("available_balance")), _dec(a.get("total_account_equity")))
