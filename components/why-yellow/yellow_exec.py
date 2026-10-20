#!/opt/why-yellow/venv/bin/python
"""
yellow_exec.py — Yellow Pro (yellow.pro) perpetuals execution adapter.

Mirrors the shape of binance_exec's executor for the WHY x Yellow book:
headless wallet auth -> API-key mint -> HMAC-signed REST -> orders/positions.

SAFETY MODEL
  * Nothing here places an order unless BOTH hold:
      - secrets/api_key.json exists (minted by `setup`)
      - env YELLOW_ARMED=1
    Every trading method checks `armed()` and raises otherwise.
  * EXECUTION-SAFETY FIX (2026-09-22, audit F-02/F-04/F-05/F-07/F-08):
      - order POSTs are NEVER retried; an ambiguous transport result raises
        TransportAmbiguous and only execution.engine reconciliation may resubmit;
      - client_order_id is mandatory on every order; local validation
        (execution.validation) runs before any order request;
      - entries are gated by state/exec_switches.json (allow_new_entries) in
        addition to arming; exits/cancels/flatten are NOT blocked by that switch;
      - open_orders/orders/trades walk every cursor page; cancel_verified returns
        an explicit result and checks both documented id fields; fills are
        de-duplicated by trade id.
  * `selftest` uses public endpoints only. It generates a throwaway keypair in
    memory and requests ONE auth challenge (no verify, nothing registered).
  * `setup` is the only command that registers anything with Yellow. It creates
    the box wallet, verifies it, and mints a scoped, IP-whitelisted API key.
    Run it only when the owner says "go to execute".

VENUE FACTS (verified 2026-09-12 against prod docs + live public API)
  * 10 USDT-margined perps, cross margin ONLY, long/short legs tracked separately.
  * Order: POST /perpetual/order {app_session_id, market, side buy|sell,
    direction long|short, type limit|market|trigger_limit|trigger_market,
    amount(str), price(str), leverage(str), time_in_force gtc|ioc|fok,
    reduce_only, client_order_id}. Returns {order_uuid}.
  * KNOWN BUG (their docs): DELETE /perpetual/order returns 200 but the order
    can stay in `wait`. -> cancel_verified() polls open_orders and re-cancels.
  * Perpetual stops use trigger_market + trigger_price + trigger_type=stop_loss,
    as documented in docs.yellow.pro/mcp/tools and layer-3/yellow-pro-mcp.
    The engine verifies the resting reduce-only stop through order readback.
  * Signing: HMAC-SHA256(secret, METHOD + PATH + unix_seconds + canonical),
    canonical = sorted `k=v` joined by `|` (POST: body keys, GET/DELETE: query).
"""
import argparse, hashlib, hmac, json, os, sys, time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

import requests

AUTH_BASE  = os.environ.get("YELLOW_AUTH_BASE",  "https://auth.api.yellow.pro")
TRADE_BASE = os.environ.get("YELLOW_TRADE_BASE", "https://trade.api.yellow.pro")
HERE       = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)
try:
    from execution.transport import TransportAmbiguous
    from execution.switches import read_mirror as _read_switches
except ImportError:                       # execution package missing: fail closed for entries
    class TransportAmbiguous(RuntimeError): pass
    def _read_switches(_p): return type("Sw", (), {"allow_new_entries": False, "manage_existing_positions": True})()
SWITCHES_F = os.path.join(HERE, "state", "exec_switches.json")
SECRETS    = os.path.join(HERE, "secrets")
WALLET_F   = os.path.join(SECRETS, "wallet.json")
APIKEY_F   = os.path.join(SECRETS, "api_key.json")
UNIVERSE   = ["BTCUSDT-PERP","ETHUSDT-PERP","SOLUSDT-PERP","XRPUSDT-PERP","DOGEUSDT-PERP",
              "BNBUSDT-PERP","LINKUSDT-PERP","SUIUSDT-PERP","TAOUSDT-PERP","HYPEUSDT-PERP"]

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

class YellowError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status, self.code, self.message = status, code, message

class NotArmed(RuntimeError): pass

# ---------------------------------------------------------------- signing
def _canon_val(v: Any) -> str:
    if isinstance(v, bool): return "true" if v else "false"
    if v is None: return ""
    if isinstance(v, (list, tuple)): return ",".join(sorted(map(str, v)))
    return str(v)

