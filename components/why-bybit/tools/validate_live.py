#!/usr/bin/env python3
"""Live-adapter validation against the REAL account. READ-ONLY: no order is created. Secrets are never printed."""
import json, os, subprocess, sys, time
sys.path.insert(0, "/opt/why-bybit")
from decimal import Decimal
import bybit_exec as be
R = []
def check(name, ok, detail=""): R.append((name, ok)); print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
c = be.BybitClient.from_secrets()

print("--- 1. authentication / clock / account ---")
skew = c.server_time_ms() - int(time.time() * 1000); check("server clock within 2s", abs(skew) < 2000, f"{skew} ms")
info = c.api_info(); perms = json.dumps(info.get("permissions", {})).lower()
check("authenticated REST (signed GET) accepted", True, f"key type {info.get('type')}, readOnly={info.get('readOnly')}")
check("ContractTrade permission present", bool(info["permissions"].get("ContractTrade")), str(info["permissions"].get("ContractTrade")))
check("NO withdrawal permission", "withdraw" not in perms)
check("IP restriction is a whitelist (not '*')", info.get("ips") not in (["*"], [], None), f"ips={info.get('ips')}")
acct = c.account_info(); check("account margin mode supported by adapter", acct.get("marginMode") in ("REGULAR_MARGIN", "ISOLATED_MARGIN"), acct.get("marginMode"))
w = c.wallet(); usdt = next((float(x["walletBalance"]) for x in w["coin"] if x["coin"] == "USDT"), 0)
check("balance readable", True, f"totalEquity {w['totalEquity']} | USDT {usdt:.2f}")
check("positions readable", isinstance(c.positions(), list), f"{len([p for p in c.positions() if float(p.get('size') or 0) > 0])} open")
check("open orders readable", isinstance(c.open_orders(), list), f"{len(c.open_orders())} open")
check("recent fills readable", isinstance(c._req("GET", "/v5/execution/list", {"category": "linear", "limit": 5})["list"], list))
check("closed-PnL readable", isinstance(c.closed_pnl("BTCUSDT", 5), list))
check("fee-rate endpoint readable", c.fee_rate("BTCUSDT").get("takerFeeRate") is not None, json.dumps({k: c.fee_rate("BTCUSDT")[k] for k in ("makerFeeRate", "takerFeeRate")}))
try: c._req("GET", "/v5/account/transaction-log", {"accountType": "UNIFIED", "category": "linear", "type": "SETTLEMENT", "limit": 5}); check("transaction-log (funding settlements) readable", True)
except Exception as e: check("transaction-log (funding settlements) readable", False, str(e)[:100])

print("\n--- 2. instrument constraints @ proposed $50 clip, 5x ---")
print(f"{'symbol':9} {'minQty':>7} {'step':>7} {'tick':>8} {'minNotional$':>12} {'maxLev':>6} | {'price':>10} {'qty@$50':>8} {'notional$':>9} {'margin$':>8} {'planned worst loss$':>19} {'action':>12}")
for m, sym in be.UNIVERSE.items():
    f = c.filters(sym); px = float(c.orderbook(sym, 1)["a"][0][0]); qty = Decimal(c.round_qty(sym, 50 / px))
    lot = max(f["min_qty"], (f["min_notional"] / Decimal(str(px)) / f["step"]).to_integral_value("ROUND_CEILING") * f["step"])
    act = "as clip"; 
    if qty < lot:
        planned = float(lot) * px * (0.02 + 0.0015); act = "MIN-LOT bump" if planned <= 2.0 else "REFUSED (cap)"; qty = lot
    n = float(qty) * px; planned = n * (0.02 + 0.0015)
    print(f"{sym:9} {str(f['min_qty']):>7} {str(f['step']):>7} {str(f['tick']):>8} {str(f['min_notional']):>12} {str(f['max_lev']):>6} | {px:10.4f} {str(qty):>8} {n:9.2f} {n/5:8.2f} {planned:19.2f} {act:>12}")
