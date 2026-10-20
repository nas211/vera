"""Offline tests with a fake LIVE client. No network. Covers: min-lot sizing, DRY_RUN never sends,
ARM gate at the client level, fail-closed gates (equity, exposure, margin, external position, book full),
long/short fills, TP/SL/TIMEOUT exits with real-fee accounting, idempotent signals, key policy, host guard."""
import json, os, sys, tempfile, unittest
os.environ["BYBIT_LIVE_YELLOW"] = "1"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, BASE)
import guard, engine
from bybit_live import BybitError, LiveClient, verify_key

STRAT = dict(cfg=dict(rv_tp=0.30, rv_sl=1.60, pb_tp=0.80, pb_sl=2.00, max_open=8, timeout_bars=144, clip_usd=2000, taker_bps=1.5, maker_bps=0.0, spread_bps=2.0, leverage=10.0), sha256="deadbeef" * 8, env={}, markets=["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP", "LINKUSDT-PERP"])
CFG = json.load(open(os.path.join(BASE, "config.json")))
TAKER = 0.00055

class Fake:
    base = guard.ALLOWED_BASE
    def __init__(self, mark=100.0, equity=200.0):
        self.mark = mark; self.equity = equity; self.available_override = None; self.orders = []; self.pos = {}; self.execs = {}; self.cpnl = {}; self.hosts_contacted = {"api.bybit.com"}; self.calls = 0; self.writes = 0
        self.stops = []; self.levs = {}; self.key_masked = "FAKE...KE"; self.external = {}
    def instruments(self):
        return [dict(symbol="BTCUSDT", status="Trading", lotSizeFilter=dict(qtyStep="0.001", minOrderQty="0.001", minNotionalValue="5"), priceFilter=dict(tickSize="0.10"), leverageFilter=dict(maxLeverage="100")),
                dict(symbol="ETHUSDT", status="Trading", lotSizeFilter=dict(qtyStep="0.01", minOrderQty="0.01", minNotionalValue="5"), priceFilter=dict(tickSize="0.01"), leverageFilter=dict(maxLeverage="100")),
                dict(symbol="SOLUSDT", status="Trading", lotSizeFilter=dict(qtyStep="0.1", minOrderQty="0.1", minNotionalValue="5"), priceFilter=dict(tickSize="0.010"), leverageFilter=dict(maxLeverage="100")),
                dict(symbol="LINKUSDT", status="Trading", lotSizeFilter=dict(qtyStep="0.1", minOrderQty="0.1", minNotionalValue="5"), priceFilter=dict(tickSize="0.001"), leverageFilter=dict(maxLeverage="75"))]
    def tickers(self, symbol=None): return [dict(symbol=symbol, markPrice=str(self.mark), bid1Price=str(self.mark - 0.01), ask1Price=str(self.mark + 0.01), lastPrice=str(self.mark))]
    def fee_rate(self, symbol): return dict(takerFeeRate=str(TAKER), makerFeeRate="0.0002")
    def _arm(self, what): guard.require_armed(what, self.arm_file)
    def set_leverage(self, symbol, lev): self._arm("set-leverage"); self.writes += 1; self.levs[symbol] = lev
    def place_market(self, symbol, side, qty, link, take_profit=None, stop_loss=None, trigger_by="MarkPrice", reduce_only=False):
        self._arm("order"); self.writes += 1
        if any(o["link"] == link for o in self.orders): raise BybitError(110072, "OrderLinkedID is duplicate", "/v5/order/create")
        oid = f"oid{len(self.orders)+1}"; fill = self.mark + (0.02 if side == "Buy" else -0.02)
        self.orders.append(dict(link=link, id=oid, symbol=symbol, side=side, qty=qty, tp=take_profit, sl=stop_loss, reduce_only=reduce_only, fill=fill))
        if reduce_only: self.close(symbol, fill, link=link, oid=oid, sot="")
        else:
            self.pos[symbol] = dict(symbol=symbol, size=qty, avgPrice=str(fill), side=side, unrealisedPnl="0", markPrice=str(self.mark), takeProfit=take_profit, stopLoss=stop_loss, leverage="10")
            self.execs.setdefault(symbol, []).append(dict(orderLinkId=link, orderId=oid, closedSize="0", execFee=str(float(qty) * fill * TAKER), execPrice=str(fill), execQty=qty, execTime="1000", stopOrderType=""))
        return dict(orderId=oid, orderLinkId=link)
    def close(self, symbol, px, link="", oid="x", sot="TakeProfit"):
        p = self.pos.pop(symbol); qty = float(p["size"]); entry = float(p["avgPrice"]); sg = 1 if p["side"] == "Buy" else -1
        fee = qty * px * TAKER; gross = sg * (px - entry) * qty
        self.execs.setdefault(symbol, []).append(dict(orderLinkId=link, orderId=oid, closedSize=p["size"], execFee=str(fee), execPrice=str(px), execQty=p["size"], execTime="2000", stopOrderType=sot))
        self.cpnl[symbol] = [dict(avgEntryPrice=str(entry), avgExitPrice=str(px), qty=p["size"], closedPnl=str(gross - fee - qty * entry * TAKER), updatedTime="2000")]
    def order_by_link(self, link):
        for o in self.orders:
            if o["link"] == link: return dict(orderStatus="Filled", avgPrice=str(o["fill"]), cumExecQty=o["qty"], cumExecFee=str(float(o["qty"]) * o["fill"] * TAKER), orderId=o["id"], updatedTime="1000")
    def set_trading_stop(self, symbol, tp, sl, trigger_by="MarkPrice"): self._arm("trading-stop"); self.writes += 1; self.stops.append((symbol, tp, sl))
    def positions(self, symbol=None):
        allp = list(self.pos.values()) + list(self.external.values())
        if symbol: return [p for p in allp if p["symbol"] == symbol] or [dict(symbol=symbol, size="0", leverage=str(self.levs.get(symbol, 10)))]
        return allp
    def executions(self, symbol, start_ms=None, limit=50): return self.execs.get(symbol, [])
    def closed_pnl(self, symbol, start_ms=None, limit=20): return self.cpnl.get(symbol, [])
    def wallet(self):
        im = sum(float(p["size"]) * float(p["avgPrice"]) / 10 for p in self.pos.values())
        return [dict(totalEquity=str(self.equity), totalWalletBalance=str(self.equity), totalAvailableBalance="", totalPerpUPL="0",
                     coin=[dict(coin="USDT", walletBalance=str(self.equity if self.available_override is None else self.available_override), equity=str(self.equity), unrealisedPnl="0", totalPositionIM=str(im), totalOrderIM="0", locked="0")])]

