"""Offline tests with a fake DEMO client. No network. Covers: long fill, short fill, TP, SL,
TIMEOUT, idempotent signal, restart recovery, host guard / no-mainnet-order path."""
import json, os, sys, tempfile, unittest, importlib
os.environ["BYBIT_DEMO_ONLY"] = "1"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
import guard, engine
from bybit_demo import BybitError, DemoClient

STRAT = dict(cfg=dict(rv_tp=0.30, rv_sl=1.60, pb_tp=0.80, pb_sl=2.00, max_open=8, timeout_bars=144, clip_usd=2000, taker_bps=1.5, maker_bps=0.0, spread_bps=2.0), sha256="deadbeef" * 8, env={}, markets=[])

class Fake:
    base = guard.ALLOWED_BASE
    def __init__(self, mark=100.0):
        self.mark = mark; self.orders = []; self.pos = {}; self.execs = {}; self.cpnl = {}; self.hosts_contacted = {"api-demo.bybit.com"}; self.calls = 0; self.key = "FAKEKEY12345678901"
        self.stops = []
    def instruments(self): return [dict(symbol=s, status="Trading", lotSizeFilter=dict(qtyStep="0.01", minOrderQty="0.01"), priceFilter=dict(tickSize="0.01")) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    def tickers(self, symbol=None): return [dict(symbol=symbol, markPrice=str(self.mark), bid1Price=str(self.mark - 0.01), ask1Price=str(self.mark + 0.01), lastPrice=str(self.mark))]
    def place_market(self, symbol, side, qty, link, take_profit=None, stop_loss=None, trigger_by="MarkPrice", reduce_only=False):
        if any(o["link"] == link for o in self.orders): raise BybitError(110072, "OrderLinkedID is duplicate", "/v5/order/create")
        oid = f"oid{len(self.orders)+1}"; fill = self.mark + (0.02 if side == "Buy" else -0.02)
        self.orders.append(dict(link=link, id=oid, symbol=symbol, side=side, qty=qty, tp=take_profit, sl=stop_loss, reduce_only=reduce_only, fill=fill))
        if reduce_only:
            self.close(symbol, fill, link=link, oid=oid, sot="")
        else:
            self.pos[symbol] = dict(symbol=symbol, size=qty, avgPrice=str(fill), side=side, unrealisedPnl="0", markPrice=str(self.mark), takeProfit=take_profit, stopLoss=stop_loss, leverage="10")
            self.execs.setdefault(symbol, []).append(dict(orderLinkId=link, orderId=oid, closedSize="0", execFee=str(float(qty) * fill * 0.00055), execPrice=str(fill), execQty=qty, execTime="1000", stopOrderType=""))
        return dict(orderId=oid, orderLinkId=link)
    def close(self, symbol, px, link="", oid="x", sot="TakeProfit"):
        p = self.pos.pop(symbol); qty = float(p["size"]); entry = float(p["avgPrice"]); sg = 1 if p["side"] == "Buy" else -1
        fee = qty * px * 0.00055; gross = sg * (px - entry) * qty
        self.execs.setdefault(symbol, []).append(dict(orderLinkId=link, orderId=oid, closedSize=p["size"], execFee=str(fee), execPrice=str(px), execQty=p["size"], execTime="2000", stopOrderType=sot))
        self.cpnl[symbol] = [dict(avgEntryPrice=str(entry), avgExitPrice=str(px), qty=p["size"], closedPnl=str(gross - fee - qty * entry * 0.00055), updatedTime="2000")]
    def order_by_link(self, link):
        for o in self.orders:
            if o["link"] == link: return dict(orderStatus="Filled", avgPrice=str(o["fill"]), cumExecQty=o["qty"], cumExecFee=str(float(o["qty"]) * o["fill"] * 0.00055), orderId=o["id"], updatedTime="1000")
    def set_trading_stop(self, symbol, tp, sl, trigger_by="MarkPrice"): self.stops.append((symbol, tp, sl))
    def positions(self, symbol=None): return list(self.pos.values())
    def executions(self, symbol, start_ms=None, limit=50): return self.execs.get(symbol, [])
    def closed_pnl(self, symbol, start_ms=None, limit=20): return self.cpnl.get(symbol, [])
    def wallet(self): return [dict(totalEquity="10000", totalWalletBalance="10000", totalAvailableBalance="10000", totalPerpUPL="0")]

def sig(market="ETHUSDT-PERP", side="long", strat="WY-PULLBACK", ts=1_790_000_000_000, bar=1_789_999_700_000, entry=100.0):
    tp, sl = (0.30, 1.60) if strat == "WY-REVERT" else (0.80, 2.00); sg = 1 if side == "long" else -1
    return dict(event="OPEN", ts=ts, bar_ms=bar, market=market, side=side, strategy=strat, entry=entry, target=entry * (1 + sg * tp / 100), stop=entry * (1 - sg * sl / 100), notional=2000, detail={})

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wyd-test-"); self.clock = {"now": 1_790_000_000_000 / 1000 + 5}
        self.fake = Fake(); self.b = engine.Bridge(self.fake, STRAT, clock=lambda: self.clock["now"], state_dir=self.d, logger=lambda *a: None); self.b.load_instruments()
    def test_long_fill_geometry(self):
        p = self.b.on_signal(sig()); o = self.fake.orders[0]
        self.assertEqual(o["side"], "Buy"); self.assertEqual(p["decision"], "FILLED"); self.assertEqual(o["qty"], "20.00")
        self.assertAlmostEqual(float(p["tp"]), p["fill"]["avg_price"] * 1.008, places=2); self.assertAlmostEqual(float(p["sl"]), p["fill"]["avg_price"] * 0.98, places=2)
        self.assertEqual(o["tp"], "100.80"); self.assertEqual(o["sl"], "98.00")       # submitted from the bybit mark
        self.assertTrue(self.fake.stops, "TP/SL re-set from real fill"); self.assertIn("ETHUSDT", self.b.open)
    def test_short_fill_geometry(self):
        p = self.b.on_signal(sig(side="short", strat="WY-REVERT")); o = self.fake.orders[0]
        self.assertEqual(o["side"], "Sell"); self.assertAlmostEqual(float(p["tp"]), p["fill"]["avg_price"] * (1 - 0.003), places=2); self.assertAlmostEqual(float(p["sl"]), p["fill"]["avg_price"] * 1.016, places=2)
    def test_tp_exit_records_trade(self):
        self.b.on_signal(sig()); self.fake.close("ETHUSDT", 100.82, sot="TakeProfit"); self.b.poll()
        t = engine.read_jsonl(self.b.f_trades)[0]
        self.assertEqual(t["exit_reason"], "TARGET"); self.assertGreater(t["net_pnl_usd"], 0); self.assertEqual(self.b.open, {})
        self.assertAlmostEqual(t["gross_pnl_usd"], (100.82 - 100.02) * 20, places=4); self.assertEqual(t["fees_usd"], round(t["fee_in_usd"] + t["fee_out_usd"], 6))
    def test_sl_exit_records_trade(self):
        self.b.on_signal(sig(side="short", strat="WY-REVERT")); self.fake.close("ETHUSDT", 101.6, sot="StopLoss"); self.b.poll()
        t = engine.read_jsonl(self.b.f_trades)[0]; self.assertEqual(t["exit_reason"], "STOP"); self.assertLess(t["net_pnl_usd"], 0)
    def test_timeout_exit(self):
        self.b.on_signal(sig()); self.clock["now"] += 144 * 300 + 1; self.b.poll()
        self.assertTrue(any(o["reduce_only"] for o in self.fake.orders)); self.b.poll()
        t = engine.read_jsonl(self.b.f_trades)[0]; self.assertEqual(t["exit_reason"], "TIMEOUT")
    def test_idempotent_signal(self):
        self.b.on_signal(sig()); self.b.on_signal(sig()); self.b.on_signal(dict(sig(), ts=sig()["ts"] + 3))
        self.assertEqual(len(self.fake.orders), 1); self.assertEqual(len(self.b.seen), 1)
    def test_stale_and_busy_and_full(self):
        self.b.on_signal(sig(ts=1_790_000_000_000 - 500_000, bar=1_789_999_400_000)); self.assertEqual(list(self.b.seen.values())[0]["decision"], "SKIP"); self.assertEqual(len(self.fake.orders), 0)
        self.b.on_signal(sig()); self.b.on_signal(sig(strat="WY-REVERT", bar=sig()["bar_ms"] + 300000)); self.assertEqual(list(self.b.seen.values())[-1]["reason"], "symbol_busy")
        self.b.max_open = 1; self.b.on_signal(sig(market="SOLUSDT-PERP")); self.assertEqual(list(self.b.seen.values())[-1]["reason"], "book_full")
    def test_geometry_mismatch_refused(self):
        s = sig(); s["target"] = s["entry"] * 1.05
        self.b.on_signal(s); self.assertEqual(list(self.b.seen.values())[0]["reason"], "geometry_mismatch"); self.assertEqual(self.fake.orders, [])
    def test_restart_recovery_no_duplicate(self):
        self.b.on_signal(sig())
        b2 = engine.Bridge(self.fake, STRAT, clock=lambda: self.clock["now"], state_dir=self.d, logger=lambda *a: None); b2.load_instruments()
        b2.on_signal(sig()); self.assertEqual(len(self.fake.orders), 1); self.assertIn("ETHUSDT", b2.open)
        self.fake.close("ETHUSDT", 100.82); b2.poll(); self.assertEqual(len(engine.read_jsonl(b2.f_trades)), 1)
    def test_restart_after_crash_between_persist_and_send(self):
        # simulate: signal persisted as SUBMITTED, process died before/after the request; on restart it must not send again
        class Boom(Fake):
            def place_market(self, *a, **k): raise BybitError(110072, "OrderLinkedID is duplicate", "/v5/order/create")
        f = Boom(); b = engine.Bridge(f, STRAT, clock=lambda: self.clock["now"], state_dir=tempfile.mkdtemp(), logger=lambda *a: None); b.load_instruments()
        f.orders.append(dict(link="wyd-" + engine.signal_id(sig()), id="oidX", symbol="ETHUSDT", side="Buy", qty="20.00", tp=None, sl=None, reduce_only=False, fill=100.02))
        p = b.on_signal(sig()); self.assertEqual(p["decision"], "FILLED"); self.assertEqual(len(f.orders), 1)
    def test_guard_blocks_mainnet_and_testnet(self):
        for u in ("https://api" + ".bybit.com/v5/order/create", "https://api-testnet" + ".bybit.com/v5/order/create", "https://example.com/x"):
            with self.assertRaises(SystemExit): guard.check_url(u)
        self.assertEqual(guard.check_url("https://api-demo.bybit.com/v5/market/time"), "https://api-demo.bybit.com/v5/market/time")
        with self.assertRaises(SystemExit): DemoClient("k", "s", base="https://api" + ".bybit.com")
        with self.assertRaises(SystemExit): guard.assert_demo_base("https://api-testnet" + ".bybit.com")
        c = DemoClient("k", "s"); c.base = "https://api" + ".bybit.com"          # tampered after construction
        with self.assertRaises(SystemExit): c.place_market("BTCUSDT", "Buy", "0.001", "x")
        import urllib.request
        with self.assertRaises(SystemExit): urllib.request.urlopen("https://api" + ".bybit.com/v5/market/time")
    def test_guard_env_and_source_scan(self):
        os.environ.pop("BYBIT_DEMO_ONLY", None)
        with self.assertRaises(SystemExit): guard.require_env()
        os.environ["BYBIT_DEMO_ONLY"] = "1"; guard.scan_sources()
        bad = os.path.join(BASE, "zz_tmp_bad.py"); open(bad, "w").write("X='https://api" + ".bybit.com'\n")
        try:
            with self.assertRaises(SystemExit): guard.scan_sources()
        finally: os.remove(bad)
    def test_no_order_function_targets_other_host(self):
        import inspect, bybit_demo
        src = inspect.getsource(bybit_demo)
        self.assertIn("guard.assert_demo_base(self.base)", inspect.getsource(bybit_demo.DemoClient.place_market))
        self.assertNotIn("api.bybit" + ".com", src); self.assertNotIn("testnet", src)

if __name__ == "__main__":
    unittest.main(verbosity=2)

class Y(unittest.TestCase):
    """Yellow-adjusted accounting: exact production close_position arithmetic + mark-shadow CLOSE reconciliation."""
    CFG = dict(STRAT["cfg"], taker_bps=1.5, maker_bps=0.0, spread_bps=2.0)
    def test_formula_matches_production_close_position(self):
        # production: slip = exit*spread/1e4; px = exit - slip (long); pnl = notional*pct - (fee_in + fee_out)
        px, gross, fees, net = engine.yellow_pnl(self.CFG, "long", 100.02, 100.82016, "TARGET", 2000)
        self.assertAlmostEqual(px, 100.82016 * (1 - 2e-4), 9); self.assertAlmostEqual(fees, 2000 * 1.5e-4 + 0, 9)
        self.assertAlmostEqual(net, 2000 * (px - 100.02) / 100.02 - fees, 9)
        px, gross, fees, net = engine.yellow_pnl(self.CFG, "short", 99.98, 101.5, "STOP", 2000)
        self.assertAlmostEqual(px, 101.5 * (1 + 2e-4), 9); self.assertAlmostEqual(fees, 2000 * 3e-4, 9); self.assertLess(net, 0)
    def test_final_from_shadow_close_and_reconcile(self):
        d = tempfile.mkdtemp(); j = os.path.join(d, "journal.jsonl"); cfg = json.loads(json.dumps(engine.CFG)); cfg["signals"]["journal"] = j
        strat = dict(STRAT, cfg=self.CFG); fake = Fake(); clock = {"now": 1_790_000_000_000 / 1000 + 5}
        b = engine.Bridge(fake, strat, clock=lambda: clock["now"], state_dir=d, logger=lambda *a: None, cfg=cfg); b.load_instruments(); b.yellow_marks = {"ETHUSDT-PERP": 100.5}
        b.on_signal(sig()); fake.close("ETHUSDT", 100.82, sot="TakeProfit"); b.poll()
        t = engine.read_jsonl(b.f_trades)[0]; ya = t["yellow_adjusted"]
        self.assertEqual(ya["status"], "pending"); self.assertEqual(ya["source"], "estimate_from_demo_exit_reason"); self.assertEqual(ya["fee_model"], "yellow")
        self.assertEqual(t["fees_usd"], round(t["fee_in_usd"] + t["fee_out_usd"], 6))          # raw demo block untouched
        # now the mark-shadow closes the same signal at its own Yellow exit
        s = sig(); open(j, "a").write(json.dumps(dict(event="CLOSE", ts=s["ts"] + 900_000, market=s["market"], side="long", strategy=s["strategy"], entry=s["entry"], exit=100.7, reason="TARGET", pnl_usd=13.3, fees_usd=0.3, hold_min=15)) + "\n")
        self.assertTrue(b.reconcile_yellow()); t = engine.read_jsonl(b.f_trades)[0]; ya = t["yellow_adjusted"]
        self.assertEqual(ya["status"], "final"); self.assertEqual(ya["net_pnl_usd"], 13.3); self.assertEqual(ya["exit"], 100.7); self.assertEqual(ya["source"], "yellow_mark_shadow_close")
        st = b.status(); self.assertEqual(st["primary"], "yellow_adjusted"); self.assertEqual(st["yellow_adjusted"]["overall"]["net_pnl_usd"], 13.3); self.assertEqual(st["yellow_adjusted"]["final_trades"], 1)
        self.assertAlmostEqual(st["raw_bybit_demo"]["overall"]["net_pnl_usd"], t["net_pnl_usd"], 4); self.assertNotEqual(st["raw_bybit_demo"]["overall"]["net_pnl_usd"], 13.3)
    def test_open_position_yellow_unrealized(self):
        d = tempfile.mkdtemp(); strat = dict(STRAT, cfg=self.CFG); fake = Fake(); clock = {"now": 1_790_000_000_000 / 1000 + 5}
        b = engine.Bridge(fake, strat, clock=lambda: clock["now"], state_dir=d, logger=lambda *a: None); b.load_instruments(); b.yellow_marks = {"ETHUSDT-PERP": 101.0}
        b.on_signal(sig()); st = b.status(); o = st["yellow_adjusted"]["open"][0]
        px, g, f, net = engine.yellow_pnl(self.CFG, "long", 100.0, 101.0, "MARK", 2000)
        self.assertAlmostEqual(o["yellow_unrealized_usd"], round(net, 4), 4); self.assertEqual(st["yellow_adjusted"]["unrealized_pnl_usd"], round(net, 4))