check("5x leverage within every symbol's max", all(c.filters(s)["max_lev"] >= 5 for s in be.UNIVERSE.values()))

print("\n--- 3. preflight (strict, then with IP override; private WS auth) — no orders ---")
env = dict(os.environ, BYBIT_ARMED="1")
def pf(extra):
    code = ("import sys; sys.path.insert(0,'/opt/why-bybit'); import bybit_exec as be\n"
            "ex=be.LiveExecutor()\n"
            "try: print('PREFLIGHT OK', ex.preflight())\n"
            "except Exception as e: print('PREFLIGHT REFUSED:', type(e).__name__, str(e)[:230])\n")
    return subprocess.run([sys.executable, "-c", code], env=dict(env, **extra), capture_output=True, text=True, timeout=60).stdout.strip()
o1 = pf({}); print("   strict   :", o1); check("preflight refuses an unrestricted-IP key (guard works)", "PREFLIGHT REFUSED" in o1 and "IP-restricted" in o1)
o2 = pf({"BYBIT_REQUIRE_IP_WHITELIST": "0"}); print("   override :", o2)
check("rest of preflight passes when IP rule is waived (margin mode, one-way, symbols, private WS auth)", o2.startswith("PREFLIGHT OK"))
check("BYBIT_ARMED is NOT set in the paper service", "BYBIT_ARMED" not in open("/etc/systemd/system/why-bybit.service").read().replace("BYBIT_ARMED=1 + keys", ""))

print("\n--- 4. POST signing verified without creating anything ---")
try: c._req("POST", "/v5/order/cancel", {"category": "linear", "symbol": "BTCUSDT", "orderLinkId": "wbVALIDATE-nonexistent-0000"}, tries=1); check("signed POST accepted", False, "unexpected success")
except be.BybitError as e: check("signed POST accepted by auth layer (cancel of a NON-EXISTENT order rejected on business logic, not signature)", e.code not in (10003, 10004, 10005) and e.http != 401, f"retCode {e.code}: {e.msg[:60]}")

print("\n--- 5. reconciliation (exchange-authoritative) ---")
ex = be.LiveExecutor(); real_pos = ex.c.positions
try: r = ex.reconcile({}); check("REAL exchange vs empty local state -> clean", r["clean"], json.dumps(r))
except Exception as e: check("REAL exchange vs empty local state -> clean", False, str(e)[:120])
def fake(sym, side, size): return {"symbol": sym, "side": side, "size": size, "positionIdx": 0}
ex.c.positions = lambda *a, **k: [fake("BTCUSDT", "Buy", "0.001")]
try: ex.reconcile({}); check("exchange position unknown to engine (ORPHAN) -> refuse to start", False)
except be.ReconcileError as e: check("exchange position unknown to engine (ORPHAN) -> refuse to start", "ORPHAN" in str(e), str(e)[:90])
lp = {"BTCUSDT-PERP": {"side": "long", "live": {"qty": 0.001}}}
try: r = ex.reconcile(lp); check("engine position matches exchange position -> recognised, clean", r["clean"], json.dumps(r))
except Exception as e: check("engine position matches exchange position -> recognised, clean", False, str(e)[:120])
try: ex.reconcile({"BTCUSDT-PERP": {"side": "short", "live": {"qty": 0.001}}}); check("side mismatch -> refuse", False)
except be.ReconcileError as e: check("side mismatch -> refuse", "side mismatch" in str(e))
try: ex.reconcile({"BTCUSDT-PERP": {"side": "long", "live": {"qty": 0.005}}}); check("size mismatch -> refuse", False)
except be.ReconcileError as e: check("size mismatch -> refuse", "size mismatch" in str(e))
ex.c.positions = real_pos
try: ex.reconcile({"ETHUSDT-PERP": {"side": "long", "live": {"qty": 0.01}}}); check("engine position with none on exchange (GHOST) -> refuse", False)
except be.ReconcileError as e: check("engine position with none on exchange (GHOST) -> refuse", "GHOST" in str(e), str(e)[:90])
real_oo = ex.c.open_orders; ex.c.open_orders = lambda *a, **k: [{"symbol": "SOLUSDT", "side": "Buy", "orderType": "Limit", "orderLinkId": "x", "reduceOnly": False}]
try: ex.reconcile({}); check("stray non-reduce-only open order -> refuse", False)
except be.ReconcileError as e: check("stray non-reduce-only open order -> refuse", "unexpected open order" in str(e))
ex.c.open_orders = real_oo