def canonical(params: Dict[str, Any]) -> str:
    return "|".join(f"{k}={_canon_val(params[k])}" for k in sorted(params))

def sign(secret: str, method: str, path: str, ts: str, params: Dict[str, Any]) -> str:
    prehash = f"{method.upper()}{path}{ts}{canonical(params)}"
    return hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()

def auth_headers(api_key: str, secret: str, method: str, path: str, params: Dict[str, Any]) -> Dict[str, str]:
    ts = str(int(time.time()))
    return {"X-API-KEY": api_key, "X-TIMESTAMP": ts,
            "X-SIGNATURE": sign(secret, method, path, ts, params),
            "Content-Type": "application/json"}

# ---------------------------------------------------------------- wallet / auth (JWT)
def _write_secret(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(SECRETS, exist_ok=True); os.chmod(SECRETS, 0o700)
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=2)
    os.chmod(tmp, 0o600); os.replace(tmp, path)

def new_wallet() -> Tuple[str, str]:
    from eth_account import Account
    acct = Account.create()
    return acct.address, acct.key.hex() if acct.key.hex().startswith("0x") else "0x" + acct.key.hex()

def load_or_create_wallet() -> Tuple[str, str]:
    if os.path.exists(WALLET_F):
        w = json.load(open(WALLET_F)); return w["address"], w["private_key"]
    addr, pk = new_wallet()
    _write_secret(WALLET_F, {"address": addr, "private_key": pk, "created": time.time(),
                             "note": "Yellow Pro box wallet. Deposits: USDT on Ethereum mainnet to this address via yellow.pro UI/API."})
    log(f"created box wallet {addr} -> {WALLET_F}")
    return addr, pk

def sign_challenge(private_key: str, challenge: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    sig = Account.sign_message(encode_defunct(text=challenge), private_key=private_key).signature
    h = sig.hex()
    return h if h.startswith("0x") else "0x" + h

def _auth_post(path: str, body: Dict[str, Any], jwt: Optional[str] = None) -> Dict[str, Any]:
    h = {"Content-Type": "application/json"}
    if jwt: h["Authorization"] = f"Bearer {jwt}"
    r = requests.post(AUTH_BASE + path, json=body, headers=h, timeout=20)
    try: j = r.json()
    except Exception: j = {"error": "non_json", "message": r.text[:300]}
    if r.status_code >= 400: raise YellowError(r.status_code, j.get("error", "?"), j.get("message", ""))
    return j

def jwt_login(address: str, private_key: str) -> Dict[str, Any]:
    ch = _auth_post("/auth/challenge", {"wallet_address": address})
    sig = sign_challenge(private_key, ch["challenge"])
    return _auth_post("/auth/verify", {"wallet_address": address, "challenge": ch["challenge"], "signature": sig})

def jwt_get(path: str, jwt: str, params: Optional[Dict[str, Any]] = None, base: str = TRADE_BASE) -> Any:
    r = requests.get(base + path, params=params, headers={"Authorization": f"Bearer {jwt}"}, timeout=20)
    j = r.json() if r.content else {}
    if r.status_code >= 400: raise YellowError(r.status_code, j.get("error", "?"), j.get("message", ""))
    return j

def jwt_post(path: str, jwt: str, body: Dict[str, Any], base: str = TRADE_BASE) -> Any:
    r = requests.post(base + path, json=body, headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}, timeout=20)
    j = r.json() if r.content else {}
    if r.status_code >= 400: raise YellowError(r.status_code, j.get("error", "?"), j.get("message", ""))
    return j

# ---------------------------------------------------------------- REST client (API key)
def _q(x: Decimal) -> str:
    s = format(x.normalize(), "f")
    return s if "." not in s else s.rstrip("0").rstrip(".") or "0"

