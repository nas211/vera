#!/usr/bin/env python3
"""READ-ONLY pre-start validation + the FINAL REPORT block. Never places an order (there is no
--test-trade here; the client refuses every POST unless armed, and this script never arms).
Results -> state/validation_<ts>.json."""
import json, os, sys, time, hashlib
os.environ.setdefault("BYBIT_LIVE_YELLOW", "1")
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import guard; guard.install(check_env=False)
import engine, capital
from bybit_live import from_secrets, verify_key
out = dict(ts=int(time.time() * 1000), checks={})
def check(name, ok, info): out["checks"][name] = dict(ok=bool(ok), info=info); print(("PASS" if ok else "FAIL"), name, "-", info)

# 1 signal source
check("paper_journal_readable", engine.CFG["signals"]["source"] == "bybit_paper" and os.path.isfile(engine.CFG["signals"]["journal"]), engine.CFG["signals"]["journal"])
yh = engine.yellow_health(); now = time.time() * 1000
last = yh.get("last_bar_ms") or {}; fresh = {m: (now - v) / 60000 for m, v in last.items() if v}
check("yellow_markshadow_healthy", yh.get("ok") and yh.get("connected"), json.dumps(dict(connected=yh.get("connected"), ticks=yh.get("ticks"))))
check("completed_mark_candles_recent", fresh and max(fresh.values()) < 15, f"minutes since last closed 5m bar: { {m: round(v,1) for m, v in fresh.items()} }")

# 2 strategy fidelity
strat = engine.load_strategy_cfg(); prod_sha = hashlib.sha256(open("/opt/why-yellow/yellow_paper.py", "rb").read()).hexdigest()
check("strategy_sha256_matches_production", strat["sha256"] == prod_sha == yh.get("prod_sha256"), f"engine {strat['sha256'][:16]} prod {prod_sha[:16]} mark-shadow {str(yh.get('prod_sha256'))[:16]}")
geo = {k: strat["cfg"][k] for k in ("rv_tp", "rv_sl", "pb_tp", "pb_sl", "rv_rsi_lo", "rv_rsi_hi", "pb_depth", "timeout_bars", "max_open", "clip_usd", "leverage")}
check("strategy_geometry_unchanged", geo == dict(rv_tp=0.30, rv_sl=1.60, pb_tp=0.80, pb_sl=2.00, rv_rsi_lo=30.0, rv_rsi_hi=70.0, pb_depth=2.0, timeout_bars=144, max_open=8, clip_usd=2000.0, leverage=10.0), json.dumps(geo))
check("no_strategy_env_overrides_here", not any(k.startswith("WY_") for k in os.environ), "no WY_* overrides in this process")
check("leverage_matches_strategy", float(engine.CFG["sizing"]["leverage"]) == float(strat["cfg"]["leverage"]), f"executor {engine.CFG['sizing']['leverage']}x strategy {strat['cfg']['leverage']}x")

# 3 host + arm guards
blocked = 0
for h in ("api-demo" + ".bybit.com", "api-testnet" + ".bybit.com"):
    try: guard.check_url(f"https://{h}/v5/order/create")
    except SystemExit: blocked += 1
check("demo_and_testnet_blocked", blocked == 2, "guard.check_url raises for both; urllib gate installed; sources scanned")
st = guard.arm_state(guard.ARM_FILE); check("not_armed", not st["armed"], st["reason"])
c = from_secrets(os.path.join(BASE, engine.CFG["secrets_file"]), arm_file=guard.ARM_FILE)
try: c.place_market("BTCUSDT", "Buy", "0.001", "validate-must-not-send"); sent = True
except guard.NotArmed: sent = False
check("client_refuses_orders_unarmed", not sent and c.writes == 0 and c.calls == 0, f"NotArmed raised before any network call (calls {c.calls}, writes {c.writes})")

# 4 key policy + account
info = c.key_info(); ok, problems, warnings, ks = verify_key(info, engine.CFG["key_policy"], engine.CFG["expected_uid"])
check("live_key_policy", ok, json.dumps(dict(problems=problems, warnings=warnings)))
check("live_uid_expected", ks["uid"] == engine.CFG["expected_uid"], f"uid {ks['uid']} key id {ks['key_id']} note {ks['note']} expires {ks['expires']} ({ks['days_left']} days)")
check("trading_permission", "Order" in ks["contract_trade"] and str(ks["readOnly"]) == "0", f"ContractTrade {ks['contract_trade']} readOnly {ks['readOnly']}")
check("withdrawal_permission_off", ks["withdrawal_permission"] == "OFF", f"withdrawal {ks['withdrawal_permission']}")
check("transfer_permission_off", ks["transfer_permission"] == "OFF", f"AccountTransfer OFF; SubMemberTransfer {ks['sub_member_transfer']}")
ai = c.account_info(); check("account_mode", ai.get("marginMode") in ("ISOLATED_MARGIN", "REGULAR_MARGIN"), f"marginMode {ai.get('marginMode')} unifiedMarginStatus {ai.get('unifiedMarginStatus')}")
w = engine.wallet_snapshot(c.wallet()[0])
check("live_balance_available", w["available"] > 0, f"equity ${w['equity']:.2f} available ${w['available']:.6f} USDT wallet ${w['usdt_wallet']:.6f}; each order must still pass the minimum-lot margin gate")
pos = [p for p in c.positions() if float(p.get("size") or 0) > 0]; oo = c.open_orders()
check("no_foreign_positions_or_orders", not pos and not oo, f"positions {len(pos)} open orders {len(oo)}")
levs = {}
for m in strat["markets"]:
    l = c.positions(engine.to_symbol(m)); levs[engine.to_symbol(m)] = float(l[0].get("leverage") or 0) if l else None
