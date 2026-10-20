#!/usr/bin/env python3
"""
bybit_exec.py — Bybit v5 (linear USDT perpetuals) execution adapter for the WHY x Bybit book.

Mirrors the shape of /opt/why-yellow/yellow_exec.py: HMAC-signed REST + private WS,
fail-closed, unarmed by default.

SAFETY MODEL
  * NOTHING here sends a trading request unless ALL hold:
      - secrets/api_key.json exists            ({"api_key": "...", "secret": "..."})
      - env BYBIT_ARMED=1
      - preflight() passed (see below)
    Every trading method calls _guard() and raises NotArmed otherwise.
  * preflight() REFUSES to arm if the key has ANY withdrawal permission, is read-only,
    the account is in hedge (two-way) position mode, the margin mode is not cross
    (REGULAR_MARGIN), or the local clock is skewed beyond recv_window.
  * Credentials live only in secrets/ (0700 dir, 0600 file), are never logged, never sent
    to the frontend, and secrets/ + state/ are git-ignored. Withdrawal permission must NOT
    be enabled on the key; the adapter has no withdraw/transfer code at all.
  * `selftest` uses PUBLIC endpoints only (+ two probes that send a deliberately FAKE key to
    confirm the auth layer answers). It registers nothing and moves nothing.

DUPLICATE-ORDER PROTECTION
  Every order carries a deterministic orderLinkId derived from the signal identity
  (strategy, market, side, triggering 5m bar). A retry of the same signal re-sends the same
  link id; Bybit rejects a duplicate (retCode 110072) and we then adopt the existing order
  instead of creating a second one. A restart mid-signal therefore cannot double-enter.

RESTART RECONCILIATION
  reconcile(local_positions) compares exchange-authoritative positions (only the WHY
  universe symbols) with the engine's positions.json. ANY mismatch raises ReconcileError and
  live mode refuses to start — it never guesses, never auto-closes, never adopts blindly.

EXCHANGE-AUTHORITATIVE STATE
  Balances / positions / orders / fills / closed PnL are read from Bybit REST (authoritative);
  the private WS (position / execution / order / wallet topics) keeps a live cache and
  connectivity signal used by the health check.

VENUE FACTS (Bybit v5, verified against public docs + live public API 2026-09-18)
  * Auth headers: X-BAPI-API-KEY, X-BAPI-TIMESTAMP (ms), X-BAPI-RECV-WINDOW, X-BAPI-SIGN.
    sign = HMAC_SHA256(secret, timestamp + apiKey + recvWindow + (queryString | raw JSON body)).
  * Private WS: wss://stream.bybit.com/v5/private, auth args [apiKey, expires_ms, HMAC(secret,"GET/realtime"+expires)].
  * Order create: POST /v5/order/create; position-attached stopLoss (tpslMode Full, MarkPrice trigger)
    is set on the entry so a venue-side stop exists even if this process dies.
"""
import argparse, hashlib, hmac, json, os, sys, threading, time, urllib.parse
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE = os.environ.get("BYBIT_BASE", "https://api.bybit.com")
WS_PRIVATE = os.environ.get("BYBIT_WS_PRIVATE", "wss://stream.bybit.com/v5/private")
HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets")
APIKEY_F = os.path.join(SECRETS, "api_key.json")
STATE = os.path.join(HERE, "state")
LIVE_STATE = os.environ.get("WB_STATE_DIR") or os.path.join(HERE, "state-live")   # where the live book keeps positions.json (the paper book uses state/)
HALT_FLAGS = [os.path.join(HERE, d, "halt.flag") for d in ("state", "state-live")]   # touch either to make every open() refuse
RECV_WINDOW = "5000"
UNIVERSE = {m + "-PERP": m for m in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                                     "BNBUSDT", "LINKUSDT", "SUIUSDT", "TAOUSDT", "HYPEUSDT"]}

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

class BybitError(RuntimeError):
    def __init__(self, ret_code: int, msg: str, http: int = 200):
        super().__init__(f"retCode {ret_code}: {msg} (http {http})")
        self.code, self.msg, self.http = ret_code, msg, http

class NotArmed(RuntimeError): pass
class ReconcileError(RuntimeError): pass
class PreflightError(RuntimeError): pass
class ExitPending(RuntimeError): pass    # position gone but its closing order is not visible in order history yet -> retry, do not guess
class TPPending(RuntimeError): pass      # target reached by mark but the resting maker TP has not filled yet -> wait, do not cross the spread

# ---------------------------------------------------------------- signing
def sign(secret: str, ts: str, api_key: str, recv_window: str, payload: str) -> str:
    return hmac.new(secret.encode(), (ts + api_key + recv_window + payload).encode(), hashlib.sha256).hexdigest()

def link_id(kind: str, *parts: Any) -> str:
    """Deterministic <=36-char orderLinkId. Same signal identity -> same id (duplicate protection)."""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:30]
    return f"wb{kind}{h}"          # 2 + 1 + 30 = 33 chars

def _q(x: Decimal) -> str:
    s = format(x.normalize(), "f")
    return s if "." not in s else (s.rstrip("0").rstrip(".") or "0")

