"""Deterministic in-memory venue for tests. It never opens a socket. Scripted
behaviours reproduce every failure mode the audit found or the fix brief lists:
accepted, rejected, accepted-but-response-lost, rejected-but-connection-fails,
partial/delayed/duplicate fills, manual close, cancel ack / stays-open / unknown,
429/500/502/503, disconnect/reconnect, cursor pagination."""
from __future__ import annotations
import itertools
from collections import deque
from decimal import Decimal
from typing import Deque, Dict, List, Optional, Tuple
from .transport import TransportAmbiguous, VenueRejected, VenueUnavailable
from .types import (Capabilities, OrderAck, OrderRequest, VenueAccount, VenueOrder, VenuePosition, VenueTrade,
                    VENUE_CANCELLED, VENUE_FILLED, VENUE_OPEN, VENUE_REJECTED)


class MockYellowAdapter:
    REAL_ORDER_ENDPOINT = None   # there is none; nothing here can reach a venue

    def __init__(self, page_size: int = 2, clock=None, capabilities: Optional[Capabilities] = None,
                 dedupe_client_order_id: bool = False):
        self.page_size = int(page_size)
        self.clock = clock or (lambda: 0.0)
        self.capabilities = capabilities or Capabilities(venue_stop_orders=False, client_order_id_dedup=dedupe_client_order_id)
        self.dedupe = dedupe_client_order_id
        self.orders: Dict[str, dict] = {}                     # venue_order_id -> record
        self.positions: Dict[Tuple[str, str, str], dict] = {}  # (account, market, direction) -> {qty, entry}
        self.trades: List[VenueTrade] = []
        self.accounts: Dict[str, VenueAccount] = {}
        self.place_script: Deque[str] = deque()
        self.cancel_script: Deque[str] = deque()
        self.read_script: Deque[str] = deque()
        self.connected = True
        self.place_calls = 0
        self.cancel_calls = 0
        self.read_calls = 0
        self.log: List[tuple] = []
        self._ids = itertools.count(1)
        self._tids = itertools.count(1)
        self.real_orders_placed = 0

    # ------------------------------------------------------------- scripting
    def script_place(self, *behaviours: str): self.place_script.extend(behaviours)
    def script_cancel(self, *behaviours: str): self.cancel_script.extend(behaviours)
    def script_read(self, *behaviours: str): self.read_script.extend(behaviours)
    def set_account(self, account_id: str, available: str, equity: str):
        self.accounts[account_id] = VenueAccount(Decimal(available), Decimal(equity))
    def disconnect(self): self.connected = False
    def reconnect(self): self.connected = True

    def _pos(self, account_id, market, direction) -> dict:
        return self.positions.setdefault((account_id, market, direction), {"qty": Decimal(0), "entry": Decimal(0)})

    def _guard_read(self):
        self.read_calls += 1
        if not self.connected:
            raise VenueUnavailable(0, "disconnected")
        if self.read_script:
            b = self.read_script.popleft()
            if b.startswith("http_"):
                raise VenueUnavailable(int(b[5:]), "scripted")
            if b == "timeout":
                raise TransportAmbiguous("scripted read timeout")

    # ------------------------------------------------------------- orders
    def _create(self, req: OrderRequest) -> dict:
        if self.dedupe and req.client_order_id:
            for o in self.orders.values():
                if o["client_order_id"] == req.client_order_id:
                    return o
        vid = f"v-{next(self._ids)}"
        rec = dict(venue_order_id=vid, client_order_id=req.client_order_id, account_id=req.account_id, market=req.market,
                   side=req.side, direction=req.direction, order_type=req.order_type, qty=Decimal(req.qty), filled_qty=Decimal(0),
                   price=req.price, state=VENUE_OPEN, reduce_only=req.reduce_only, ts=self.clock(),
                   trigger_price=req.trigger_price, trigger_type=req.trigger_type)
        self.orders[vid] = rec
        return rec

    def place_order(self, req: OrderRequest) -> OrderAck:
        self.place_calls += 1
        self.log.append(("place", req.client_order_id, req.market, req.side, str(req.qty)))
        if not self.connected:
            raise TransportAmbiguous("disconnected during submit")
        b = self.place_script.popleft() if self.place_script else "accept"
        if b == "http_429":
            raise VenueUnavailable(429, "rate limited")
        if b in ("http_500", "http_502", "http_503", "http_504"):
            # edge/server error: the request may or may not have been processed
            raise TransportAmbiguous(f"{b[5:]} on order submit")
        if b == "http_503_accepted":
            self._create(req)
            raise TransportAmbiguous("503 after venue accepted")
        if b == "timeout_before_accept" or b == "reject_conn_fail":
            raise TransportAmbiguous("connection failed before acceptance")
        if b in ("accept_lost", "timeout_after_accept"):
            self._create(req)
            raise TransportAmbiguous("response lost after acceptance")
        if b == "reject":
            raise VenueRejected("insufficient_balance", "scripted reject")
        if b.startswith("reject:"):
            raise VenueRejected(b.split(":", 1)[1], "scripted reject")
        if b == "accept_filled":
            rec = self._create(req)
            self.fill(rec["venue_order_id"], rec["qty"], req.price or Decimal("100"))
            return OrderAck(rec["venue_order_id"])
        if b == "accept":
            return OrderAck(self._create(req)["venue_order_id"])
        raise ValueError(f"unknown place behaviour {b}")

    def cancel_order(self, account_id: str, market: str, venue_order_id: str) -> None:
        self.cancel_calls += 1
        self.log.append(("cancel", venue_order_id))
        if not self.connected:
            raise TransportAmbiguous("disconnected during cancel")
        b = self.cancel_script.popleft() if self.cancel_script else "ack"
        rec = self.orders.get(venue_order_id)
        if b == "http_503":
            raise TransportAmbiguous("503 on cancel")
        if rec is None or rec["account_id"] != account_id:
            raise VenueRejected("order_not_found", venue_order_id, 404)
        if b == "ambiguous":
            if rec["state"] == VENUE_OPEN:
                rec["state"] = VENUE_CANCELLED
            raise TransportAmbiguous("response lost after cancel")
        if b == "ambiguous_no_cancel":
            raise TransportAmbiguous("connection reset before cancel")
        if b == "ack_stays_open":       # documented Yellow bug: 200 but order stays in wait
            return None
        if b == "ack":
            if rec["state"] == VENUE_FILLED:
                raise VenueRejected("order_not_found", "already filled", 404)
            if rec["state"] == VENUE_OPEN:
                rec["state"] = VENUE_CANCELLED
            return None
        raise ValueError(f"unknown cancel behaviour {b}")

    # ------------------------------------------------------------- venue-side happenings
    def fill(self, order_ref: str, qty, price, trade_id: Optional[str] = None, fee: str = "0", fee_currency: str = "USDT") -> VenueTrade:
        rec = self.orders.get(order_ref) or next((o for o in self.orders.values() if o["client_order_id"] == order_ref), None)
        if rec is None:
            raise KeyError(order_ref)
        qty, price = Decimal(str(qty)), Decimal(str(price))
        if rec["state"] not in (VENUE_OPEN,):
            raise RuntimeError(f"cannot fill order in state {rec['state']}")
        remaining = rec["qty"] - rec["filled_qty"]
        qty = min(qty, remaining)
        rec["filled_qty"] += qty
        if rec["filled_qty"] >= rec["qty"]:
            rec["state"] = VENUE_FILLED
        elif rec["order_type"] == "market" and rec.get("ioc_done"):
            rec["state"] = VENUE_CANCELLED
        p = self._pos(rec["account_id"], rec["market"], rec["direction"])
        if rec["reduce_only"]:
            p["qty"] = max(Decimal(0), p["qty"] - qty)
        else:
            new_qty = p["qty"] + qty
            p["entry"] = ((p["entry"] * p["qty"]) + price * qty) / new_qty if new_qty else Decimal(0)
            p["qty"] = new_qty
        t = VenueTrade(trade_id or f"t-{next(self._tids)}", rec["venue_order_id"], rec["client_order_id"], rec["market"],
                       qty, price, Decimal(fee), fee_currency, self.clock())
        self.trades.append(t)
        return t

    def expire_ioc(self, order_ref: str) -> None:
        """Remaining IOC quantity cancelled by the venue."""
        rec = self.orders.get(order_ref) or next(o for o in self.orders.values() if o["client_order_id"] == order_ref)
        if rec["state"] == VENUE_OPEN:
            rec["state"] = VENUE_CANCELLED

    def duplicate_last_trade(self) -> VenueTrade:
        t = self.trades[-1]
        self.trades.append(t)
        return t

    def manual_close(self, account_id: str, market: str, direction: str) -> None:
        p = self._pos(account_id, market, direction)
        vid = f"v-manual-{next(self._ids)}"
        qty = p["qty"]
        self.orders[vid] = dict(venue_order_id=vid, client_order_id=None, account_id=account_id, market=market,
                                side="sell" if direction == "long" else "buy", direction=direction, order_type="market",
                                qty=qty, filled_qty=qty, price=None, state=VENUE_FILLED, reduce_only=True, ts=self.clock())
        self.trades.append(VenueTrade(f"t-{next(self._tids)}", vid, None, market, qty, p["entry"] or Decimal(1), Decimal(0), "USDT", self.clock()))
        p["qty"] = Decimal(0)

    def manual_open(self, account_id: str, market: str, direction: str, qty, price) -> None:
        p = self._pos(account_id, market, direction)
        p["qty"] += Decimal(str(qty)); p["entry"] = Decimal(str(price))

    def manual_cancel(self, order_ref: str) -> None:
        rec = self.orders.get(order_ref) or next(o for o in self.orders.values() if o["client_order_id"] == order_ref)
        if rec["state"] == VENUE_OPEN:
            rec["state"] = VENUE_CANCELLED

    # ------------------------------------------------------------- reads (paginated)
    def _page(self, items: list, cursor: Optional[str]):
        start = int(cursor) if cursor else 0
        page = items[start:start + self.page_size]
        nxt = str(start + self.page_size) if start + self.page_size < len(items) else None
        return page, nxt

    @staticmethod
    def _vo(o: dict) -> VenueOrder:
        return VenueOrder(o["venue_order_id"], o["client_order_id"], o["market"], o["side"], o["direction"], o["order_type"],
                          o["qty"], o["filled_qty"], o["price"], o["state"], o["reduce_only"],
                          o.get("trigger_price"), o.get("trigger_type"))

    def get_open_orders(self, account_id: str, market: Optional[str] = None, cursor: Optional[str] = None):
        self._guard_read()
        items = [self._vo(o) for o in self.orders.values() if o["account_id"] == account_id and o["state"] == VENUE_OPEN and (market is None or o["market"] == market)]
        return self._page(items, cursor)

    def get_orders(self, account_id: str, market: Optional[str] = None, since: Optional[float] = None, cursor: Optional[str] = None):
        self._guard_read()
        items = [self._vo(o) for o in self.orders.values() if o["account_id"] == account_id and (market is None or o["market"] == market) and (since is None or o["ts"] >= since)]
        return self._page(items, cursor)

    def get_positions(self, account_id: str) -> List[VenuePosition]:
        self._guard_read()
        return [VenuePosition(m, d, p["qty"], p["entry"]) for (a, m, d), p in self.positions.items() if a == account_id and p["qty"] > 0]

    def get_trades(self, account_id: str, market: Optional[str] = None, since: Optional[float] = None, cursor: Optional[str] = None):
        self._guard_read()
        items = [t for t in self.trades if (market is None or t.market == market) and (since is None or t.ts >= since)
                 and (self.orders.get(t.venue_order_id, {}).get("account_id", account_id) == account_id)]
        return self._page(items, cursor)

    def get_account(self, account_id: str) -> VenueAccount:
        self._guard_read()
        return self.accounts.get(account_id, VenueAccount(Decimal("100000"), Decimal("100000")))

    # ------------------------------------------------------------- inspection
    def orders_for_coid(self, coid: str) -> List[dict]:
        return [o for o in self.orders.values() if o["client_order_id"] == coid]

    def open_orders_count(self, account_id: str) -> int:
        return sum(1 for o in self.orders.values() if o["account_id"] == account_id and o["state"] == VENUE_OPEN)

    def position_qty(self, account_id: str, market: str, direction: str) -> Decimal:
        return self._pos(account_id, market, direction)["qty"]