wrong = {k: v for k, v in levs.items() if v != float(engine.CFG["sizing"]["leverage"])}
check("one_way_mode", all(int(p.get("positionIdx", 0)) == 0 for p in c.positions("BTCUSDT")), "positionIdx 0")
check("leverage_per_symbol", True, f"{levs} -> {'all ' + str(engine.CFG['sizing']['leverage']) + 'x' if not wrong else 'to set at arm time: ' + str(wrong)}")

# 5 loculus reads the same account (read-only key)
loc = "/opt/why-loculus-paper/secrets/mainnet_api_key.json"
if os.path.exists(loc):
    li = from_secrets(loc, arm_file=guard.ARM_FILE).key_info()
    check("loculus_key_same_uid_readonly", li.get("userID") == ks["uid"] and str(li.get("readOnly")) == "1", f"loculus key id {li.get('id')} uid {li.get('userID')} readOnly {li.get('readOnly')}")

# 6 capital from CURRENT rules
cap = json.load(open(sorted([os.path.join(engine.STATE, f) for f in os.listdir(engine.STATE) if f.startswith("capital_")])[-1])) if any(f.startswith("capital_") for f in os.listdir(engine.STATE)) else None
if not cap or now - cap["ts"] > 3600_000:
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()): capital.main()
    cap = json.load(open(sorted([os.path.join(engine.STATE, f) for f in os.listdir(engine.STATE) if f.startswith("capital_")])[-1]))
check("capital_computed", bool(cap), f"tiers {cap['tiers']['minimum_theoretical_usd']}/{cap['tiers']['minimum_practical_usd']}/{cap['tiers']['recommended_tiny_live_test_usd']}")
check("adaptive_lot_sizing_enabled", engine.CFG["sizing"]["mode"] == "min_size", "minimum exchange lot, capped by available collateral; each order is rechecked")
check("only_live_host_contacted", c.hosts_contacted == {"api.bybit.com"}, f"hosts {sorted(c.hosts_contacted)} writes {c.writes}")

ready = all(v["ok"] for v in out["checks"].values())
out.update(key=ks, warnings=warnings, wallet=w, capital_tiers=cap["tiers"], worst_case=cap["worst_case"], ready=ready, strategy=dict(sha256=strat["sha256"], cfg=strat["cfg"], markets=strat["markets"]))
path = os.path.join(engine.STATE, f"validation_{out['ts']}.json"); json.dump(out, open(path, "w"), indent=1)
minpos = ", ".join(f"{r['symbol']} {r['min_qty']:g} (${r['min_notional_usd']:.2f})" for r in cap["markets"])
print("\n================ FINAL REPORT (no order has been placed) ================")
print(f"LIVE BYBIT UID:                {ks['uid']}  (key id {ks['key_id']}, note {ks['note']!r}, expires {ks['expires']}; Loculus read-only key on the same uid)")
print(f"AVAILABLE BALANCE:             ${w['available']:.2f} available / ${w['equity']:.2f} equity (USDT wallet ${w['usdt_wallet']:.2f}); margin mode {ai.get('marginMode')}")
print(f"MARKETS:                       {', '.join(engine.to_symbol(m) for m in strat['markets'])}")
print(f"MAX OPEN POSITIONS:            {strat['cfg']['max_open']}")
print(f"LEVERAGE:                      {strat['cfg']['leverage']:.0f}x (per symbol now: {levs})")
print(f"MINIMUM VALID POSITION PER MARKET: {minpos}")
print(f"MINIMUM THEORETICAL CAPITAL:   ${cap['tiers']['minimum_theoretical_usd']:.2f}")
print(f"MINIMUM PRACTICAL CAPITAL:     ${cap['tiers']['minimum_practical_usd']:.2f}")
print(f"RECOMMENDED TINY TEST CAPITAL: ${cap['tiers']['recommended_tiny_live_test_usd']:.2f}")
print(f"MAX LOSS IF ALL POSITIONS HIT SL: ${cap['worst_case']['total_loss_if_all_sl_usd']:.2f} ({cap['worst_case']['positions']} lots, ${cap['worst_case']['exposure_usd']:.2f} exposure, ${cap['worst_case']['initial_margin_usd']:.2f} margin)")
print(f"WITHDRAWAL PERMISSION:         {ks['withdrawal_permission']}")
print(f"TRADING PERMISSION:            {'ON' if 'Order' in ks['contract_trade'] and str(ks['readOnly']) == '0' else 'OFF'} (ContractTrade {ks['contract_trade']}, readOnly {ks['readOnly']}); transfer {ks['transfer_permission']}; IP restriction {'NONE' if '*' in ks['ips'] else ks['ips']}")
print(f"SYSTEM READY:                  {'YES' if ready else 'NO'}  failed: {[k for k, v in out['checks'].items() if not v['ok']] or 'none'}")
print(f"ARMED:                         {st['armed']} ({st['reason']})")
print(f"saved {path}")
