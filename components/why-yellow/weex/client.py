"""Small official-WEEX V3 REST client used by WHY.

Private requests use ACCESS-KEY / ACCESS-SIGN / ACCESS-PASSPHRASE. Secrets never
appear in reprs or errors. Order POSTs are single-attempt; callers reconcile any
ambiguous outcome before doing anything else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode

import requests

BASE_URL = "https://api-contract.weex.com"
BROKER_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


class WeexError(RuntimeError):
    def __init__(self, code="weex_error", message="", status=0, ambiguous=False):
        super().__init__(f"{status} {code}: {message}".strip())
        self.code, self.message, self.status, self.ambiguous = str(code), str(message), int(status or 0), bool(ambiguous)


def broker_client_order_id(broker_id: str, unique_id: str, max_length: int = 36) -> str:
    """Preserve WEEX's required broker prefix within this endpoint's documented cap."""
    broker = str(broker_id or "").strip()
    if not BROKER_RE.fullmatch(broker):
        raise ValueError("invalid_weex_broker_id")
    prefix = f"b-{broker}-"
    if len(prefix) >= max_length:
        raise ValueError("weex_broker_id_too_long_for_endpoint")
    suffix = hashlib.sha256(str(unique_id).encode()).hexdigest()[: max_length - len(prefix)]
    return prefix + suffix


class WeexClient:
    def __init__(self, api_key="", api_secret="", passphrase="", *, base_url=BASE_URL,
                 broker_id=None, timeout=15.0, session=None, clock=time.time):
        self.api_key, self._secret, self._passphrase = str(api_key), str(api_secret), str(passphrase)
        self.base_url, self.broker_id, self.timeout = str(base_url).rstrip("/"), broker_id, float(timeout)
        self.s, self.clock = session or requests.Session(), clock

    def __repr__(self):
        return f"WeexClient(base_url={self.base_url!r}, api_key=***, api_secret=***, passphrase=***)"

    @staticmethod
    def sign(secret: str, timestamp: str, method: str, path: str, query="", body="") -> str:
        msg = str(timestamp) + method.upper() + path + (("?" + query) if query else "") + (body or "")
        return base64.b64encode(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()).decode()

    def _request(self, method, path, *, params=None, data=None, private=True, order_write=False):
        query = urlencode([(k, v) for k, v in (params or {}).items() if v is not None])
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=True) if data is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": "WHY-Trading/1.0"}
        if private:
            if not self.api_key or not self._secret or not self._passphrase:
                raise WeexError("credentials_missing", status=401)
            ts = str(int(self.clock() * 1000))
            headers.update({"ACCESS-KEY": self.api_key, "ACCESS-TIMESTAMP": ts,
                            "ACCESS-PASSPHRASE": self._passphrase,
                            "ACCESS-SIGN": self.sign(self._secret, ts, method, path, query, body)})
        url = self.base_url + path + (("?" + query) if query else "")
        try:
            r = self.s.request(method.upper(), url, headers=headers, data=(body or None), timeout=self.timeout)
        except requests.RequestException as e:
            raise WeexError("transport_error", "request failed", ambiguous=order_write) from e
        try:
            payload = r.json()
        except Exception:
            payload = None
        if r.status_code == 429:
            raise WeexError("rate_limited", status=429)
        if r.status_code >= 500:
            raise WeexError("venue_unavailable", status=r.status_code, ambiguous=order_write)
        if not r.ok:
            code = payload.get("code", payload.get("errorCode", "rejected")) if isinstance(payload, dict) else "rejected"
            msg = payload.get("msg", payload.get("errorMessage", "request rejected")) if isinstance(payload, dict) else "request rejected"
            raise WeexError(code, msg, r.status_code)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise WeexError(payload.get("errorCode", "rejected"), payload.get("errorMessage", "request rejected"), 400)
        return payload

    # public market metadata
    def exchange_info(self, symbol=None):
        return self._request("GET", "/capi/v3/market/exchangeInfo", params={"symbol": symbol}, private=False)

    def api_trading_symbols(self):
        return self._request("GET", "/capi/v3/market/apiTradingSymbols", private=False)

    # private account reads / settings
    def balances(self):
        return self._request("GET", "/capi/v3/account/balance")

    def positions(self):
        return self._request("GET", "/capi/v3/account/position/allPosition")

    def set_leverage(self, symbol, leverage, margin_type="CROSSED"):
        data = {"symbol": symbol, "marginType": margin_type}
        if margin_type == "CROSSED":
            data["crossLeverage"] = str(leverage)
        else:
            data.update(isolatedLongLeverage=str(leverage), isolatedShortLeverage=str(leverage))
        return self._request("POST", "/capi/v3/account/leverage", data=data)

    # order lifecycle
    def open_orders(self, symbol=None, limit=100, page=0):
        return self._request("GET", "/capi/v3/openOrders", params={"symbol": symbol, "limit": limit, "page": page})

    def order(self, order_id):
        return self._request("GET", "/capi/v3/order", params={"orderId": order_id})

    def order_history(self, symbol=None, limit=100, page=0, start_time=None, end_time=None):
        return self._request("GET", "/capi/v3/order/history", params={"symbol": symbol, "limit": limit, "page": page,
                                                                       "startTime": start_time, "endTime": end_time})

    def income(self, **filters):
        return self._request("POST", "/capi/v3/account/income", data=filters)

    def place_order(self, *, symbol, side, position_side, order_type, quantity, client_id,
                    time_in_force=None, price=None, reduce_only=False, leverage=None):
        if not self.broker_id:
            raise WeexError("broker_id_missing", status=503)
        coid = broker_client_order_id(self.broker_id, client_id, 36)
        if leverage is not None:
            self.set_leverage(symbol, min(20, int(leverage)))
        data = {"symbol": symbol, "side": side, "positionSide": position_side, "type": order_type,
                "quantity": str(quantity), "newClientOrderId": coid, "reduceOnly": bool(reduce_only)}
        if time_in_force and order_type == "LIMIT": data["timeInForce"] = time_in_force
        if price is not None: data["price"] = str(price)
        return self._request("POST", "/capi/v3/order", data=data, order_write=True)

    def cancel_order(self, *, order_id=None, client_id=None):
        if not order_id and not client_id:
            raise ValueError("order_id_or_client_id_required")
        return self._request("DELETE", "/capi/v3/order", params={"orderId": order_id, "origClientOrderId": client_id}, order_write=True)