# ---------------------------------------------------------------- REST client
class BybitClient:
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None):
        self.api_key, self.secret = api_key, secret
        self.s = requests.Session()
        self._inst: Dict[str, Dict[str, Any]] = {}
        self._inst_ts = 0.0

    @classmethod
    def from_secrets(cls) -> "BybitClient":
        if not os.path.exists(APIKEY_F): return cls()
        k = json.load(open(APIKEY_F))
        return cls(k["api_key"], k["secret"])

    @property
    def has_keys(self) -> bool: return bool(self.api_key and self.secret)
    def armed(self) -> bool: return self.has_keys and os.environ.get("BYBIT_ARMED") == "1"
    def _guard(self):
        if not self.armed(): raise NotArmed("BYBIT_ARMED!=1 or no API key — refusing to send a trading request")

    def _req(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, auth: bool = True, tries: int = 3) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last: Optional[Exception] = None
        for i in range(tries):
            headers = {"Content-Type": "application/json"}
            if method == "GET":
                qs = urllib.parse.urlencode(sorted(params.items())); payload, body = qs, None
                url = BASE + path + ("?" + qs if qs else "")
            else:
                body = json.dumps(params, separators=(",", ":")); payload, url = body, BASE + path
            if auth:
                if not self.has_keys: raise NotArmed("no API key on disk")
                ts = str(int(time.time() * 1000))
                headers.update({"X-BAPI-API-KEY": self.api_key, "X-BAPI-TIMESTAMP": ts,
                                "X-BAPI-RECV-WINDOW": RECV_WINDOW,
                                "X-BAPI-SIGN": sign(self.secret, ts, self.api_key, RECV_WINDOW, payload)})
            try:
                r = self.s.request(method, url, data=body, headers=headers, timeout=15)
            except requests.RequestException as e:
                last = e; time.sleep(0.5 * (i + 1)); continue
            try: j = r.json()
            except ValueError:   # Bybit's edge answers an invalid key/signature/IP with a bare HTTP 401 + empty body
                j = {"retCode": -1, "retMsg": ("HTTP 401 unauthorized (invalid key / signature / IP)" if r.status_code == 401 else r.text[:200])}
            if (r.status_code in (429, 502, 503, 504) or j.get("retCode") in (10006, 10016)) and i < tries - 1:
                time.sleep(1.0 * (i + 1)); continue
            if r.status_code >= 400 or j.get("retCode") != 0:
                raise BybitError(int(j.get("retCode", -1)), str(j.get("retMsg", ""))[:300], r.status_code)
            return j.get("result", {})
        raise RuntimeError(f"{method} {path} failed after {tries} tries: {last}")

    # ---- public
    def server_time_ms(self) -> int:
        return int(self._req("GET", "/v5/market/time", auth=False)["timeNano"]) // 1_000_000

    def instruments(self, max_age: float = 600) -> Dict[str, Dict[str, Any]]:
        if time.time() - self._inst_ts > max_age:
            out, cur = {}, None
            while True:
                r = self._req("GET", "/v5/market/instruments-info", {"category": "linear", "limit": 1000, "cursor": cur}, auth=False)
                for x in r["list"]: out[x["symbol"]] = x
                cur = r.get("nextPageCursor") or None
                if not cur: break
            self._inst, self._inst_ts = out, time.time()
        return self._inst

    def filters(self, sym: str) -> Dict[str, Any]:
        x = self.instruments()[sym]
        return {"tick": Decimal(x["priceFilter"]["tickSize"]), "step": Decimal(x["lotSizeFilter"]["qtyStep"]),
                "min_qty": Decimal(x["lotSizeFilter"]["minOrderQty"]), "max_qty": Decimal(x["lotSizeFilter"].get("maxMktOrderQty") or x["lotSizeFilter"]["maxOrderQty"]),
                "min_notional": Decimal(x["lotSizeFilter"].get("minNotionalValue") or "5"),
                "max_lev": Decimal(x["leverageFilter"]["maxLeverage"]), "status": x["status"],
                "contract": x["contractType"], "settle": x["settleCoin"]}

    def round_qty(self, sym: str, qty: float) -> str:
        f = self.filters(sym); return _q((Decimal(str(qty)) / f["step"]).to_integral_value(ROUND_DOWN) * f["step"])

    def round_px(self, sym: str, px: float, up: bool) -> str:
        f = self.filters(sym); d = (Decimal(str(px)) / f["tick"]).to_integral_value(ROUND_CEILING if up else ROUND_DOWN)
        return _q(d * f["tick"])

    def orderbook(self, sym: str, limit: int = 200) -> Dict[str, Any]:
        return self._req("GET", "/v5/market/orderbook", {"category": "linear", "symbol": sym, "limit": limit}, auth=False)

    def slippage_bps(self, sym: str, notional_usd: float, is_buy: bool) -> Tuple[float, float]:
        """Walk the book for a market order. Returns (avg fill slippage bps vs top, depth USD within 10bps)."""
        ob = self.orderbook(sym); side = ob["a"] if is_buy else ob["b"]
        top = float(side[0][0]); need = notional_usd; cost = qty = d10 = 0.0
        for px, am in side:
            px, am = float(px), float(am); lvl = px * am
            if abs(px / top - 1) * 1e4 <= 10: d10 += lvl
            take = min(lvl, need)
            if take > 0: cost += take; qty += take / px; need -= take
        if need > 0: return float("inf"), d10
        return (cost / qty / top - 1) * 1e4 * (1 if is_buy else -1), d10

    # ---- private reads (exchange-authoritative)
    def api_info(self) -> Dict[str, Any]: return self._req("GET", "/v5/user/query-api")
    def account_info(self) -> Dict[str, Any]: return self._req("GET", "/v5/account/info")
    def wallet(self) -> Dict[str, Any]:
        l = self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})["list"]; return l[0] if l else {}
    def positions(self, sym: Optional[str] = None) -> List[Dict[str, Any]]:
        p = {"category": "linear", "limit": 200}
        if sym: p["symbol"] = sym
        else: p["settleCoin"] = "USDT"
        return self._req("GET", "/v5/position/list", p)["list"]
    def open_orders(self, sym: Optional[str] = None) -> List[Dict[str, Any]]:
        p = {"category": "linear", "limit": 50}
        if sym: p["symbol"] = sym
        else: p["settleCoin"] = "USDT"
        return self._req("GET", "/v5/order/realtime", p)["list"]
    def order_by_link(self, sym: str, link: str) -> Optional[Dict[str, Any]]:
        for path in ("/v5/order/realtime", "/v5/order/history"):
            l = self._req("GET", path, {"category": "linear", "symbol": sym, "orderLinkId": link})["list"]
            if l: return l[0]
        return None
    def executions(self, sym: str, order_id: str) -> List[Dict[str, Any]]:
        return self._req("GET", "/v5/execution/list", {"category": "linear", "symbol": sym, "orderId": order_id, "limit": 100})["list"]
    def closed_pnl(self, sym: str, limit: int = 10) -> List[Dict[str, Any]]:
        return self._req("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": sym, "limit": limit})["list"]
    def fee_rate(self, sym: str) -> Dict[str, Any]:
        l = self._req("GET", "/v5/account/fee-rate", {"category": "linear", "symbol": sym})["list"]; return l[0] if l else {}

    # ---- trading (ARMED ONLY)
    def set_leverage(self, sym: str, lev: float) -> None:
        self._guard()
        try: self._req("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": sym, "buyLeverage": str(lev), "sellLeverage": str(lev)})
        except BybitError as e:
            if e.code != 110043: raise            # 110043 = leverage not modified (already set)

    def create(self, sym: str, side: str, qty: str, link: str, *, reduce_only: bool = False,
               stop_loss: Optional[str] = None) -> Dict[str, Any]:
        """Market IOC. Idempotent on `link`: a duplicate link id adopts the existing order."""
        self._guard()
        body: Dict[str, Any] = {"category": "linear", "symbol": sym, "side": side, "orderType": "Market", "qty": qty,
                                "timeInForce": "IOC", "positionIdx": 0, "orderLinkId": link, "reduceOnly": reduce_only}
        if stop_loss:
            body.update({"stopLoss": stop_loss, "slTriggerBy": "MarkPrice", "tpslMode": "Full", "slOrderType": "Market"})
        try:
            return self._req("POST", "/v5/order/create", body, tries=1)     # never blind-retry an order POST
        except BybitError as e:
            if e.code == 110072:                                            # duplicate orderLinkId
                ex = self.order_by_link(sym, link)
                if ex: return {"orderId": ex["orderId"], "orderLinkId": link, "adopted": True}
            raise
        except RuntimeError:
            ex = self.order_by_link(sym, link)                              # transport failure: did it land?
            if ex: return {"orderId": ex["orderId"], "orderLinkId": link, "adopted": True}
            raise

    def set_stop(self, sym: str, stop_loss: str) -> None:
        """Amend the position-level stop (Full mode, one-way, same MarkPrice trigger the entry used). TP is not passed, so it is untouched.
        34040 = not modified (already that value)."""
        self._guard()
        try: self._req("POST", "/v5/position/trading-stop", {"category": "linear", "symbol": sym, "tpslMode": "Full", "positionIdx": 0,
                                                             "stopLoss": stop_loss, "slTriggerBy": "MarkPrice"}, tries=1)
        except BybitError as e:
            if e.code != 34040: raise

    def create_limit_maker(self, sym: str, side: str, qty: str, px: str, link: str) -> Dict[str, Any]:
        """Reduce-only PostOnly limit: rests in the book, never crosses the spread (always maker). Idempotent on `link`."""
        self._guard()
        body: Dict[str, Any] = {"category": "linear", "symbol": sym, "side": side, "orderType": "Limit", "qty": qty, "price": px,
                                "timeInForce": "PostOnly", "positionIdx": 0, "orderLinkId": link, "reduceOnly": True}
        try:
            return self._req("POST", "/v5/order/create", body, tries=1)
        except BybitError as e:
            if e.code == 110072:
                ex = self.order_by_link(sym, link)
                if ex: return {"orderId": ex["orderId"], "orderLinkId": link, "adopted": True}
            raise
        except RuntimeError:
            ex = self.order_by_link(sym, link)
            if ex: return {"orderId": ex["orderId"], "orderLinkId": link, "adopted": True}
            raise

    def cancel(self, sym: str, link: str) -> None:
        self._guard(); self._req("POST", "/v5/order/cancel", {"category": "linear", "symbol": sym, "orderLinkId": link})

