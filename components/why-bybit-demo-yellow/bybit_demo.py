"""Signed Bybit v5 client bound to the DEMO host only. Every call re-asserts the base
URL through guard.py before the request is built. Public market data also comes from
the demo host (identical to mainnet data), so no other host is ever contacted."""
import json, time, hmac, hashlib, urllib.request, urllib.error, urllib.parse, threading
import guard

RECV = "5000"

class BybitError(Exception):
    def __init__(self, code, msg, path):
        super().__init__(f"bybit {path} -> {code} {msg}"); self.code = code; self.msg = msg; self.path = path

class DemoClient:
    def __init__(self, key, secret, base=guard.ALLOWED_BASE, timeout=15, logger=None):
        self.base = guard.assert_demo_base(base); self.key = key; self.secret = secret
        self.timeout = timeout; self.log = logger or (lambda *a: None)
        self.hosts_contacted = set(); self.calls = 0; self.lock = threading.Lock()

    def _sign(self, ts, payload):
        return hmac.new(self.secret.encode(), (ts + self.key + RECV + payload).encode(), hashlib.sha256).hexdigest()

    def _req(self, method, path, params=None, body=None, auth=True, retries=2):
        guard.assert_demo_base(self.base)
        url = self.base + path
        q = urllib.parse.urlencode(params or {})
        if q: url += "?" + q
        guard.check_url(url)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if auth:
            ts = str(int(time.time() * 1000))
            headers.update({"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": RECV,
                            "X-BAPI-SIGN": self._sign(ts, q if method == "GET" else (data.decode() if data else ""))})
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with self.lock:
            self.calls += 1; self.hosts_contacted.add(urllib.parse.urlsplit(url).hostname)
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r: d = json.load(r)
                break
            except urllib.error.HTTPError as e:
                txt = e.read()[:300].decode(errors="replace")
                if attempt < retries and e.code >= 500: time.sleep(1 + attempt); continue
                raise BybitError(f"HTTP{e.code}", txt, path)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < retries: time.sleep(1 + attempt); continue
                raise BybitError("NET", repr(e), path)
        if d.get("retCode") != 0: raise BybitError(d.get("retCode"), d.get("retMsg"), path)
        return d.get("result", {})

    # ---- public (demo host serves the same market data as mainnet) ----
    def tickers(self, symbol=None):
        p = {"category": "linear"}
        if symbol: p["symbol"] = symbol
        return self._req("GET", "/v5/market/tickers", p, auth=False)["list"]
    def instruments(self, symbol=None):
        p = {"category": "linear", "limit": 1000}
        if symbol: p["symbol"] = symbol
        return self._req("GET", "/v5/market/instruments-info", p, auth=False)["list"]
    # ---- private read ----
    def key_info(self): return self._req("GET", "/v5/user/query-api")
    def wallet(self): return self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})["list"]
    def positions(self, symbol=None):
        p = {"category": "linear"}
        p.update({"symbol": symbol} if symbol else {"settleCoin": "USDT"})
        return self._req("GET", "/v5/position/list", p)["list"]
    def order_by_link(self, link):
        r = self._req("GET", "/v5/order/realtime", {"category": "linear", "orderLinkId": link})["list"]
        if r: return r[0]
        r = self._req("GET", "/v5/order/history", {"category": "linear", "orderLinkId": link})["list"]
        return r[0] if r else None
    def executions(self, symbol, start_ms=None, limit=50):
        p = {"category": "linear", "symbol": symbol, "limit": limit}
        if start_ms: p["startTime"] = int(start_ms)
        return self._req("GET", "/v5/execution/list", p)["list"]
    def closed_pnl(self, symbol, start_ms=None, limit=20):
        p = {"category": "linear", "symbol": symbol, "limit": limit}
        if start_ms: p["startTime"] = int(start_ms)
        return self._req("GET", "/v5/position/closed-pnl", p)["list"]
    # ---- private write (DEMO ONLY; guard re-checked inside _req) ----
    def place_market(self, symbol, side, qty, link, take_profit=None, stop_loss=None, trigger_by="MarkPrice", reduce_only=False):
        guard.assert_demo_base(self.base)
        body = dict(category="linear", symbol=symbol, side=side, orderType="Market", qty=str(qty),
                    timeInForce="IOC", positionIdx=0, orderLinkId=link, reduceOnly=bool(reduce_only))
        if take_profit is not None or stop_loss is not None:
            body.update(tpslMode="Full", tpTriggerBy=trigger_by, slTriggerBy=trigger_by, tpOrderType="Market", slOrderType="Market")
            if take_profit is not None: body["takeProfit"] = str(take_profit)
            if stop_loss is not None: body["stopLoss"] = str(stop_loss)
        return self._req("POST", "/v5/order/create", body=body, retries=0)
    def set_trading_stop(self, symbol, take_profit, stop_loss, trigger_by="MarkPrice"):
        guard.assert_demo_base(self.base)
        body = dict(category="linear", symbol=symbol, tpslMode="Full", positionIdx=0,
                    takeProfit=str(take_profit), stopLoss=str(stop_loss), tpTriggerBy=trigger_by, slTriggerBy=trigger_by)
        return self._req("POST", "/v5/position/trading-stop", body=body, retries=0)

def from_secrets(path, logger=None):
    d = json.load(open(path))
    if d.get("env") != "demo" or d.get("host") != guard.ALLOWED_BASE:
        guard.die(f"secrets file {path} is not labelled env=demo/host={guard.ALLOWED_BASE}")
    return DemoClient(d["api_key"], d["secret"], logger=logger)
