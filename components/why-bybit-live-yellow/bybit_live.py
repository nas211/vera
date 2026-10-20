"""Signed Bybit v5 client bound to the LIVE host only. Every call re-asserts the base URL through
guard.py. Every POST additionally requires the ARM gate (guard.require_armed) -> unarmed processes
can only read. The secret is loaded from the secrets file and never logged or returned."""
import json, time, hmac, hashlib, urllib.request, urllib.error, urllib.parse, threading
import guard

RECV = "5000"
WRITE_PATHS = ("/v5/order/create", "/v5/position/trading-stop", "/v5/position/set-leverage", "/v5/order/cancel")

class BybitError(Exception):
    def __init__(self, code, msg, path):
        super().__init__(f"bybit {path} -> {code} {msg}"); self.code = code; self.msg = msg; self.path = path

class LiveClient:
    def __init__(self, key, secret, base=guard.ALLOWED_BASE, timeout=15, logger=None, arm_file=None):
        self.base = guard.assert_live_base(base); self._key = key; self._secret = secret
        self.timeout = timeout; self.log = logger or (lambda *a: None); self.arm_file = arm_file
        self.hosts_contacted = set(); self.calls = 0; self.writes = 0; self.lock = threading.Lock()

    @property
    def key_masked(self): return self._key[:4] + "..." + self._key[-2:]
    def __repr__(self): return f"<LiveClient {self.base} key {self.key_masked}>"

    def _sign(self, ts, payload):
        return hmac.new(self._secret.encode(), (ts + self._key + RECV + payload).encode(), hashlib.sha256).hexdigest()

    def _req(self, method, path, params=None, body=None, auth=True, retries=2):
        guard.assert_live_base(self.base)
        if method != "GET" or path in WRITE_PATHS:
            guard.require_armed(f"{method} {path}", self.arm_file)          # hard gate: no write unless armed
        url = self.base + path
        q = urllib.parse.urlencode(params or {})
        if q: url += "?" + q
        guard.check_url(url)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if auth:
            ts = str(int(time.time() * 1000))
            headers.update({"X-BAPI-API-KEY": self._key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": RECV,
                            "X-BAPI-SIGN": self._sign(ts, q if method == "GET" else (data.decode() if data else ""))})
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with self.lock:
            self.calls += 1; self.hosts_contacted.add(urllib.parse.urlsplit(url).hostname)
            if method != "GET": self.writes += 1
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

    # ---- public ----
    def tickers(self, symbol=None):
        p = {"category": "linear"}
        if symbol: p["symbol"] = symbol
        return self._req("GET", "/v5/market/tickers", p, auth=False)["list"]
    def instruments(self, symbol=None):
        p = {"category": "linear", "limit": 1000}
        if symbol: p["symbol"] = symbol
        return self._req("GET", "/v5/market/instruments-info", p, auth=False)["list"]
    def risk_limit(self, symbol):
        return self._req("GET", "/v5/market/risk-limit", {"category": "linear", "symbol": symbol}, auth=False)["list"]
    # ---- private read ----
    def key_info(self): return self._req("GET", "/v5/user/query-api")
    def account_info(self): return self._req("GET", "/v5/account/info")
    def fee_rate(self, symbol):
        l = self._req("GET", "/v5/account/fee-rate", {"category": "linear", "symbol": symbol})["list"]; return l[0] if l else {}
    def wallet(self): return self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})["list"]
    def positions(self, symbol=None):
        p = {"category": "linear"}
        p.update({"symbol": symbol} if symbol else {"settleCoin": "USDT"})
        return self._req("GET", "/v5/position/list", p)["list"]
    def open_orders(self, symbol=None):
        p = {"category": "linear"}
        p.update({"symbol": symbol} if symbol else {"settleCoin": "USDT"})
        return self._req("GET", "/v5/order/realtime", p)["list"]
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
    # ---- private write (ARM-gated inside _req; guard re-checked) ----
    def set_leverage(self, symbol, lev):
        guard.assert_live_base(self.base)
        return self._req("POST", "/v5/position/set-leverage", body=dict(category="linear", symbol=symbol, buyLeverage=str(lev), sellLeverage=str(lev)), retries=0)
    def place_market(self, symbol, side, qty, link, take_profit=None, stop_loss=None, trigger_by="MarkPrice", reduce_only=False):
        guard.assert_live_base(self.base)
        body = dict(category="linear", symbol=symbol, side=side, orderType="Market", qty=str(qty),
                    timeInForce="IOC", positionIdx=0, orderLinkId=link, reduceOnly=bool(reduce_only))
        if take_profit is not None or stop_loss is not None:
            body.update(tpslMode="Full", tpTriggerBy=trigger_by, slTriggerBy=trigger_by, tpOrderType="Market", slOrderType="Market")
            if take_profit is not None: body["takeProfit"] = str(take_profit)
            if stop_loss is not None: body["stopLoss"] = str(stop_loss)
        return self._req("POST", "/v5/order/create", body=body, retries=0)
    def set_trading_stop(self, symbol, take_profit, stop_loss, trigger_by="MarkPrice"):
        guard.assert_live_base(self.base)
        body = dict(category="linear", symbol=symbol, tpslMode="Full", positionIdx=0,
                    takeProfit=str(take_profit), stopLoss=str(stop_loss), tpTriggerBy=trigger_by, slTriggerBy=trigger_by)
        return self._req("POST", "/v5/position/trading-stop", body=body, retries=0)