# ---------------------------------------------------------------- private WS (live cache + health)
class PrivateStream:
    """Authenticated private stream: position / execution / order / wallet. Cache + connectivity only;
    state-changing decisions always re-read REST (authoritative)."""
    def __init__(self, api_key: str, secret: str):
        self.k, self.sec = api_key, secret
        self.connected = False; self.authed = False
        self.last_event = 0.0; self.last_error: Optional[str] = None
        self.cache: Dict[str, Any] = {"position": {}, "order": {}, "execution": [], "wallet": None}
        self.reconnects = 0

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="bybit-private-ws").start()

    def _run(self):
        import websocket
        backoff = 1
        while True:
            try:
                ws = websocket.create_connection(WS_PRIVATE, timeout=20)
                exp = int((time.time() + 10) * 1000)
                sig = hmac.new(self.sec.encode(), f"GET/realtime{exp}".encode(), hashlib.sha256).hexdigest()
                ws.send(json.dumps({"op": "auth", "args": [self.k, exp, sig]}))
                ack = json.loads(ws.recv())
                if not ack.get("success"): raise RuntimeError(f"private ws auth failed: {ack.get('ret_msg')}")
                self.authed = True
                ws.send(json.dumps({"op": "subscribe", "args": ["position", "execution", "order", "wallet"]}))
                self.connected = True; backoff = 1; last_ping = time.time(); ws.settimeout(15)
                while True:
                    if time.time() - last_ping > 20: ws.send(json.dumps({"op": "ping"})); last_ping = time.time()
                    try: msg = json.loads(ws.recv())
                    except websocket.WebSocketTimeoutException: continue
                    t = msg.get("topic"); self.last_event = time.time()
                    if t == "position":
                        for p in msg.get("data", []): self.cache["position"][p["symbol"]] = p
                    elif t == "order":
                        for o in msg.get("data", []): self.cache["order"][o.get("orderLinkId") or o["orderId"]] = o
                    elif t == "execution": self.cache["execution"] = (self.cache["execution"] + msg.get("data", []))[-200:]
                    elif t == "wallet": self.cache["wallet"] = msg.get("data")
            except Exception as e:
                self.connected = self.authed = False; self.last_error = repr(e)[:200]; self.reconnects += 1
                time.sleep(backoff); backoff = min(backoff * 2, 60)

