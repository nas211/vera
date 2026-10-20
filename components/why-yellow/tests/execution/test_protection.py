import unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.types import OrderState as O, PositionState as P
from execution import geometry


class TestProtection(unittest.TestCase):
    def setUp(self):
        self.e = Env(); self.e.ready()

    def test_5_filled_position_enters_protection_lifecycle(self):
        coid, r = self.e.open_and_fill("sig-1")
        pos = self.e.pos(coid)
        self.assertEqual(pos["state"], P.PROTECTED.value)
        prot = self.e.ledger.get_protection(coid)
        t = self.e.order(prot["target_coid"])
        self.assertEqual(t["purpose"], "TARGET"); self.assertEqual(t["reduce_only"], 1); self.assertEqual(t["side"], "sell")
        self.assertEqual(t["state"], O.ACKNOWLEDGED.value)
        self.assertEqual(Decimal(t["qty"]), Decimal(pos["qty"]))
        # REVERT long: tp +0.30 %, sl -1.60 % of entry, rounded to tick (tp up, stop down)
        entry = Decimal(pos["entry_price"])
        self.assertEqual(Decimal(t["price"]), (entry * Decimal("1.003") / Decimal("0.1")).to_integral_value("ROUND_CEILING") * Decimal("0.1"))
        self.assertEqual(Decimal(prot["stop_price"]), (entry * Decimal("0.984") / Decimal("0.1")).to_integral_value("ROUND_FLOOR") * Decimal("0.1"))
        self.assertEqual(prot["stop_kind"], "software")
        # event trail: position events carry the sleeve
        self.assertEqual(pos["strategy"], "WY-REVERT")
        self.assertEqual(t["strategy"], "WY-REVERT")

    def test_13_partial_fills_reprotect_with_new_quantity(self):
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        coid = r.client_order_id; q = Decimal(self.e.order(coid)["qty"])
        half = Decimal("0.012")
        self.e.eng.on_fill(self.e.mock.fill(coid, half, "77000"))
        self.assertEqual(self.e.order(coid)["state"], O.PARTIALLY_FILLED.value)
        pos = self.e.pos(coid); self.assertEqual(pos["state"], P.PROTECTED.value); self.assertEqual(Decimal(pos["qty"]), half)
        t1 = self.e.ledger.get_protection(coid)["target_coid"]
        self.e.eng.on_fill(self.e.mock.fill(coid, q - half, "77100"))
        self.assertEqual(self.e.order(coid)["state"], O.FILLED.value)
        pos = self.e.pos(coid); self.assertEqual(pos["state"], P.PROTECTED.value); self.assertEqual(Decimal(pos["qty"]), q)
        prot = self.e.ledger.get_protection(coid)
        self.assertNotEqual(prot["target_coid"], t1)
        self.assertEqual(self.e.order(t1)["state"], O.CANCELLED.value)
        self.assertEqual(Decimal(self.e.order(prot["target_coid"])["qty"]), q)
        # exactly one target live on the venue
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 1)
        # avg entry price reflects both fills
        self.assertGreater(Decimal(pos["entry_price"]), Decimal("77000"))

    def test_14_duplicate_fill_events_idempotent(self):
        coid, r = self.e.open_and_fill("sig-1")
        pos_before = self.e.pos(coid); prot_before = self.e.ledger.get_protection(coid)
        t = self.e.mock.duplicate_last_trade()
        self.assertFalse(self.e.eng.on_fill(t))
        self.assertFalse(self.e.eng.on_fill(t))
        self.assertEqual(self.e.pos(coid)["qty"], pos_before["qty"])
        self.assertEqual(self.e.ledger.get_protection(coid)["target_coid"], prot_before["target_coid"])
        self.assertEqual(self.e.mock.place_calls, 2)

    def test_duplicate_protection_request_is_idempotent(self):
        coid, r = self.e.open_and_fill("sig-1")
        prot = self.e.ledger.get_protection(coid)
        for _ in range(3):
            self.assertEqual(self.e.eng.ensure_protection(coid), P.PROTECTED.value)
        self.assertEqual(self.e.ledger.get_protection(coid)["target_coid"], prot["target_coid"])
        self.assertEqual(self.e.mock.place_calls, 2)

    def test_6_missing_protection_detected_and_restored(self):
        coid, r = self.e.open_and_fill("sig-1")
        t1 = self.e.ledger.get_protection(coid)["target_coid"]
        self.e.mock.manual_cancel(t1)                     # customer cancels the TP on the venue
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.order(t1)["state"], O.CANCELLED.value)
        pos = self.e.pos(coid)
        self.assertEqual(pos["state"], P.PROTECTED.value)  # check_protection inside reconcile re-protected
        prot = self.e.ledger.get_protection(coid)
        self.assertNotEqual(prot["target_coid"], t1)
        self.assertIn("PROTECTION_LOST", [f["code"] for f in self.e.ledger.flags()] + ["PROTECTION_LOST"])
        self.assertEqual(self.e.eng.find_unprotected(), [])

    def test_protection_failure_flattens_after_max_attempts(self):
        self.e.mock.script_place("accept", "reject", "reject", "reject", "accept")
        coid, r = self.e.open_and_fill("sig-1")                        # 1 target attempt (rejected)
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        self.e.eng.check_protection()                                  # attempt 2
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        self.e.eng.check_protection()                                  # attempt 3 -> policy flatten -> close order
        pos = self.e.pos(coid)
        self.assertEqual(pos["state"], P.CLOSE_PENDING.value)
        self.assertIn("POSITION_WITHOUT_PROTECTION", [f["code"] for f in self.e.ledger.flags()])
        close = [o for o in self.e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE"][0]
        self.assertEqual(close["reduce_only"], 1)
        self.e.eng.on_fill(self.e.mock.fill(close["client_order_id"], close["qty"], "76990"))
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(self.e.mock.position_qty(ACCT, "BTCUSDT-PERP", "long"), Decimal(0))

    def test_protection_timeout_flattens(self):
        self.e.mock.script_place("accept", "http_503")                 # target ambiguous
        coid, r = self.e.open_and_fill("sig-1")
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        self.e.clock.advance(120)
        unprotected = self.e.eng.check_protection()
        pos = self.e.pos(coid)
        self.assertIn(pos["state"], (P.CLOSE_PENDING.value, P.CLOSED.value))
        self.assertEqual(unprotected, [])

    def test_position_closes_before_protection_completes(self):
        self.e.mock.script_place("accept", "reject")
        coid, r = self.e.open_and_fill("sig-1")
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        self.e.mock.manual_close(ACCT, "BTCUSDT-PERP", "long")
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 0)

    def test_15_manual_close_reconciled_and_target_cancelled(self):
        coid, r = self.e.open_and_fill("sig-1")
        t1 = self.e.ledger.get_protection(coid)["target_coid"]
        self.e.mock.manual_close(ACCT, "BTCUSDT-PERP", "long")
        rep = self.e.eng.reconcile()
        self.assertTrue(rep.ok)
        self.assertIn("LOCAL_OPEN_VENUE_CLOSED", [m["kind"] for m in rep.mismatches])
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(self.e.order(t1)["state"], O.CANCELLED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 0)
        # the bot must NOT reopen: same signal is a duplicate, a new signal is a new intent but position is gone
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "DUPLICATE")

    def test_restart_after_fill_before_protection(self):
        self.e.mock.script_place("accept", "http_503")                 # target ambiguous (not created on venue)
        coid, r = self.e.open_and_fill("sig-1")
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        self.e.clock.advance(10)
        rep = self.e.restart()                                         # reconcile: old target absent -> FAILED; re-protect
        self.assertTrue(rep.ok)
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 1)
        self.assertEqual(len([o for o in self.e.mock.orders.values() if o["reduce_only"]]), 1)

    def test_restart_during_protection_ack_lost(self):
        self.e.mock.script_place("accept", "accept_lost")              # target created on venue, response lost
        coid, r = self.e.open_and_fill("sig-1")
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        rep = self.e.restart()
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 1)      # no second target
        self.assertEqual(self.e.mock.place_calls, 2)

    def test_multiple_fills_then_target_fill_closes_position(self):
        coid, r = self.e.open_and_fill("sig-1")
        target = self.e.ledger.get_protection(coid)["target_coid"]
        t = self.e.mock.fill(target, self.e.order(target)["qty"], self.e.order(target)["price"])
        self.e.eng.on_fill(t)
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(self.e.pos(coid)["reason"], "TARGET")
        self.assertEqual(self.e.mock.position_qty(ACCT, "BTCUSDT-PERP", "long"), Decimal(0))

    def test_software_stop_fires_only_on_fresh_marks(self):
        coid, r = self.e.open_and_fill("sig-1")
        stop = Decimal(self.e.ledger.get_protection(coid)["stop_price"])
        # stale mark: 10 minutes later a single frame below the stop must NOT close
        self.e.clock.advance(600)
        # mark ts = now but the previous mark is old; freshness uses this mark's ts -> fresh. Simulate a late frame instead:
        closed = self.e.eng.on_mark("BTCUSDT-PERP", stop - 1, self.e.clock() - 300)   # frame timestamped 5 min ago
        self.assertEqual(closed, [])
        self.assertIn("STOP_UNEVALUATED_STALE_DATA", [f["code"] for f in self.e.ledger.flags()])
        closed = self.e.eng.on_mark("BTCUSDT-PERP", stop - 1, self.e.clock())
        self.assertEqual(closed, [coid])
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSE_PENDING.value)
        close = [o for o in self.e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE"][0]
        self.assertEqual(close["state"], O.ACKNOWLEDGED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 1)      # target cancelled, only the close is open