def from_secrets(path, logger=None, arm_file=None):
    d = json.load(open(path))
    k = d.get("api_key"); s = d.get("secret")
    if not k or not s: guard.die(f"secrets file {path} lacks api_key/secret")
    return LiveClient(k, s, logger=logger, arm_file=arm_file)

def verify_key(info, policy, expected_uid):
    """Pure check of /v5/user/query-api output against config.key_policy. Returns (ok, problems, warnings, summary).
    Never includes key material."""
    problems, warnings = [], []
    perms = info.get("permissions") or {}
    if int(info.get("userID") or 0) != int(expected_uid): problems.append(f"uid {info.get('userID')} != expected {expected_uid}")
    if str(info.get("readOnly")) != "0": problems.append(f"readOnly={info.get('readOnly')} (needs a Read-Write key)")
    ct = perms.get("ContractTrade") or []
    for need in policy.get("require_contract_trade", []):
        if need not in ct: problems.append(f"ContractTrade lacks {need}")
    flat = {p for v in perms.values() for p in (v or [])}
    for bad in policy.get("forbid_any_permission", []):
        if bad in flat: problems.append(f"permission {bad} present (must be OFF)")
    wallet = perms.get("Wallet") or []
    for bad in policy.get("forbid_wallet", []):
        if bad in wallet: problems.append(f"Wallet:{bad} present (transfer must be OFF)")
    for w in policy.get("warn_wallet", []):
        if w in wallet: warnings.append(f"Wallet:{w} present (sub-account transfer; harmless without sub-accounts, prefer OFF)")
    ips = info.get("ips") or []
    if policy.get("warn_if_ip_unrestricted") and (not ips or "*" in ips): warnings.append("key has no IP restriction (ips=*)")
    summary = dict(uid=info.get("userID"), key_id=info.get("id"), note=info.get("note"), readOnly=info.get("readOnly"), contract_trade=ct,
                   wallet=wallet, withdrawal_permission="OFF" if not any(b in flat for b in policy.get("forbid_any_permission", [])) else "ON",
                   transfer_permission="OFF" if "AccountTransfer" not in wallet else "ON", sub_member_transfer="ON" if "SubMemberTransfer" in wallet else "OFF",
                   ips=ips, created=info.get("createdAt"), expires=info.get("expiredAt"), days_left=info.get("deadlineDay"), uta=info.get("uta"), is_master=info.get("isMaster"))
    return (not problems), problems, warnings, summary