# ---------------------------------------------------------------- live executor (used by bybit_paper.py when WB_MODE=live)
class LiveExecutor:
    def __init__(self, universe: Optional[Dict[str, str]] = None):
        self.u = universe or UNIVERSE
        self.c = BybitClient.from_secrets()
        self.stream: Optional[PrivateStream] = None
        self.ready = False

    # --- integrity gate
    def preflight(self) -> Dict[str, Any]:
        c = self.c
        if not c.has_keys: raise PreflightError("no secrets/api_key.json — live mode cannot start")
        if not c.armed(): raise PreflightError("BYBIT_ARMED!=1 — live mode cannot start")
        skew = abs(c.server_time_ms() - int(time.time() * 1000))
        if skew > 2000: raise PreflightError(f"clock skew {skew}ms > 2000ms")
        info = c.api_info()
        perms = json.dumps(info.get("permissions", {})).lower()
        if "withdraw" in perms: raise PreflightError("API key has a WITHDRAW permission — refuse. Recreate the key without it.")
        if int(info.get("readOnly", 1)) == 1: raise PreflightError("API key is read-only; needs Contract Trade")
        if not info.get("permissions", {}).get("ContractTrade"): raise PreflightError("API key lacks ContractTrade permission")
        acct = c.account_info()
        if acct.get("marginMode") not in ("REGULAR_MARGIN", "ISOLATED_MARGIN"):
            raise PreflightError(f"marginMode={acct.get('marginMode')} (need REGULAR_MARGIN or ISOLATED_MARGIN; portfolio margin is not supported)")
        ips = info.get("ips")
        if os.environ.get("BYBIT_REQUIRE_IP_WHITELIST", "1") == "1" and (not ips or ips == ["*"]):
            raise PreflightError("API key is NOT IP-restricted (ips=['*']). Whitelist this server's IP on the key, or set BYBIT_REQUIRE_IP_WHITELIST=0 to override knowingly.")
        for m, sym in self.u.items():
            idxs = {int(p["positionIdx"]) for p in c.positions(sym)}
            if idxs & {1, 2}: raise PreflightError(f"{sym} is in hedge (two-way) mode; set one-way before arming")
            f = c.filters(sym)
            if f["status"] != "Trading" or f["contract"] != "LinearPerpetual": raise PreflightError(f"{sym} not tradable: {f}")
        self.stream = PrivateStream(c.api_key, c.secret); self.stream.start(); time.sleep(3)
        if not self.stream.authed: raise PreflightError(f"private WS not authed: {self.stream.last_error}")
        self.ready = True
        w = c.wallet()
        rep = {"ips": info.get("ips"), "margin_mode": acct.get("marginMode"), "expires": info.get("expiredAt"), "equity_usd": w.get("totalEquity"),
               "private_ws": self.stream.connected}
        return rep

    def reconcile(self, local_pos: Dict[str, Any]) -> Dict[str, Any]:
        """Exchange positions (universe symbols only) must equal engine positions. Otherwise raise."""
        by_sym = {p["symbol"]: p for p in self.c.positions() if Decimal(p.get("size") or "0") > 0 and p["symbol"] in self.u.values()}
        local = {self.u[m]: p for m, p in local_pos.items() if m in self.u}
        problems = []
        for sym, p in by_sym.items():
            lp = local.get(sym)
            if not lp: problems.append(f"ORPHAN on exchange: {sym} {p['side']} size {p['size']} (engine has none)"); continue
            want = "Buy" if lp["side"] == "long" else "Sell"
            if p["side"] != want: problems.append(f"{sym} side mismatch exchange={p['side']} engine={lp['side']}")
            lq = (lp.get("live") or {}).get("qty")
            if lq and abs(Decimal(p["size"]) - Decimal(str(lq))) > self.c.filters(sym)["step"]:
                problems.append(f"{sym} size mismatch exchange={p['size']} engine={lq}")
        for sym, lp in local.items():
            if sym not in by_sym: problems.append(f"GHOST in engine: {sym} {lp['side']} (no exchange position)")
        for o in self.c.open_orders():
            if o["symbol"] in self.u.values() and not o.get("reduceOnly"):
                problems.append(f"unexpected open order {o['symbol']} {o['side']} {o['orderType']} link={o.get('orderLinkId')}")
        if problems: raise ReconcileError("; ".join(problems))
        return {"exchange_positions": sorted(by_sym), "engine_positions": sorted(local), "clean": True}

    # --- trading
    def _fill(self, sym: str, order_id: str, link: str) -> Dict[str, Any]:
        for _ in range(12):                                   # up to ~6s for the fill to publish
            o = self.c.order_by_link(sym, link)
            if o and o.get("orderStatus") in ("Filled", "PartiallyFilled", "Cancelled", "Rejected", "Deactivated") and Decimal(o.get("cumExecQty") or "0") >= 0:
                if Decimal(o.get("cumExecQty") or "0") > 0:
                    ex = self.c.executions(sym, o["orderId"])
                    for _ in range(4):                        # executions publish a beat after the order: never trust the first empty list
                        if ex: break
                        time.sleep(0.5); ex = self.c.executions(sym, o["orderId"])
                    fee = sum(float(e.get("execFee") or 0) for e in ex)
                    qty = sum(float(e["execQty"]) for e in ex) or float(o["cumExecQty"])
                    avg = (sum(float(e["execQty"]) * float(e["execPrice"]) for e in ex) / qty) if ex and qty else float(o["avgPrice"])
                    out = {"avg_px": avg, "qty": qty, "fee_usd": fee, "notional": qty * avg, "order_id": o["orderId"], "link": link, "status": o["orderStatus"]}
                    if not ex:                                # still nothing: use the order record's own fee, and say so loudly
                        out["fee_usd"] = fee = float(o.get("cumExecFee") or 0)
                        out["fee_unverified"] = True
                        log(f"WARNING fill {sym} {link}: no executions after retries; fee ${fee:.6f} taken from the order record"
                            + ("" if fee else " — FEE IS ZERO, will be repaired from the exchange at close/restart"))
                    return out
                raise RuntimeError(f"order {link} ended {o['orderStatus']} with zero fill ({o.get('rejectReason')})")
            time.sleep(0.5)
        raise RuntimeError(f"order {link} fill not confirmed within 6s — reconcile before retrying")

    def open(self, market: str, side: str, strat: str, notional: float, stop_px: float, leverage: float,
             bar_ms: Optional[int], mark_ts: Optional[float], risk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Size rule: qty = floor(notional/price to qtyStep). If that is below the venue minimum (e.g. BTC 0.001),
        bump to the minimum lot ONLY IF the planned worst-case loss of that lot is <= risk.max_loss_usd; else refuse."""
        self.c._guard()
        if not self.ready: raise PreflightError("preflight has not passed")
        if any(os.path.exists(f) for f in HALT_FLAGS): raise RuntimeError("halt.flag present")
        if mark_ts is None or time.time() - mark_ts > 30: raise RuntimeError("market data stale >30s — refusing to enter")
        if self.stream and not self.stream.connected: raise RuntimeError("private WS down — refusing to enter")
        risk = risk or {}
        sym = self.u[market]; f = self.c.filters(sym)
        px = float(self.c.orderbook(sym, 1)["a" if side == "long" else "b"][0][0])
        qty = Decimal(self.c.round_qty(sym, notional / px))
        min_lot = max(f["min_qty"], (f["min_notional"] / Decimal(str(px)) / f["step"]).to_integral_value(ROUND_CEILING) * f["step"])
        if qty < min_lot:
            planned = float(min_lot) * px * (risk.get("sl_pct", 2.0) / 100 + (risk.get("fee_bps_rt", 11) + risk.get("slip_bps_rt", 4)) / 1e4)
            cap = risk.get("max_loss_usd")
            if cap is None or planned > cap:
                raise RuntimeError(f"{sym} minimum lot {min_lot} (${float(min_lot)*px:.2f}) has planned worst-case loss ${planned:.2f} > cap ${cap}")
            qty = min_lot                                     # min-lot bump, inside the loss cap
        w = self.c.wallet(); usdt = next((float(x.get("walletBalance") or 0) for x in w.get("coin", []) if x["coin"] == "USDT"), 0.0)
        margin = float(qty) * px / float(leverage)
        if usdt < margin * 1.5: raise RuntimeError(f"USDT {usdt:.2f} < 1.5x required margin {margin:.2f}")
        self.c.set_leverage(sym, leverage)
        link = link_id("o", strat, market, side, bar_ms)
        sl = self.c.round_px(sym, stop_px, up=(side == "short"))
        res = self.c.create(sym, "Buy" if side == "long" else "Sell", _q(qty), link, stop_loss=sl)
        fill = self._fill(sym, res["orderId"], link); fill["adopted"] = bool(res.get("adopted")); fill["venue_stop"] = sl
        self._anchor_stop_to_fill(sym, side, fill, sl, risk.get("sl_pct"))
        return fill

    def _anchor_stop_to_fill(self, sym: str, side: str, fill: Dict[str, Any], sl_pre: str, sl_pct: Optional[float]) -> None:
        """The entry carried a venue stop derived from the PRE-fill mark (so the position is protected from its first instant). Now that the
        real fill is known, re-anchor that stop to fill x (1 +/- sl_pct) - the stop the engine itself tracks. Never leaves a position
        unprotected: the pre-fill stop stays in force unless the amend is confirmed, any failure only logs, and the recorded venue_stop is
        always what the exchange reports. Adopted (retried) orders are never touched."""
        if fill.get("adopted") or not sl_pct or not fill.get("avg_px"): return
        try:
            want = fill["avg_px"] * (1 + sl_pct / 100) if side == "short" else fill["avg_px"] * (1 - sl_pct / 100)
            new = self.c.round_px(sym, want, up=(side == "short"))
            if Decimal(new) == Decimal(sl_pre): return
            self.c.set_stop(sym, new)
            held = next((p for p in self.c.positions(sym) if Decimal(p.get("size") or "0") > 0), None)
            seen = (held or {}).get("stopLoss") or ""
            if seen and Decimal(seen) == Decimal(new):
                fill["venue_stop"] = new; log(f"venue stop {sym} re-anchored to the actual fill {fill['avg_px']:.8g}: {sl_pre} -> {new}")
            else:
                if seen: fill["venue_stop"] = seen
                log(f"WARNING venue stop {sym}: amend to {new} not confirmed, exchange shows {seen or 'NONE'} (pre-fill stop was {sl_pre})")
        except Exception as e:
            log(f"venue stop re-anchor to fill FAILED {sym}: {e!r} - the pre-fill venue stop {sl_pre} stays in force")

    # --- resting maker take-profit (reduce-only PostOnly limit at the target)
    TP_GRACE_S = 20      # mark reached the target but the resting TP has not filled: wait this long before crossing the spread

    def _cancel_tp(self, sym: str, link: Optional[str]) -> None:
        if not link: return
        try: self.c.cancel(sym, link)
        except Exception: pass                                # already filled / cancelled / auto-cancelled with the position

    def place_tp(self, market: str, pos: Dict[str, Any]) -> Dict[str, Any]:
        """Resting reduce-only PostOnly limit at pos['target']. Idempotent on the entry's link id. Raises if it did not rest;
        the caller keeps the trade either way (venue stop + engine target watch stay in force)."""
        self.c._guard(); sym = self.u[market]; lv = pos["live"]
        link = link_id("t", lv["link"]); long_ = pos["side"] == "long"
        px = self.c.round_px(sym, pos["target"], up=long_)    # long TP sells: never below target; short TP buys: never above
        self.c.create_limit_maker(sym, "Sell" if long_ else "Buy", _q(Decimal(str(lv["qty"]))), px, link)
        time.sleep(0.4); o = self.c.order_by_link(sym, link)
        if not o or o.get("orderStatus") not in ("New", "PartiallyFilled", "Filled"):
            raise RuntimeError(f"resting TP not accepted: {o and (o.get('orderStatus'), o.get('rejectReason'))}")
        lv["tp_link"], lv["tp_px"] = link, px
        return {"link": link, "px": px, "status": o["orderStatus"]}

    def ensure_tp(self, market: str, pos: Dict[str, Any]) -> str:
        """Startup: make sure a restored live position has its resting TP."""
        sym = self.u[market]; lv = pos["live"]; link = link_id("t", lv["link"])
        o = self.c.order_by_link(sym, link)
        if o and o.get("orderStatus") in ("New", "PartiallyFilled"):
            lv["tp_link"], lv["tp_px"] = link, o.get("price"); return "exists"
        self.place_tp(market, pos); return "placed"

    # --- exit classification: the EXCHANGE order that closed the position decides the label, never a guess
    EXIT_LOOKUP_S = 30   # closing order not yet visible in order history: retry this long before giving up and labelling OTHER

    @staticmethod
    def classify_exit(o: Dict[str, Any], tp_link: Optional[str]) -> Tuple[str, str]:
        """(journal reason, exit_kind) for a filled closing order. Kinds: RESTING_TP | STOP | MANUAL | OTHER
        (TIMEOUT and MARKET_TARGET_FALLBACK are bot-placed market exits and are tagged in close())."""
        link = o.get("orderLinkId") or ""; ct = o.get("createType") or ""; so = o.get("stopOrderType") or ""
        if tp_link and link == tp_link: return "TARGET", "RESTING_TP"
        if ct == "CreateByStopLoss" or so in ("StopLoss", "PartialStopLoss"): return "STOP", "STOP"
        if ct == "CreateByTakeProfit" or so in ("TakeProfit", "PartialTakeProfit"): return "TARGET", "VENUE_TP"
        if ct == "CreateByClosing" or (ct == "CreateByUser" and not link): return "MANUAL", "MANUAL"   # Bybit's own close-position button / no bot link id
        if link.startswith("wbx"): return "OTHER", "BOT_MARKET_EXIT"
        return "OTHER", ct or "UNKNOWN"

    @staticmethod
    def _exit_info(o: Dict[str, Any], kind: str, reason: str) -> Dict[str, Any]:
        return {"reason": reason, "kind": kind, "order_id": o.get("orderId"), "link": o.get("orderLinkId") or "", "create_type": o.get("createType"),
                "order_type": o.get("orderType"), "reduce_only": o.get("reduceOnly"), "avg_px": o.get("avgPrice"), "updated_ms": o.get("updatedTime")}

    def exit_order(self, market: str, pos: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Newest FILLED reduce-only order on the symbol since the position opened (the order that actually closed it)."""
        sym = self.u[market]
        rows = self.c._req("GET", "/v5/order/history", {"category": "linear", "symbol": sym, "startTime": int(pos["opened_ms"]) - 120000, "limit": 50})["list"]
        cands = [r for r in rows if r.get("orderStatus") == "Filled" and r.get("reduceOnly") and Decimal(r.get("cumExecQty") or "0") > 0
                 and int(r.get("updatedTime") or 0) >= int(pos["opened_ms"]) - 5000]
        return max(cands, key=lambda r: int(r["updatedTime"])) if cands else None

    def _classify_gone(self, market: str, pos: Dict[str, Any]) -> str:
        """Position no longer on the exchange: label it from the real closing order and stash the evidence in pos['live']['exit_info']."""
        sym = self.u[market]; lv = pos.setdefault("live", {}); tp = lv.get("tp_link")
        o = self.c.order_by_link(sym, tp) if tp else None
        if o and o.get("orderStatus") == "Filled":
            lv["exit_info"] = self._exit_info(o, "RESTING_TP", "TARGET"); return "TARGET"
        x = self.exit_order(market, pos)
        if x:
            reason, kind = self.classify_exit(x, tp); lv["exit_info"] = self._exit_info(x, kind, reason); return reason
        first = lv.setdefault("exit_miss_ms", int(time.time() * 1000))
        if time.time() * 1000 - first < self.EXIT_LOOKUP_S * 1000:
            lv["exit_chk"] = time.time()
            raise ExitPending(f"{sym}: position gone but the closing order is not in order history yet")
        lv["exit_info"] = {"reason": "OTHER", "kind": "OTHER", "order_id": None, "link": "", "create_type": "NOT_FOUND"}
        return "OTHER"                                        # never guess STOP

    def gone_reason(self, market: str, pos: Dict[str, Any]) -> Optional[str]:
        """None while the exchange still holds the position; else the exchange-derived label (TARGET / STOP / MANUAL / OTHER)."""
        sym = self.u[market]
        if any(Decimal(p.get("size") or "0") > 0 for p in self.c.positions(sym)): return None
        return self._classify_gone(market, pos)

    def entry_fee(self, market: str, pos: Dict[str, Any]) -> Optional[float]:
        """Authoritative entry fee of a live position straight from the exchange (executions of its entry order, else the order record)."""
        lv = pos.get("live") or {}; sym = self.u[market]
        try:
            if lv.get("order_id"):
                ex = [e for e in self.c.executions(sym, lv["order_id"]) if e.get("execType", "Trade") == "Trade"]
                fee = sum(float(e.get("execFee") or 0) for e in ex)
                if ex and fee > 0: return fee
            o = self.c.order_by_link(sym, lv["link"]) if lv.get("link") else None
            fee = float((o or {}).get("cumExecFee") or 0)
            return fee if fee > 0 else None
        except Exception as e:
            log(f"entry_fee lookup {sym} failed: {e!r}"); return None

    def _funding_usd(self, sym: str, opened_ms: int) -> Optional[float]:
        """Exchange-reported funding expense (positive = paid) for the symbol since open, or None."""
        try:
            tl = self.c._req("GET", "/v5/account/transaction-log", {"accountType": "UNIFIED", "category": "linear", "type": "SETTLEMENT",
                             "startTime": opened_ms, "endTime": int(time.time() * 1000), "limit": 50})["list"]
            return -sum(float(r.get("change") or 0) for r in tl if r.get("symbol") == sym)
        except Exception: return None

    def _closed_record(self, market: str, sym: str, pos: Dict[str, Any]) -> Dict[str, Any]:
        for r in self.c.closed_pnl(sym, 5):
            if int(r["updatedTime"]) >= pos["opened_ms"]:
                lv = pos.setdefault("live", {})
                if not lv.get("exit_info"): self._classify_gone(market, pos)      # e.g. on_mark saw the exit first: still label it from the exchange
                info = lv["exit_info"]
                out = {"avg_px": float(r["avgExitPrice"]), "qty": float(r["qty"]), "fee_usd": float(r.get("closeFee") or 0), "via": "venue_exit",
                       "exit_info": info, "exit_reason": info["reason"]}
                f = self._funding_usd(sym, pos["opened_ms"])
                if f is not None: out["funding_usd"] = f
                return out
        raise RuntimeError(f"{sym}: no exchange position and no closed-pnl record — reconcile manually")

    # --- exchange-authoritative capital (read-only; own HTTP session so it never contends with the trading thread)
    FLOW_SKIP = ("TRADE", "SETTLEMENT")

    def exchange_equity(self) -> Dict[str, Any]:
        if not hasattr(self, "_ro"): self._ro = BybitClient.from_secrets()
        w = self._ro._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})["list"][0]
        u = next((x for x in w.get("coin", []) if x["coin"] == "USDT"), {})
        return {"usdt_wallet": float(u.get("walletBalance") or 0), "usdt_equity": float(u.get("equity") or 0),
                "usdt_upnl": float(u.get("unrealisedPnl") or 0), "total_equity": float(w.get("totalEquity") or 0), "ts": int(time.time() * 1000)}

    def sync_flows(self, path: str, since_ms: int) -> Dict[str, Any]:
        """Deposits / withdrawals / transfers / other NON-trading USDT ledger rows since live activation, accumulated in `path`
        (de-duplicated by ledger id) so the funded amount survives Bybit's 7-day query window."""
        if not hasattr(self, "_ro"): self._ro = BybitClient.from_secrets()
        try: st = json.load(open(path))
        except Exception: st = {"since_ms": since_ms, "checked_ms": since_ms, "flows": {}}
        now = int(time.time() * 1000)
        t0 = max(st["checked_ms"] - 600000, now - int(6.5 * 86400 * 1000), st["since_ms"] - 600000)
        cursor = None
        for _ in range(20):
            p = {"accountType": "UNIFIED", "startTime": t0, "endTime": now, "limit": 50}
            if cursor: p["cursor"] = cursor
            r = self._ro._req("GET", "/v5/account/transaction-log", p)
            for row in r.get("list", []):
                if row.get("type") in self.FLOW_SKIP or row.get("currency") != "USDT": continue
                if int(row["transactionTime"]) < st["since_ms"]: continue
                st["flows"][row["id"]] = {"ts": int(row["transactionTime"]), "type": row["type"], "change": float(row["change"])}
            cursor = r.get("nextPageCursor")
            if not cursor: break
        st["checked_ms"] = now
        tmp = path + ".tmp"
        with open(tmp, "w") as f: json.dump(st, f)
        os.replace(tmp, path)
        return st

    def close(self, market: str, pos: Dict[str, Any], reason: str) -> Dict[str, Any]:
        self.c._guard()
        sym = self.u[market]; lv = pos.get("live") or {}; tp = lv.get("tp_link")
        if lv.get("exit_miss_ms") and time.time() - lv.get("exit_chk", 0) < 2: raise ExitPending(f"{sym}: closing order not visible yet (throttled)")
        if reason == "TARGET" and tp:                         # throttle: on_mark calls this on every tick while the TP is working
            if time.time() - lv.get("tp_chk", 0) < 2: raise TPPending(f"{sym}: resting maker TP working")
            lv["tp_chk"] = time.time()
        def held(): return [p for p in self.c.positions(sym) if Decimal(p.get("size") or "0") > 0]
        ex = held()
        if ex and tp:
            o = self.c.order_by_link(sym, tp)
            if reason == "TARGET" and o and o.get("orderStatus") in ("New", "PartiallyFilled"):
                t0 = lv.setdefault("tp_defer_ms", int(time.time() * 1000))
                if time.time() * 1000 - t0 < self.TP_GRACE_S * 1000:
                    raise TPPending(f"{sym}: target reached, resting maker TP working (<= {self.TP_GRACE_S}s before market fallback)")
            self._cancel_tp(sym, tp); ex = held()             # a reduce-only limit must not sit beside our market exit; TP may fill while cancelling
        if not ex:                                            # resting TP or venue stop already closed it — report the real exit
            self._cancel_tp(sym, tp)
            return self._closed_record(market, sym, pos)
        size = ex[0]["size"]
        link = link_id("x", lv.get("link", pos["opened_ms"]), reason)
        res = self.c.create(sym, "Sell" if pos["side"] == "long" else "Buy", size, link, reduce_only=True)
        out = self._fill(sym, res["orderId"], link)
        kind = "MARKET_TARGET_FALLBACK" if reason == "TARGET" else reason        # TIMEOUT / STOP forced by the engine
        out["exit_info"] = {"reason": reason, "kind": kind, "order_id": out.get("order_id"), "link": link, "create_type": "CreateByUser",
                            "order_type": "Market", "reduce_only": True, "avg_px": out.get("avg_px")}
        f = self._funding_usd(sym, pos["opened_ms"])
        if f is not None: out["funding_usd"] = f
        return out

