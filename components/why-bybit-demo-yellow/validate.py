#!/usr/bin/env python3
"""Pre-start validation. `python3 validate.py` runs checks 1-6; `--test-trade` also runs the
ONE controlled demo trade (smallest ETHUSDT lot, exchange-side TP/SL, then reduce-only close)
and proves it in the demo account; results are written to state/validation_<ts>.json."""
import json, os, sys, time, hashlib
os.environ.setdefault("BYBIT_DEMO_ONLY", "1")
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import guard; guard.install()
import engine
from bybit_demo import from_secrets, BybitError
out = dict(ts=int(time.time() * 1000), checks={})
def check(name, ok, info): out["checks"][name] = dict(ok=bool(ok), info=info); print(("PASS" if ok else "FAIL"), name, "-", info)

# 1 + 2 yellow mark-shadow health and completed candles
yh = engine.yellow_health(); now = time.time() * 1000
last = yh.get("last_bar_ms") or {}
fresh = {m: (now - v) / 60000 for m, v in last.items() if v}
check("yellow_markshadow_healthy", yh.get("ok") and yh.get("connected"), json.dumps(dict(connected=yh.get("connected"), ticks=yh.get("ticks"))))
check("completed_mark_candles_recent", fresh and max(fresh.values()) < 15, f"minutes since last closed 5m bar per market: { {m: round(v,1) for m, v in fresh.items()} }")
mc = engine.CFG["signals"]["mark_candles_dir"]; n = {f[:-6]: sum(1 for _ in open(os.path.join(mc, f))) for f in os.listdir(mc)} if os.path.isdir(mc) else {}
check("mark_candle_files", bool(n) and min(n.values()) > 0, json.dumps(n))

# 3 strategy parameters identical to the production Yellow strategy
strat = engine.load_strategy_cfg()
prod_sha = hashlib.sha256(open("/opt/why-yellow/yellow_paper.py", "rb").read()).hexdigest()
check("strategy_sha256_matches_production", strat["sha256"] == prod_sha == yh.get("prod_sha256"), f"engine {strat['sha256'][:16]} prod file {prod_sha[:16]} mark-shadow {str(yh.get('prod_sha256'))[:16]}")
geo = {k: strat["cfg"][k] for k in ("rv_tp", "rv_sl", "pb_tp", "pb_sl", "rv_rsi_lo", "rv_rsi_hi", "pb_depth", "timeout_bars", "max_open", "clip_usd", "leverage")}
check("strategy_geometry", geo == dict(rv_tp=0.30, rv_sl=1.60, pb_tp=0.80, pb_sl=2.00, rv_rsi_lo=30.0, rv_rsi_hi=70.0, pb_depth=2.0, timeout_bars=144, max_open=8, clip_usd=2000.0, leverage=10.0), json.dumps(geo))
check("no_strategy_env_overrides_here", not any(k.startswith("WY_") for k in os.environ), "engine process has no WY_* overrides; CFG comes from the production unit env")
out["strategy"] = dict(sha256=strat["sha256"], cfg=strat["cfg"], env=strat["env"], markets=strat["markets"])

# 4 + 5 demo auth + balance
c = from_secrets(os.path.join(BASE, engine.CFG["secrets_file"]))
ki = c.key_info(); w = c.wallet()[0]
check("demo_auth", ki.get("userID") is not None, f"host {c.base} uid {ki.get('userID')} key {c.key[:4]}...{c.key[-2:]} readOnly {ki.get('readOnly')} perms {list((ki.get('permissions') or {}).get('ContractTrade', []))}")
check("demo_balance", float(w.get("totalEquity") or 0) > 0, f"equity {w.get('totalEquity')} USDT available {w.get('totalAvailableBalance')}")
out["demo"] = dict(uid=ki.get("userID"), key_masked=c.key[:4] + "..." + c.key[-2:], readOnly=ki.get("readOnly"), equity=w.get("totalEquity"))

# 6 zero access to mainnet order endpoints
blocked = 0
for h in ("api" + ".bybit.com", "api-testnet" + ".bybit.com"):
    try: guard.check_url(f"https://{h}/v5/order/create"); ok = False
    except SystemExit: blocked += 1
check("mainnet_and_testnet_blocked", blocked == 2, "guard.check_url raises SystemExit for both hosts; urllib gate installed; sources scanned")
check("only_demo_host_contacted", c.hosts_contacted == {"api-demo.bybit.com"}, f"hosts so far {sorted(c.hosts_contacted)}")

# 7-10 controlled trade
if "--test-trade" in sys.argv:
    sym = "ETHUSDT"; ins = [i for i in c.instruments(sym)][0]; step = float(ins["lotSizeFilter"]["qtyStep"]); mq = float(ins["lotSizeFilter"]["minOrderQty"]); tick = float(ins["priceFilter"]["tickSize"])
    t = c.tickers(sym)[0]; mark = float(t["markPrice"]); qty = engine.fmt_step(mq, step)
    tp = engine.fmt_tick(mark * 1.008, tick); sl = engine.fmt_tick(mark * 0.98, tick); link = "wyd-selftest-" + str(int(time.time()))[-8:]
    print(f"TEST TRADE: {sym} Buy {qty} (~${float(qty)*mark:.2f}) mark {mark} tp {tp} sl {sl} link {link}")
    try:
        r = c.place_market(sym, "Buy", qty, link, take_profit=tp, stop_loss=sl); oid = r.get("orderId")
        time.sleep(2); o = c.order_by_link(link); pos = [p for p in c.positions(sym) if float(p.get("size") or 0) > 0]
        check("test_trade_filled", o and o.get("orderStatus") == "Filled", f"orderId {oid} status {o.get('orderStatus') if o else None} avg {o.get('avgPrice') if o else None}")
        check("test_position_visible_in_demo", bool(pos), f"{[(p['side'], p['size'], 'avg', p['avgPrice'], 'tp', p.get('takeProfit'), 'sl', p.get('stopLoss')) for p in pos]}")
        r2 = c.place_market(sym, "Sell", pos[0]["size"] if pos else qty, link + "-x", reduce_only=True); time.sleep(2)
        pos2 = [p for p in c.positions(sym) if float(p.get("size") or 0) > 0]; cp = c.closed_pnl(sym, int(time.time() * 1000) - 120_000)
        check("test_trade_closed", not pos2 and cp, f"close orderId {r2.get('orderId')} closedPnl {cp[0].get('closedPnl') if cp else None} entry {cp[0].get('avgEntryPrice') if cp else None} exit {cp[0].get('avgExitPrice') if cp else None}")
        out["test_trade"] = dict(symbol=sym, qty=qty, entry_order=oid, close_order=r2.get("orderId"), closed_pnl=cp[0] if cp else None)
    except BybitError as e:
        check("test_trade_filled", False, f"order refused: {e.code} {e.msg}"); out["test_trade"] = dict(error=str(e))
    check("only_demo_host_contacted_after_trade", c.hosts_contacted == {"api-demo.bybit.com"}, f"hosts {sorted(c.hosts_contacted)} calls {c.calls}")

out["all_ok"] = all(v["ok"] for v in out["checks"].values())
os.makedirs(engine.STATE, exist_ok=True); p = os.path.join(engine.STATE, f"validation_{out['ts']}.json"); json.dump(out, open(p, "w"), indent=1)
print("ALL PASS" if out["all_ok"] else "SOME FAILED", "->", p); sys.exit(0 if out["all_ok"] else 1)