print("\n--- 6. duplicate-order protection / restart safety ---")
l1 = be.link_id("o", "WY-PULLBACK", "ETHUSDT-PERP", "long", 1789766100000)
l2 = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0,'/opt/why-bybit'); import bybit_exec as be; print(be.link_id('o','WY-PULLBACK','ETHUSDT-PERP','long',1789766100000))"], capture_output=True, text=True).stdout.strip()
check("same signal -> same orderLinkId across separate processes (restart-safe)", l1 == l2, l1)
check("different bar/side/strategy -> different orderLinkId", len({be.link_id("o", "WY-PULLBACK", "ETHUSDT-PERP", "long", 1789766400000), be.link_id("o", "WY-PULLBACK", "ETHUSDT-PERP", "short", 1789766100000), be.link_id("o", "WY-REVERT", "ETHUSDT-PERP", "long", 1789766100000), l1}) == 4)
check("orderLinkId within Bybit's 36-char limit", len(l1) <= 36, f"len {len(l1)}")
ex2 = be.LiveExecutor(); real_env = os.environ.pop("BYBIT_ARMED", None)
try: ex2.c.create("BTCUSDT", "Buy", "0.001", l1); check("create() refuses when BYBIT_ARMED unset", False, "IT ATTEMPTED AN ORDER")
except be.NotArmed: check("create() refuses when BYBIT_ARMED unset (no request sent)", True)
os.environ["BYBIT_ARMED"] = "1"; sent = []
def fake_req_dup(method, path, params=None, auth=True, tries=3):
    sent.append((method, path))
    if path == "/v5/order/create": raise be.BybitError(110072, "OrderLinkedID is duplicate", 200)
    if path in ("/v5/order/realtime", "/v5/order/history"): return {"list": [{"orderId": "EXISTING-123", "orderLinkId": l1}]}
    raise AssertionError(path)
ex2.c._req = fake_req_dup; res = ex2.c.create("BTCUSDT", "Buy", "0.001", l1)
check("duplicate link id (retCode 110072) -> adopts the existing order, sends NO second order", res.get("adopted") and res["orderId"] == "EXISTING-123" and sum(1 for s in sent if s[1] == "/v5/order/create") == 1, str(res))
sent.clear()
def fake_req_net(method, path, params=None, auth=True, tries=3):
    sent.append((method, path))
    if path == "/v5/order/create": raise RuntimeError("POST failed: connection reset")
    if path in ("/v5/order/realtime", "/v5/order/history"): return {"list": [{"orderId": "LANDED-9", "orderLinkId": l1}]}
    raise AssertionError(path)
ex2.c._req = fake_req_net; res = ex2.c.create("BTCUSDT", "Buy", "0.001", l1)
check("transport failure after the order landed -> adopts it (no blind retry, no duplicate)", res.get("adopted") and sum(1 for s in sent if s[1] == "/v5/order/create") == 1, str(res))
if real_env is None: os.environ.pop("BYBIT_ARMED", None)
ok = all(x[1] for x in R); print(f"\nSUMMARY: {sum(1 for x in R if x[1])}/{len(R)} passed; failed: {[x[0] for x in R if not x[1]]}")