# ---------------------------------------------------------------- CLI
def do_selftest(clip: float) -> int:
    ok = True
    # 1) signing: the exact string that is signed is the exact string that is sent
    qs = urllib.parse.urlencode(sorted({"category": "linear", "symbol": "BTCUSDT", "limit": 5}.items()))
    s1 = sign("secret", "1700000000000", "KEY", RECV_WINDOW, qs)
    ref = hmac.new(b"secret", ("1700000000000" + "KEY" + RECV_WINDOW + "category=linear&limit=5&symbol=BTCUSDT").encode(), hashlib.sha256).hexdigest()
    print("sign GET payload :", "OK" if s1 == ref else "MISMATCH", "|", qs); ok &= s1 == ref
    l1, l2 = link_id("o", "WY-PULLBACK", "BTCUSDT-PERP", "long", 1789766100000), link_id("o", "WY-PULLBACK", "BTCUSDT-PERP", "long", 1789766100000)
    print("link id determinism:", "OK" if l1 == l2 and len(l1) <= 36 else "FAIL", l1, f"len={len(l1)}"); ok &= (l1 == l2 and len(l1) <= 36)
    # 2) clock
    c = BybitClient(); skew = c.server_time_ms() - int(time.time() * 1000)
    print(f"clock skew vs Bybit: {skew} ms", "OK" if abs(skew) < 2000 else "TOO LARGE"); ok &= abs(skew) < 2000
    # 3) auth layer answers a deliberately FAKE key (no real credential involved)
    fake = BybitClient("FAKEKEYFAKEKEY", "fakesecretfakesecretfakesecret")
    try: fake._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, tries=1); print("fake-key probe: UNEXPECTED SUCCESS"); ok = False
    except BybitError as e:
        print(f"fake-key REST probe: auth layer answered http {e.http} retCode {e.code} ({e.msg[:60]}) -> endpoint + headers reachable"); ok &= (e.http == 401 or e.code in (10003, 10004, 10005))
    try:
        import websocket
        ws = websocket.create_connection(WS_PRIVATE, timeout=15); exp = int((time.time() + 10) * 1000)
        sig = hmac.new(b"fakesecretfakesecretfakesecret", f"GET/realtime{exp}".encode(), hashlib.sha256).hexdigest()
        ws.send(json.dumps({"op": "auth", "args": ["FAKEKEYFAKEKEY", exp, sig]})); ack = json.loads(ws.recv()); ws.close()
        print(f"fake-key private-WS probe: success={ack.get('success')} ret_msg={str(ack.get('ret_msg'))[:50]} -> private WS reachable, auth protocol answered")
        ok &= ack.get("success") is False
    except Exception as e: print("private WS probe FAIL", e); ok = False
    # 4) instruments + liquidity for the clip
    inst = c.instruments()
    print(f"\ninstruments-info: {len(inst)} linear symbols; universe present: {all(s in inst for s in UNIVERSE.values())}")
    print(f"{'yellow symbol':15} {'bybit':10} {'status':8} {'contract':16} {'tick':>9} {'step':>7} {'minNot':>6} {'maxLev':>6} | ${clip:.0f} clip slip buy/sell bps | depth<=10bps buy/sell $ | qty")
    for m, sym in UNIVERSE.items():
        try:
            f = c.filters(sym); sb, db = c.slippage_bps(sym, clip, True); ss, ds = c.slippage_bps(sym, clip, False)
            top = float(c.orderbook(sym, 1)["a"][0][0]); qty = c.round_qty(sym, clip / top)
            good = f["status"] == "Trading" and f["contract"] == "LinearPerpetual" and Decimal(qty) >= f["min_qty"]
            ok &= good
            print(f"{m:15} {sym:10} {f['status']:8} {f['contract']:16} {str(f['tick']):>9} {str(f['step']):>7} {str(f['min_notional']):>6} {str(f['max_lev']):>6} | {sb:6.1f} / {ss:6.1f} | {db:10.0f} / {ds:10.0f} | {qty}")
        except Exception as e: print(f"{m:15} ERR {e}"); ok = False
    print("\nkeys on disk:", os.path.exists(APIKEY_F), "| armed:", BybitClient.from_secrets().armed())
    print("SELFTEST", "PASS" if ok else "FAIL"); return 0 if ok else 1

