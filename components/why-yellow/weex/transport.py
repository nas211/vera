"""Adapter from official WEEX V3 responses to the existing execution core."""
from __future__ import annotations

from decimal import Decimal

from execution.metadata import MarketFilters, UnknownMarket
from execution.transport import TransportAmbiguous, VenueRejected, VenueUnavailable
from execution.types import (Capabilities, OrderAck, VenueAccount, VenueOrder, VenuePosition,
                             VenueTrade, VENUE_CANCELLED, VENUE_FILLED, VENUE_OPEN, VENUE_REJECTED)
from .client import WeexError

D = lambda v: Decimal(str(v or 0))
STATES = {"NEW": VENUE_OPEN, "PARTIALLY_FILLED": VENUE_OPEN, "FILLED": VENUE_FILLED,
          "CANCELED": VENUE_CANCELLED, "CANCELLED": VENUE_CANCELLED, "REJECTED": VENUE_REJECTED,
          "EXPIRED": VENUE_CANCELLED}


def external_symbol(market: str) -> str:
    return str(market).upper().replace("-PERP", "")


def internal_symbol(symbol: str) -> str:
    s = str(symbol).upper()
    return s if s.endswith("-PERP") else s + "-PERP"


def normalise_order(o) -> VenueOrder:
    return VenueOrder(str(o.get("orderId", "")), o.get("clientOrderId"), internal_symbol(o.get("symbol", "")),
                      str(o.get("side", "")).lower(), str(o.get("positionSide", "")).lower(),
                      str(o.get("type", "")).lower(), D(o.get("origQty")), D(o.get("executedQty")),
                      (D(o.get("price")) if o.get("price") not in (None, "", "0") else None),
                      STATES.get(str(o.get("status", "")).upper(), VENUE_OPEN), bool(o.get("reduceOnly", False)))


class WeexMetadata:
    def __init__(self, client):
        self.c, self._cache = client, None

    def _load(self):
        if self._cache is None:
            j = self.c.exchange_info() or {}
            self._cache = {internal_symbol(x["symbol"]): x for x in j.get("symbols", []) if x.get("contractType") == "PERPETUAL"}
        return self._cache

    def known_markets(self):
        return set(self._load())

    def filters(self, market):
        try: x = self._load()[market]
        except KeyError as e: raise UnknownMarket(market) from e
        qp, pp = int(x.get("quantityPrecision", 8)), int(x.get("pricePrecision", 8))
        step, tick = Decimal(1).scaleb(-qp), Decimal(1).scaleb(-pp)
        return MarketFilters(market, tick, step, D(x.get("minOrderSize", step)), Decimal(0), D(x.get("maxLeverage", 1)),
                             "TRADING", max_qty=D(x.get("maxOrderSize")) if x.get("maxOrderSize") else None,
                             contract_multiplier=D(x.get("contractVal", 1)), maker_fee=D(x.get("apiMakerFeeRate", x.get("makerFeeRate", 0))),
                             taker_fee=D(x.get("apiTakerFeeRate", x.get("takerFeeRate", 0))))


class WeexTransport:
    capabilities = Capabilities(venue_stop_orders=False, client_order_id_dedup=True)

    def __init__(self, client): self.c = client

    @staticmethod
    def _raise(e, write=False):
        if isinstance(e, WeexError):
            if e.ambiguous or (write and e.status >= 500): raise TransportAmbiguous(str(e)) from e
            if e.status in (0, 429) or e.status >= 500: raise VenueUnavailable(e.status, e.message) from e
            raise VenueRejected(e.code, e.message, e.status) from e
        raise TransportAmbiguous("weex_transport_error") from e

    def place_order(self, req):
        try:
            j = self.c.place_order(symbol=external_symbol(req.market), side=req.side.upper(), position_side=req.direction.upper(),
                                   order_type=req.order_type.upper(), quantity=req.qty, client_id=req.client_order_id,
                                   time_in_force=req.time_in_force.upper(), price=req.price, reduce_only=req.reduce_only,
                                   leverage=req.leverage)
            return OrderAck(str(j["orderId"]))
        except Exception as e: self._raise(e, True)

    def cancel_order(self, account_id, market, venue_order_id):
        try: self.c.cancel_order(order_id=venue_order_id)
        except Exception as e: self._raise(e, True)

    def _page(self, rows, cursor):
        rows = rows if isinstance(rows, list) else []
        page = int(cursor or 0)
        return [normalise_order(x) for x in rows], (str(page + 1) if len(rows) >= 100 else None)

    def get_open_orders(self, account_id, market=None, cursor=None):
        try: return self._page(self.c.open_orders(external_symbol(market) if market else None, 100, int(cursor or 0)), cursor)
        except Exception as e: self._raise(e)

    def get_orders(self, account_id, market=None, since=None, cursor=None):
        try:
            ms = int(since * 1000) if since else None
            return self._page(self.c.order_history(external_symbol(market) if market else None, 100, int(cursor or 0), ms), cursor)
        except Exception as e: self._raise(e)

    def get_positions(self, account_id):
        try: rows = self.c.positions() or []
        except Exception as e: self._raise(e)
        return [VenuePosition(internal_symbol(p["symbol"]), str(p.get("side", "")).lower(), D(p.get("size")),
                              (D(p.get("openValue")) / D(p.get("size")) if D(p.get("size")) else Decimal(0)))
                for p in rows if D(p.get("size")) > 0]

    def get_trades(self, account_id, market=None, since=None, cursor=None):
        try:
            orders = self.c.order_history(external_symbol(market) if market else None, 100, int(cursor or 0),
                                          int(since * 1000) if since else None) or []
        except Exception as e: self._raise(e)
        trades = []
        for o in orders:
            if D(o.get("executedQty")) <= 0: continue
            trades.append(VenueTrade(str(o.get("orderId")) + ":fill", str(o.get("orderId")), o.get("clientOrderId"),
                                     internal_symbol(o.get("symbol", "")), D(o.get("executedQty")), D(o.get("avgPrice")),
                                     Decimal(0), "USDT", float(o.get("updateTime", 0)) / 1000))
        return trades, (str(int(cursor or 0) + 1) if len(orders) >= 100 else None)

    def get_account(self, account_id):
        try: rows = self.c.balances() or []
        except Exception as e: self._raise(e)
        a = next((x for x in rows if x.get("asset") == "USDT"), None)
        if not a: raise VenueUnavailable(0, "USDT balance unavailable")
        return VenueAccount(D(a.get("availableBalance")), D(a.get("balance")) + D(a.get("unrealizePnl")))