def sig(market="ETHUSDT-PERP", side="long", strat="WY-PULLBACK", ts=1_790_000_000_000, bar=1_789_999_700_000, entry=100.0):
    tp, sl = (0.30, 1.60) if strat == "WY-REVERT" else (0.80, 2.00); sg = 1 if side == "long" else -1
    return dict(event="OPEN", ts=ts, bar_ms=bar, market=market, side=side, strategy=strat, entry=entry, target=entry * (1 + sg * tp / 100), stop=entry * (1 - sg * sl / 100), notional=2000, detail={})

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wyl-test-"); self.clock = {"now": 1_790_000_000_000 / 1000 + 5}
        self.arm = os.path.join(self.d, "ARMED.json"); self.fake = Fake(); self.fake.arm_file = self.arm
        self.b = engine.Bridge(self.fake, STRAT, clock=lambda: self.clock["now"], state_dir=self.d, logger=lambda *a: None, cfg=CFG, arm_file=self.arm); self.b.load_instruments()
    def arm_it(self):
        with open(self.arm, "w") as fh: json.dump(dict(command="START LIVE"), fh)

    # ---- sizing ----
    def test_min_lot_bound_by_min_qty_and_min_notional(self):
        ins = self.b.instr
        self.assertEqual(engine.min_lot(ins["BTCUSDT"], 84547.3, CFG["sizing"]), 0.001)                    # minOrderQty binds ($84.5)
        self.assertAlmostEqual(engine.min_lot(ins["LINKUSDT"], 13.882, CFG["sizing"]), 0.4)                 # 5*1.05/13.882=0.378 -> 0.4
        self.assertAlmostEqual(engine.min_lot(ins["SOLUSDT"], 117.4, CFG["sizing"]), 0.1)
        self.assertAlmostEqual(engine.min_lot(ins["ETHUSDT"], 100.0, dict(CFG["sizing"], target_notional_usd=50)), 0.5)  # scaling knob
        self.assertAlmostEqual(engine.min_lot(ins["ETHUSDT"], 100.0, dict(CFG["sizing"], target_notional_usd=1)), 0.05)  # never below min notional
    def test_signal_uses_min_lot_not_strategy_clip(self):
        self.arm_it(); p = self.b.on_signal(sig()); o = self.fake.orders[0]
        self.assertEqual(o["qty"], "0.05"); self.assertAlmostEqual(p["live_notional_usd"], 5.0, places=6); self.assertAlmostEqual(p["size_scale"], 0.0025)
        self.assertEqual(p["tp_pct"], 0.80); self.assertEqual(p["sl_pct"], 2.00)                                           # geometry untouched

    # ---- arm gate ----
    def test_unarmed_is_dry_run_and_sends_nothing(self):
        p = self.b.on_signal(sig())
        self.assertEqual(p["decision"], "DRY_RUN"); self.assertEqual(self.fake.orders, []); self.assertEqual(self.fake.writes, 0)
        self.assertEqual(p["would_send"]["qty"], "0.05"); self.assertEqual(p["would_send"]["side"], "Buy"); self.assertEqual(self.b.open, {})
        self.assertEqual(engine.read_jsonl(self.b.f_signals)[0]["decision"], "DRY_RUN")
    def test_client_level_refuses_writes_when_unarmed(self):
        with self.assertRaises(guard.NotArmed): self.fake.place_market("ETHUSDT", "Buy", "0.06", "x")
        open(self.arm, "w").write(json.dumps(dict(command="start live")))                                                         # wrong phrase
        with self.assertRaises(guard.NotArmed): self.fake.set_trading_stop("ETHUSDT", 1, 2)
        os.environ["BYBIT_LIVE_YELLOW"] = "0"; self.arm_it()
        try:
            with self.assertRaises(guard.NotArmed): self.fake.set_leverage("ETHUSDT", 10)
        finally: os.environ["BYBIT_LIVE_YELLOW"] = "1"
    def test_real_client_post_blocked_unarmed_before_any_network(self):
        c = LiveClient("k", "s", arm_file=self.arm)
        with self.assertRaises(guard.NotArmed): c.place_market("ETHUSDT", "Buy", "0.06", "x")
        self.assertEqual(c.calls, 0); self.assertEqual(c.hosts_contacted, set())
    def test_dry_run_signal_is_not_reexecuted_after_arming(self):
        self.b.on_signal(sig()); self.arm_it(); self.b.on_signal(sig())
        self.assertEqual(self.fake.orders, [])                                                                             # idempotent: decided once

    # ---- fail-closed gates ----
    def test_small_funded_account_can_mirror_minimum_lot(self):
        self.arm_it(); self.fake.equity = 4.0; p = self.b.on_signal(sig())
        self.assertEqual(p["decision"], "FILLED"); self.assertEqual(self.fake.orders[0]["qty"], "0.05")
    def test_equity_without_available_collateral_cannot_mirror(self):
        self.arm_it(); self.fake.equity = 4.0; self.fake.available_override = 0.000043
        p = self.b.on_signal(sig())
        self.assertEqual(p["decision"], "SKIP"); self.assertIn("available_0.00_below_need", p["reason"]); self.assertEqual(self.fake.orders, [])
    def test_requested_lot_is_scaled_to_available_collateral(self):
        self.arm_it(); self.fake.equity = 100.0; self.fake.available_override = 4.0
        self.b.cfg = dict(CFG, sizing=dict(CFG["sizing"], target_notional_usd=50.0))
        p = self.b.on_signal(sig())
        self.assertEqual(p["decision"], "FILLED"); self.assertLess(float(self.fake.orders[0]["qty"]), 0.5)
        self.assertGreaterEqual(float(self.fake.orders[0]["qty"]), 0.05)
    def test_gate_exposure_never_exceeds_equity(self):
        self.b.cfg = dict(CFG, risk=dict(CFG["risk"], max_exposure_x_equity=1.0))
        self.arm_it(); self.fake.equity = 160.0; self.fake.mark = 3000.0                                                    # ETH lot 0.01 = $30
        self.b.on_signal(sig(market="ETHUSDT-PERP")); self.assertEqual(len(self.fake.orders), 1)
        self.fake.mark = 84547.3; p = self.b.on_signal(sig(market="BTCUSDT-PERP"))                                        # +$84.5 -> $114.5 ok
        self.assertEqual(p["decision"], "FILLED")
        self.fake.mark = 60.0; p = self.b.on_signal(sig(market="SOLUSDT-PERP"))                                          # 0.1 SOL = $6 -> $120.5 ok
        self.assertEqual(p["decision"], "FILLED")
        self.fake.mark = 400.0; p = self.b.on_signal(sig(market="LINKUSDT-PERP"))                                        # 0.1 LINK = $40 -> $160.5 > 160
        self.assertEqual(p["decision"], "SKIP"); self.assertEqual(p["reason"], "gate:exposure_would_exceed_equity")
    def test_gate_margin_available(self):
        self.arm_it(); self.fake.equity = 160.0; self.fake.mark = 1_000_000.0                                              # min ETH lot 0.01 = $10k > equity
        p = self.b.on_signal(sig()); self.assertEqual(p["decision"], "SKIP"); self.assertTrue(p["reason"].startswith("gate:"))
    def test_gate_external_position_and_book_full(self):
        self.arm_it(); self.fake.external["ETHUSDT"] = dict(symbol="ETHUSDT", size="1", avgPrice="100", markPrice="100", side="Buy", leverage="10")
        p = self.b.on_signal(sig()); self.assertEqual(p["reason"], "gate:external_position_on_symbol")
        self.fake.external = {f"X{i}": dict(symbol=f"X{i}", size="0.001", avgPrice="1", markPrice="1", side="Buy", leverage="10") for i in range(8)}
        p = self.b.on_signal(sig(market="SOLUSDT-PERP")); self.assertEqual(p["reason"], "gate:exchange_book_full")
    def test_halt_flag_skips(self):
        self.arm_it(); open(self.b.halt_file, "w").close(); p = self.b.on_signal(sig()); self.assertEqual(p["reason"], "halt_flag"); self.assertEqual(self.fake.orders, [])
    def test_leverage_set_when_armed(self):
        self.arm_it(); self.fake.levs["ETHUSDT"] = 5; self.b.on_signal(sig()); self.assertEqual(self.fake.levs["ETHUSDT"], 10)

    # ---- fills + exits (armed) ----
    def test_long_fill_geometry(self):
        self.arm_it(); p = self.b.on_signal(sig()); o = self.fake.orders[0]
        self.assertEqual(o["side"], "Buy"); self.assertEqual(p["decision"], "FILLED")
        self.assertAlmostEqual(float(p["tp"]), p["fill"]["avg_price"] * 1.008, places=2); self.assertAlmostEqual(float(p["sl"]), p["fill"]["avg_price"] * 0.98, places=2)
        self.assertEqual(o["tp"], "100.80"); self.assertEqual(o["sl"], "98.00"); self.assertTrue(self.fake.stops); self.assertIn("ETHUSDT", self.b.open)
    def test_short_fill_geometry(self):
        self.arm_it(); p = self.b.on_signal(sig(side="short", strat="WY-REVERT")); o = self.fake.orders[0]
        self.assertEqual(o["side"], "Sell"); self.assertAlmostEqual(float(p["tp"]), p["fill"]["avg_price"] * (1 - 0.003), places=2); self.assertAlmostEqual(float(p["sl"]), p["fill"]["avg_price"] * 1.016, places=2)
    def test_tp_exit_actual_accounting(self):
        self.arm_it(); self.b.on_signal(sig()); self.fake.close("ETHUSDT", 100.82, sot="TakeProfit"); self.b.poll()
        t = engine.read_jsonl(self.b.f_trades)[0]
        self.assertEqual(t["exit_reason"], "TARGET"); self.assertEqual(t["accounting"], "actual_bybit_live"); self.assertEqual(self.b.open, {})
        self.assertAlmostEqual(t["gross_pnl_usd"], (100.82 - 100.02) * 0.05, places=6)
        self.assertAlmostEqual(t["fees_usd"], 0.05 * 100.02 * TAKER + 0.05 * 100.82 * TAKER, places=6)
        self.assertAlmostEqual(t["net_pnl_usd"], t["gross_pnl_usd"] - t["fees_usd"], places=6); self.assertAlmostEqual(t["net_pnl_usd"], t["exchange_closed_pnl_usd"], places=6)
        ya = t["yellow_adjusted"]; self.assertEqual(ya["notional_usd"], 2000); self.assertAlmostEqual(ya["scaled_to_live"]["notional_usd"], 5.001, places=4)
    def test_sl_exit(self):
        self.arm_it(); self.b.on_signal(sig(side="short", strat="WY-REVERT")); self.fake.close("ETHUSDT", 101.6, sot="StopLoss"); self.b.poll()
        t = engine.read_jsonl(self.b.f_trades)[0]; self.assertEqual(t["exit_reason"], "STOP"); self.assertLess(t["net_pnl_usd"], 0)
    def test_timeout_exit(self):
        self.arm_it(); self.b.on_signal(sig()); self.clock["now"] += 144 * 300 + 1; self.b.poll()
        self.assertTrue(any(o["reduce_only"] for o in self.fake.orders)); self.b.poll()
        t = engine.read_jsonl(self.b.f_trades)[0]; self.assertEqual(t["exit_reason"], "TIMEOUT")
    def test_idempotent_signal(self):
        self.arm_it(); self.b.on_signal(sig()); self.b.on_signal(sig()); self.b.on_signal(sig())
        self.assertEqual(len(self.fake.orders), 1); self.assertEqual(len(self.b.seen), 1)
    def test_distinct_paper_entries_have_distinct_ids(self):
        first = sig(); second = dict(first, ts=first["ts"] + 300_000)
        self.assertNotEqual(engine.signal_id(first, "bybit_paper"), engine.signal_id(second, "bybit_paper"))
        self.assertEqual(self.b.canonical(first)["source"], "bybit_paper")
        self.assertIsInstance(self.b.canonical(first)["bar_ms"], int)
    def test_restart_recovery_never_resends(self):
        self.arm_it(); self.b.on_signal(sig())
        b2 = engine.Bridge(self.fake, STRAT, clock=lambda: self.clock["now"], state_dir=self.d, logger=lambda *a: None, cfg=CFG, arm_file=self.arm); b2.load_instruments()
        b2.on_signal(sig()); self.assertEqual(len(self.fake.orders), 1); self.assertIn("ETHUSDT", b2.open)
    def test_stale_and_busy_and_full(self):
        self.arm_it()
        self.b.on_signal(sig(ts=1_790_000_000_000 - 500_000, bar=1_789_999_400_000)); self.assertEqual(list(self.b.seen.values())[0]["decision"], "SKIP"); self.assertEqual(len(self.fake.orders), 0)
        self.b.on_signal(sig()); self.b.on_signal(sig(strat="WY-REVERT", bar=sig()["bar_ms"] + 300000)); self.assertEqual(list(self.b.seen.values())[-1]["reason"], "symbol_busy")
        self.b.max_open = 1; self.b.on_signal(sig(market="SOLUSDT-PERP")); self.assertEqual(list(self.b.seen.values())[-1]["reason"], "book_full")
    def test_geometry_mismatch_refused(self):
        self.arm_it(); s = sig(); s["target"] = s["entry"] * 1.05
        self.b.on_signal(s); self.assertEqual(list(self.b.seen.values())[0]["reason"], "geometry_mismatch"); self.assertEqual(self.fake.orders, [])

    # ---- key policy + host guard ----
    def test_key_policy(self):
        pol = CFG["key_policy"]
        good = dict(userID=39279297, readOnly=0, permissions=dict(ContractTrade=["Order", "Position"], Wallet=["SubMemberTransfer"]), ips=["*"])
        ok, pr, wn, sm = verify_key(good, pol, 39279297); self.assertTrue(ok); self.assertEqual(sm["withdrawal_permission"], "OFF"); self.assertEqual(sm["transfer_permission"], "OFF"); self.assertTrue(any("IP" in w for w in wn))
        for bad, why in ((dict(good, userID=1), "uid"), (dict(good, readOnly=1), "readOnly"), (dict(good, permissions=dict(ContractTrade=["Position"])), "Order"),
                         (dict(good, permissions=dict(good["permissions"], Wallet=["AccountTransfer"])), "AccountTransfer"), (dict(good, permissions=dict(good["permissions"], Wallet=["Withdraw"])), "Withdraw")):
            ok, pr, _, _ = verify_key(bad, pol, 39279297); self.assertFalse(ok, why); self.assertTrue(any(why in p for p in pr), (why, pr))
    def test_host_guard(self):
        for h in ("api-demo" + ".bybit.com", "api-testnet" + ".bybit.com", "evil.example"):
            with self.assertRaises(SystemExit): guard.check_url(f"https://{h}/v5/order/create")
        self.assertEqual(guard.check_url("https://api.bybit.com/v5/market/tickers"), "https://api.bybit.com/v5/market/tickers")
        guard.scan_sources()
    def test_wallet_snapshot_isolated_margin_empty_available(self):
        w = dict(totalEquity="200", totalWalletBalance="200", totalAvailableBalance="", totalPerpUPL="-1", coin=[dict(coin="USDT", walletBalance="200", equity="199", unrealisedPnl="-1", totalPositionIM="15.8", totalOrderIM="0", locked="0")])
        s = engine.wallet_snapshot(w); self.assertAlmostEqual(s["available"], 183.2); self.assertEqual(s["equity"], 200.0)

if __name__ == "__main__": unittest.main(verbosity=1)