def do_status() -> int:
    c = BybitClient.from_secrets()
    if not c.has_keys: print("no secrets/api_key.json — create a Bybit API key (Contract Trade + read only, NO withdrawal, IP-whitelisted) and save it as {\"api_key\":..., \"secret\":...} (0600)"); return 1
    info = c.api_info(); w = c.wallet()
    print(json.dumps({"key_permissions": info.get("permissions"), "readOnly": info.get("readOnly"), "ips": info.get("ips"),
                      "equity": w.get("totalEquity"), "available": w.get("totalAvailableBalance"),
                      "positions": [p for p in c.positions() if Decimal(p.get("size") or "0") > 0],
                      "open_orders": c.open_orders(), "armed": c.armed()}, indent=2))
    return 0

def do_fees() -> int:
    """Authenticated, read-only: pull the account's fee rates for the universe and write state*/fees.json."""
    c = BybitClient.from_secrets()
    if not c.has_keys: print("no API key on disk"); return 1
    info = c.api_info(); rates = {}
    for m, sym in UNIVERSE.items():
        r = c.fee_rate(sym); rates[sym] = {"maker_bps": float(r["makerFeeRate"]) * 1e4, "taker_bps": float(r["takerFeeRate"]) * 1e4}
    doc = {"source": "GET /v5/account/fee-rate (authenticated)", "vip_level": info.get("vipLevel"), "fetched_ms": int(time.time() * 1000), "rates": rates}
    for d in ("state", "state-live"):
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
        with open(os.path.join(HERE, d, "fees.json"), "w") as f: json.dump(doc, f, indent=1)
    print(json.dumps({"vip": doc["vip_level"], "distinct_maker_bps": sorted({v["maker_bps"] for v in rates.values()}),
                      "distinct_taker_bps": sorted({v["taker_bps"] for v in rates.values()})}))
    return 0

def do_reconcile() -> int:
    ex = LiveExecutor()
    if not ex.c.has_keys: print("no API key on disk"); return 1
    pf = os.path.join(LIVE_STATE, "positions.json")
    pos = json.load(open(pf)) if os.path.exists(pf) else {}
    print(f"local state: {pf} ({len(pos)} position(s))")
    try: print(ex.reconcile(pos)); return 0
    except ReconcileError as e: print("RECONCILE FAILED:", e); return 2

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("selftest", help="public endpoints + signing + fake-key probes (registers nothing)"); s.add_argument("--clip", type=float, default=2000)
    sub.add_parser("status", help="balances / positions / open orders (read-only; needs keys)")
    sub.add_parser("fees", help="fetch authenticated fee rates -> state/fees.json (read-only)")
    sub.add_parser("reconcile", help="compare exchange positions with the LIVE book state-live/positions.json (read-only; needs keys)")
    a = ap.parse_args()
    return {"selftest": lambda: do_selftest(a.clip), "status": do_status, "reconcile": do_reconcile, "fees": do_fees}[a.cmd]()

if __name__ == "__main__": sys.exit(main())