class YellowClient:
    """HMAC-signed REST client. Public endpoints work without credentials."""
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, app_session_id: Optional[str] = None):
        self.api_key, self.secret, self.asid = api_key, secret, app_session_id
        self.s = requests.Session()
        self._exinfo: Dict[str, Dict[str, Any]] = {}
        self._exinfo_ts = 0.0

    @classmethod
    def from_secrets(cls) -> "YellowClient":
        if not os.path.exists(APIKEY_F): return cls()
        k = json.load(open(APIKEY_F))
        return cls(k["api_key"], k["secret"], k.get("app_session_id"))

    @property
    def has_keys(self) -> bool: return bool(self.api_key and self.secret and self.asid)

    def _req(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, auth: bool = True, tries: int = 3,
             idempotent: bool = True) -> Any:
        """idempotent=True (reads, cancels): bounded retries on transport errors / 429 / 5xx.
        idempotent=False (order submission, batch close): exactly ONE attempt; any
        transport error or 5xx raises TransportAmbiguous because the venue may have
        accepted the request. Callers must reconcile, never resubmit blindly."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = TRADE_BASE + path
        last: Optional[Exception] = None
        attempts = tries if idempotent else 1
        for i in range(attempts):
            headers = auth_headers(self.api_key, self.secret, method, path, params) if (auth and self.api_key) else {"Content-Type": "application/json"}
            try:
                if method in ("GET",):
                    r = self.s.get(url, params=params, headers=headers, timeout=20)
                elif method == "DELETE":
                    # docs: DELETE canonical = query params; body carries the same fields
                    r = self.s.delete(url, params=params, headers=headers, data=json.dumps(params, separators=(",", ":")), timeout=20)
                else:
                    r = self.s.request(method, url, headers=headers, data=json.dumps(params, separators=(",", ":")), timeout=20)
            except requests.RequestException as e:
                if not idempotent:
                    raise TransportAmbiguous(f"{method} {path}: transport failure after send, outcome unknown: {e!r}") from e
                last = e; time.sleep(0.5 * (i + 1)); continue
            try: j = r.json() if r.content else {}
            except ValueError: j = {"error": "non_json", "message": r.text[:300]}
            if r.status_code == 401 and isinstance(j, dict) and j.get("error") == "invalid_timestamp":
                print(f"Yellow timestamp rejection {method} {path} header_age_s={time.time()-int(headers['X-TIMESTAMP']):.1f}", flush=True)
            # A cold connection can consume Yellow's five-second signature window
            # before the request reaches the venue. Reads and idempotent cancels
            # are safe to retry with
            # a newly generated timestamp; order submissions remain one-shot.
            if (method in ("GET", "DELETE") and idempotent and r.status_code == 401
                    and isinstance(j, dict) and j.get("error") == "invalid_timestamp"
                    and i < attempts - 1):
                time.sleep(0.1)
                continue
            if r.status_code in (502, 503, 504, 500) and not idempotent:
                raise TransportAmbiguous(f"{method} {path}: HTTP {r.status_code} on non-idempotent request, outcome unknown")
            if r.status_code in (502, 503, 504, 429) and i < attempts - 1:
                time.sleep(1.0 * (i + 1)); continue
            if r.status_code >= 400:
                raise YellowError(r.status_code, (j or {}).get("error", "?") if isinstance(j, dict) else "?",
                                  (j or {}).get("message", "") if isinstance(j, dict) else str(j)[:300])
            return j
        raise RuntimeError(f"{method} {path} failed: {last}")

    # ---- public
    def exchange_info(self, max_age: float = 600) -> Dict[str, Dict[str, Any]]:
        if time.time() - self._exinfo_ts > max_age:
            j = self._req("GET", "/perpetual/exchangeInfo", auth=False)
            self._exinfo = {s["symbol"]: s for s in j["symbols"]}; self._exinfo_ts = time.time()
        return self._exinfo

    def filters(self, market: str) -> Dict[str, Any]:
        s = self.exchange_info()[market]
        f = {x["filter_type"]: x["config"] for x in s["filters"]}
        return {"tick": Decimal(f["PRICE_FILTER"]["tick_size"]), "step": Decimal(f["LOT_SIZE"]["step_size"]),
                "min_qty": Decimal(f["LOT_SIZE"]["min_qty"]), "min_notional": Decimal(f["MIN_NOTIONAL"]["min_notional"]),
                "max_lev": Decimal(s["max_allowed_leverage"]), "status": s["status"],
                "maker": Decimal(s["maker_fee_rate"]), "taker": Decimal(s["taker_fee_rate"]),
                "px_max_ratio": Decimal(f["PRICE_FILTER"]["price_max_ratio"]), "px_min_ratio": Decimal(f["PRICE_FILTER"]["price_min_ratio"])}

    def round_qty(self, market: str, qty: float) -> str:
        f = self.filters(market); q = (Decimal(str(qty)) / f["step"]).to_integral_value(ROUND_DOWN) * f["step"]
        return _q(q)

    def round_px(self, market: str, px: float, up: bool) -> str:
        f = self.filters(market); d = Decimal(str(px)) / f["tick"]
        d = d.to_integral_value("ROUND_CEILING" if up else ROUND_DOWN) * f["tick"]
        return _q(d)

    def orderbook(self, market: str) -> Dict[str, List[List[str]]]:
        return self._req("GET", "/orderbook", {"symbol": market}, auth=False)

    def top_of_book(self, market: str) -> Tuple[float, float]:
        ob = self.orderbook(market); return float(ob["bids"][0][0]), float(ob["asks"][0][0])

    def slippage_bps(self, market: str, notional_usd: float, is_buy: bool) -> Tuple[float, float]:
        """Walk the book for a market order of `notional_usd`. Returns (avg_fill_slip_bps_vs_top, depth_usd_within_10bps)."""
        ob = self.orderbook(market); side = ob["asks"] if is_buy else ob["bids"]
        top = float(side[0][0]); need = notional_usd; cost = 0.0; qty = 0.0; d10 = 0.0
        for px, am in side:
            px, am = float(px), float(am); lvl = px * am
            if abs(px / top - 1) * 1e4 <= 10: d10 += lvl
            take = min(lvl, need)
            if take > 0: cost += take; qty += take / px; need -= take
        if need > 0: return float("inf"), d10
        avg = cost / qty
        return (avg / top - 1) * 1e4 * (1 if is_buy else -1), d10

    def klines(self, market: str, interval: str = "5m", limit: int = 400) -> List[List[Any]]:
        return self._req("GET", "/klines", {"symbol": market, "interval": interval, "limit": limit}, auth=False)

    def fee_schedule(self) -> Any:
        return self._req("GET", "/account/fee-schedule", auth=False)

    # ---- private reads
    def accounts(self) -> List[Dict[str, Any]]: return self._req("GET", "/perpetual/accounts")
    def account(self) -> Dict[str, Any]: return self._req("GET", "/perpetual/account", {"app_session_id": self.asid})
    def positions(self) -> List[Dict[str, Any]]: return self._req("GET", "/perpetual/positions", {"app_session_id": self.asid})
    # ---- cursor pagination (docs: use_cursor=true, follow next_cursor while has_more; page_size max 100)
    def _page(self, path: str, params: Dict[str, Any], cursor: Optional[str], page_size: int = 100) -> Dict[str, Any]:
        q = dict(params, use_cursor="true", page_size=page_size)
        if cursor: q["cursor"] = cursor
        j = self._req("GET", path, q)
        return j if isinstance(j, dict) else {"orders": j, "trades": j, "has_more": False, "next_cursor": ""}
    def _walk(self, path: str, params: Dict[str, Any], key: str, max_pages: int = 200) -> List[Dict[str, Any]]:
        out, cursor = [], None
        for _ in range(max_pages):
            j = self._page(path, params, cursor)
            out.extend(j.get(key, []) or [])
            if not j.get("has_more") or not j.get("next_cursor"): return out
            cursor = j["next_cursor"]
        raise RuntimeError(f"{path}: pagination exceeded {max_pages} pages")
    def open_orders_page(self, market: Optional[str] = None, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._page("/perpetual/open_orders", {"app_session_id": self.asid, "market": market}, cursor)
    def orders_page(self, market: Optional[str] = None, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._page("/perpetual/orders", {"app_session_id": self.asid, "market": market}, cursor, page_size=1)
    def trades_page(self, market: Optional[str] = None, cursor: Optional[str] = None, start_time: Optional[str] = None) -> Dict[str, Any]:
        return self._page("/perpetual/trades", {"app_session_id": self.asid, "market": market, "start_time": start_time}, cursor)
    def open_orders(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        """ALL open orders (every page), never just the first page."""
        return self._walk("/perpetual/open_orders", {"app_session_id": self.asid, "market": market}, "orders")
    def orders(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._walk("/perpetual/orders", {"app_session_id": self.asid, "market": market}, "orders")
    def trades(self, market: Optional[str] = None, start_time: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._walk("/perpetual/trades", {"app_session_id": self.asid, "market": market, "start_time": start_time}, "trades")
    def market_fee_rate(self, market: str) -> Dict[str, Any]:
        j = self._req("GET", "/perpetual/account/market-fee-rate", {"app_session_id": self.asid, "market": market})
        return j[0] if isinstance(j, list) and j else j
    def equity(self) -> float: return float(self.account()["total_account_equity"])
    def available(self) -> float: return float(self.account()["available_balance"])

    # ---- trading (ARMED ONLY)
    def armed(self) -> bool: return self.has_keys and os.environ.get("YELLOW_ARMED") == "1"
    def entries_allowed(self) -> bool:
        """Separate from arming: the persisted switch execution.switches / state/exec_switches.json.
        Fails closed if the mirror is missing. Never consulted for exits/cancels/flatten."""
        return bool(_read_switches(SWITCHES_F).allow_new_entries)
    def _guard(self, kind: str = "manage"):
        if not self.armed(): raise NotArmed("YELLOW_ARMED!=1 or no API key — refusing to send a trading request")
        if kind == "entry" and not self.entries_allowed():
            raise NotArmed("new entries disabled by exec_switches.json (allow_new_entries=false); position management unaffected")

    def set_leverage(self, market: str, leverage: int) -> Dict[str, Any]:
        self._guard(); return self._req("POST", "/perpetual/leverage", {"app_session_id": self.asid, "market": market, "leverage": str(leverage)})

    def place(self, market: str, is_buy: bool, direction: str, qty: str, *, typ: str = "limit", price: Optional[str] = None,
              tif: str = "gtc", reduce_only: bool = False, leverage: int = 10, coid: Optional[str] = None,
              ref_price: Optional[str] = None, validate: bool = True,
              trigger_price: Optional[str] = None, trigger_type: Optional[str] = None) -> str:
        """ONE attempt, mandatory client_order_id, strict local validation. Raises
        TransportAmbiguous when the outcome is unknown (caller must reconcile)."""
        self._guard("manage" if reduce_only else "entry")
        if not coid: raise ValueError("client_order_id is mandatory (deterministic per intent)")
        if typ.startswith("trigger"):
            if trigger_price is None:
                raise ValueError("trigger order needs trigger_price")
            trigger = Decimal(str(trigger_price))
            if not trigger.is_finite() or trigger <= 0:
                raise ValueError("trigger_price must be finite and positive")
            if trigger_type not in (None, "stop_loss", "take_profit"):
                raise ValueError("invalid trigger_type")
        elif trigger_price is not None or trigger_type is not None:
            raise ValueError("trigger fields require a trigger order")
        if validate:
            from execution.validation import validate_order
            from execution.metadata import MarketFilters
            from execution.types import OrderRequest
            filt = MarketFilters.from_adapter_filters(market, self.filters(market))
            rp = ref_price
            if rp is None and not typ.endswith("limit"):
                bid, ask = self.top_of_book(market); rp = str((Decimal(str(bid)) + Decimal(str(ask))) / 2)
            validate_order(OrderRequest(account_id=self.asid or "", market=market, side="buy" if is_buy else "sell", direction=direction,
                                        order_type=typ, qty=Decimal(str(qty)), leverage=int(leverage), client_order_id=coid,
                                        price=None if price is None else Decimal(str(price)), reduce_only=reduce_only, time_in_force=tif,
                                        trigger_price=None if trigger_price is None else Decimal(str(trigger_price)), trigger_type=trigger_type),
                           filt, ref_price=None if rp is None else Decimal(str(rp)))
        body: Dict[str, Any] = {"app_session_id": self.asid, "market": market, "side": "buy" if is_buy else "sell",
                                "direction": direction, "type": typ, "amount": qty, "leverage": str(leverage),
                                "time_in_force": tif, "reduce_only": reduce_only, "client_order_id": coid}
        if typ.endswith("limit") or typ == "post_only":
            if price is None: raise ValueError("limit order needs price")
            body["price"] = price
        if typ.startswith("trigger"):
            body["trigger_price"] = str(trigger_price)
            if trigger_type is not None:
                body["trigger_type"] = trigger_type
        return self._req("POST", "/perpetual/order", body, idempotent=False)["order_uuid"]

    def market_entry(self, market: str, direction: str, qty: str, leverage: int, coid: str, ref_price: Optional[str] = None) -> str:
        """Open a leg at market (taker). direction long -> buy, short -> sell. coid mandatory."""
        return self.place(market, direction == "long", direction, qty, typ="market", tif="ioc", leverage=leverage, coid=coid, ref_price=ref_price)

    def target_exit(self, market: str, direction: str, qty: str, price: str, leverage: int, coid: str) -> str:
        """Resting reduce-only limit (maker) that closes the leg at the target."""
        return self.place(market, direction != "long", direction, qty, typ="limit", price=price, tif="gtc", reduce_only=True, leverage=leverage, coid=coid)

    def close_market(self, market: str, direction: str, qty: str, leverage: int, coid: str, ref_price: Optional[str] = None) -> str:
        """Engine-managed stop / timeout: reduce-only market IOC. Not blocked by the entries switch."""
        return self.place(market, direction != "long", direction, qty, typ="market", tif="ioc", reduce_only=True, leverage=leverage, coid=coid, ref_price=ref_price)

    @staticmethod
    def _oid(o: Dict[str, Any]) -> Optional[str]:
        # docs show `order_id` in list responses and `order_uuid` in create responses; accept both
        return o.get("order_id") or o.get("order_uuid")

    def cancel_verified(self, market: str, order_uuid: str, tries: int = 4, wait: float = 0.75) -> str:
        """DELETE then establish the authoritative state from the venue's order lists
        (every page). Returns one of: CANCELLED | FILLED | STILL_OPEN | UNKNOWN. A 200 on
        DELETE is never treated as proof (documented venue bug: order can stay in wait)."""
        self._guard("manage")
        for i in range(tries):
            try: self._req("DELETE", "/perpetual/order", {"app_session_id": self.asid, "market": market, "order_uuid": order_uuid}, idempotent=True)
            except YellowError as e:
                if not (e.status == 404 or "not found" in e.message.lower()):
                    if i == tries - 1: raise
            except TransportAmbiguous:
                pass
            time.sleep(wait * (i + 1))
            try:
                still_open = any(self._oid(o) == order_uuid for o in self.open_orders(market))
            except (YellowError, RuntimeError, TransportAmbiguous):
                return "UNKNOWN"
            if not still_open:
                try:
                    for o in self.orders(market):
                        if self._oid(o) == order_uuid:
                            st = str(o.get("state") or o.get("status") or "").lower()
                            if st == "filled": return "FILLED"
                            if st == "done":
                                filled = Decimal(str(o.get("fill_amount", o.get("filled_amount", "0"))))
                                return "FILLED" if filled > 0 else "CANCELLED"
                            if st in ("canceled", "cancelled", "rejected"): return "CANCELLED"
                            if st in ("pending", "wait", "open", "new", "trigger_wait", "trigger_triggered"):
                                still_open = True
                                break  # conditional orders may be visible only in history
                            return "UNKNOWN"
                except (YellowError, RuntimeError, TransportAmbiguous):
                    return "UNKNOWN"
                if not still_open:
                    return "UNKNOWN"   # not in open orders and not in history: do not assume cancelled
        return "STILL_OPEN"

    def cancel_all(self, market: Optional[str] = None) -> Dict[str, Any]:
        self._guard(); return self._req("DELETE", "/perpetual/orders", {"app_session_id": self.asid, "market": market})

    def flatten(self, market: Optional[str] = None) -> Dict[str, Any]:
        """Venue-side batch market close of every leg (or one market). Non-idempotent: one attempt."""
        self._guard("manage"); return self._req("POST", "/perpetual/positions/close", {"app_session_id": self.asid, "market": market}, idempotent=False)

    def fills_for(self, order_uuid: str, market: str, since_iso: Optional[str] = None) -> Tuple[Decimal, Decimal, Dict[str, Decimal]]:
        """(filled_qty, avg_price, fees_by_currency) from /perpetual/trades for one order,
        walking every page and de-duplicating by trade id."""
        q = p = Decimal(0); fees: Dict[str, Decimal] = {}; seen = set()
        for t in self.trades(market, since_iso):
            if self._oid(t) != order_uuid and t.get("order_uuid") != order_uuid: continue
            tid = t.get("id") or t.get("trade_id") or t.get("trade_uuid") or (t.get("order_uuid"), t.get("created_at"), t.get("amount"), t.get("price"))
            if tid in seen: continue
            seen.add(tid)
            a = Decimal(str(t["amount"])); q += a; p += a * Decimal(str(t["price"]))
            cur = str(t.get("fee_currency") or t.get("fee_asset") or "USDT")
            fees[cur] = fees.get(cur, Decimal(0)) + Decimal(str(t.get("fee") or 0))
        return q, (p / q if q else Decimal(0)), fees

# ---------------------------------------------------------------- setup (registers with Yellow — owner "go" only)
def do_setup(ip: Optional[str], expires: Optional[str]) -> None:
    addr, pk = load_or_create_wallet()
    tok = jwt_login(addr, pk); jwt = tok["access_token"]
    me = jwt_get("/auth/me", jwt, base=AUTH_BASE)
    log(f"verified wallet {addr}; auth session_id={me.get('session_id')}")
    spot = jwt_get("/spot/accounts", jwt); perp = jwt_get("/perpetual/accounts", jwt)
    log(f"spot accounts: {[a.get('app_session_id') for a in spot] if isinstance(spot, list) else spot}")
    log(f"perp accounts: {[a.get('app_session_id') for a in perp] if isinstance(perp, list) else perp}")
    asid = None
    if isinstance(perp, list) and perp: asid = perp[0]["app_session_id"]
    else:
        cand = spot[0]["app_session_id"] if isinstance(spot, list) and spot and spot[0].get("app_session_id") else me.get("session_id")
        log(f"no perp account — creating with app_session_id={cand}")
        jwt_post("/perpetual/account", jwt, {"app_session_id": cand})
        for _ in range(20):
            time.sleep(1.5)
            try: jwt_get("/perpetual/account", jwt, {"app_session_id": cand}); asid = cand; break
            except YellowError as e:
                if e.status != 404: raise
        if not asid: raise SystemExit("perp account did not appear after 30s")
    body: Dict[str, Any] = {"scopes": ["read:futures", "trade:futures"], "description": "why-yellow executor"}
    if ip: body["ip_whitelist"] = [ip]
    if expires: body["expires_at"] = expires
    k = jwt_post("/accounts/api-keys", jwt, body, base=TRADE_BASE)
    _write_secret(APIKEY_F, {"api_key": k["key"]["api_key"], "secret": k["secret"], "key_id": k["key"]["id"],
                             "app_session_id": asid, "wallet": addr, "scopes": body["scopes"], "ip_whitelist": body.get("ip_whitelist", []),
                             "created": time.time()})
    log(f"API key {k['key']['api_key']} minted (scopes {body['scopes']}, ip {body.get('ip_whitelist')}) -> {APIKEY_F}")
    c = YellowClient.from_secrets()
    acct = c.account()
    log(f"perp account OK: equity ${float(acct['total_account_equity']):.2f} available ${float(acct['available_balance']):.2f}")
    log(f"DEPOSIT ADDRESS (USDT on Ethereum mainnet only): {addr}")

# ---------------------------------------------------------------- selftest (public only)
def do_selftest(clip: float) -> int:
    ok = True
    # 1) signing vector from the docs
    body = {"market": "BTCUSDT", "side": "buy", "type": "limit", "amount": "0.5", "price": "35000"}
    c = canonical(body); exp = "amount=0.5|market=BTCUSDT|price=35000|side=buy|type=limit"
    print("canonical:", "OK" if c == exp else f"MISMATCH {c}"); ok &= c == exp
    print("prehash  :", "POST/perpetual/order1701938200" + c)
    print("bool canon:", canonical({"reduce_only": True, "a": None}))
    # 2) local keygen + EIP-191 sign + recover
    from eth_account import Account
    from eth_account.messages import encode_defunct
    addr, pk = new_wallet(); msg = "Yellow Challenge: test, Timestamp: x, Nonce: y"
    sig = sign_challenge(pk, msg)
    rec = Account.recover_message(encode_defunct(text=msg), signature=sig)
    print("eip191   :", "OK" if rec == addr else "RECOVER MISMATCH", f"sig_len={len(sig)}"); ok &= rec == addr and len(sig) == 132
    # 3) auth challenge shape (throwaway address, no verify)
    try:
        ch = _auth_post("/auth/challenge", {"wallet_address": addr})
        print("challenge:", "OK", ch["challenge"][:40] + "...", "expires", ch.get("expires_at"))
    except Exception as e: print("challenge: FAIL", e); ok = False
    # 4) public market data + liquidity for the clip
    cl = YellowClient(); ex = cl.exchange_info()
    print(f"\nexchangeInfo: {len(ex)} symbols; universe present: {all(m in ex for m in UNIVERSE)}")
    print(f"{'market':14} {'status':8} {'tick':>10} {'step':>8} {'minNot':>6} {'lev':>4} {'mk/tk bps':>10} | ${clip:.0f} clip slip buy/sell bps | depth<=10bps buy/sell $")
    for m in UNIVERSE:
        try:
            f = cl.filters(m); sb, db = cl.slippage_bps(m, clip, True); ss, ds = cl.slippage_bps(m, clip, False)
            qty = cl.round_qty(m, clip / cl.top_of_book(m)[1])
            print(f"{m:14} {f['status']:8} {str(f['tick']):>10} {str(f['step']):>8} {str(f['min_notional']):>6} {str(f['max_lev']):>4} {str(f['maker']*10000)+'/'+str(f['taker']*10000):>10} | {sb:6.1f} / {ss:6.1f} | {db:9.0f} / {ds:9.0f}   qty={qty}")
        except Exception as e: print(f"{m:14} ERR {e}"); ok = False
    k = cl.klines("BTCUSDT-PERP", "5m", 3); print(f"\nklines 5m BTC: {len(k)} rows, last close {k[-1][4]} (Yellow's own candles — can replace the Bybit warmup seed)")
    fs = cl.fee_schedule(); print("fee tiers (perp mk/tk bps):", [(t['tier'], t['perp_maker_bps'], t['perp_taker_bps']) for t in fs][:4])
    print("\nkeys on disk:", os.path.exists(APIKEY_F), "| armed:", YellowClient.from_secrets().armed())
    print("SELFTEST", "PASS" if ok else "FAIL"); return 0 if ok else 1

def do_status() -> int:
    c = YellowClient.from_secrets()
    if not c.has_keys: print("no API key on disk — run `setup` when the owner says go"); return 1
    a = c.account()
    print(json.dumps({"wallet": a.get("owner"), "app_session_id": c.asid, "equity": a["total_account_equity"], "available": a["available_balance"],
                      "locked": a["total_locked_balance"], "initial_leverages": a.get("initial_leverages", {}),
                      "positions": c.positions(), "open_orders": c.open_orders(), "armed": c.armed()}, indent=2))
    for m in UNIVERSE[:1]:
        try: print("fee rate", m, c.market_fee_rate(m))
        except Exception as e: print("fee rate", m, "ERR", e)
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("selftest", help="public endpoints + signing + local keygen (registers nothing)"); s.add_argument("--clip", type=float, default=2000)
    s = sub.add_parser("setup", help="create box wallet, verify, mint API key (REGISTERS with Yellow)")
    s.add_argument("--ip", default=None, help="IP whitelist for the key (default: this box's public IP)"); s.add_argument("--expires", default=None)
    s.add_argument("--i-mean-it", action="store_true")
    sub.add_parser("status", help="account / positions / open orders (needs keys)")
    a = ap.parse_args()
    if a.cmd == "selftest": return do_selftest(a.clip)
    if a.cmd == "status": return do_status()
    if a.cmd == "setup":
        if not a.i_mean_it: print("setup registers a wallet + API key with Yellow. Re-run with --i-mean-it when the owner has said go."); return 2
        ip = a.ip or requests.get("https://api.ipify.org", timeout=10).text.strip()
        do_setup(ip, a.expires); return 0
    return 0

if __name__ == "__main__": sys.exit(main())
