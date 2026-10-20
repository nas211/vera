import threading, time, unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.types import OrderState as O, PositionState as P


class TestRestart(unittest.TestCase):
    def test_restart_during_submitting(self):
        e = Env(); e.ready()
        L = e.ledger
        row, created = L.create_intent(signal_id="sig-1", account_id=ACCT, market="BTCUSDT-PERP", side="buy", direction="long", order_type="market",
                                       purpose="ENTRY", strategy="WY-REVERT", qty=Decimal("0.025"), leverage=10, reduce_only=False, tif="ioc")
        coid = row["client_order_id"]
        L.transition_order(coid, O.VALIDATED, "v"); self.assertTrue(L.claim_for_submission(coid))
        self.assertEqual(e.order(coid)["state"], O.SUBMITTING.value)    # crash here
        e.clock.advance(10)
        rep = e.restart()
        self.assertTrue(rep.ok)
        self.assertEqual(e.mock.place_calls, 0)                            # nothing recreated blindly
        self.assertEqual(e.order(coid)["state"], O.VALIDATED.value)        # absent on venue -> eligible, not submitted
        self.assertIn("FAILED", " ".join(rep.actions))

    def test_restart_during_unknown(self):
        e = Env(); e.ready()
        e.mock.script_place("accept_lost")
        r = e.eng.submit_entry(e.signal("sig-1"))
        rep = e.restart()
        self.assertEqual(e.order(r.client_order_id)["state"], O.ACKNOWLEDGED.value)
        self.assertEqual(e.mock.place_calls, 1)

    def test_restart_during_partial_fill(self):
        e = Env(); e.ready()
        r = e.eng.submit_entry(e.signal("sig-1")); coid = r.client_order_id
        half = Decimal("0.012")
        e.mock.fill(coid, half, "77000")                                   # fill happened on venue, never delivered
        rep = e.restart()
        self.assertEqual(e.order(coid)["state"], O.PARTIALLY_FILLED.value)
        pos = e.pos(coid); self.assertEqual(Decimal(pos["qty"]), half); self.assertEqual(pos["state"], P.PROTECTED.value)
        self.assertEqual(e.mock.open_orders_count(ACCT), 2)                # entry remainder + target

    def test_restart_after_filled(self):
        e = Env(); e.ready()
        coid, _ = e.open_and_fill("sig-1")
        target = e.ledger.get_protection(coid)["target_coid"]
        rep = e.restart()
        self.assertEqual(e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(e.ledger.get_protection(coid)["target_coid"], target)
        self.assertEqual(e.mock.place_calls, 2)

    def test_restart_during_protection_pending(self):
        e = Env(); e.ready()
        e.mock.script_place("accept", "http_503")
        coid, _ = e.open_and_fill("sig-1")
        self.assertEqual(e.pos(coid)["state"], P.PROTECTION_PENDING.value)
        e.clock.advance(10)
        rep = e.restart()
        self.assertEqual(e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(e.mock.open_orders_count(ACCT), 1)

    def test_restart_during_close_pending(self):
        e = Env(); e.ready()
        coid, _ = e.open_and_fill("sig-1")
        self.assertEqual(e.eng.close_position(coid, "TIMEOUT"), "CLOSE_SUBMITTED")
        close = [o for o in e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE"][0]
        e.mock.fill(close["client_order_id"], close["qty"], "76000")     # filled on venue while we were down
        rep = e.restart()
        self.assertEqual(e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(len([o for o in e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE"]), 1)

    def test_restart_close_pending_order_vanished(self):
        e = Env(); e.ready()
        coid, _ = e.open_and_fill("sig-1")
        e.mock.script_place("timeout_before_accept")
        self.assertEqual(e.eng.close_position(coid, "TIMEOUT"), "RECONCILIATION_REQUIRED")
        e.clock.advance(10)
        rep = e.restart()
        # venue still has the leg, close order never existed -> re-protected, no blind re-close
        self.assertEqual(e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(e.mock.position_qty(ACCT, "BTCUSDT-PERP", "long") > 0, True)


class TestConcurrency(unittest.TestCase):
    def test_same_signal_delivered_twice_concurrently(self):
        e = Env(); e.ready()
        res = []
        def w(): res.append(e.eng.submit_entry(e.signal("sig-1")).status)
        ts = [threading.Thread(target=w) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(res.count("ACKNOWLEDGED"), 1); self.assertEqual(res.count("DUPLICATE"), 7)
        self.assertEqual(e.mock.place_calls, 1)

    def test_duplicate_fill_events_concurrent(self):
        e = Env(); e.ready()
        r = e.eng.submit_entry(e.signal("sig-1")); coid = r.client_order_id
        t = e.mock.fill(coid, e.order(coid)["qty"], "77000")
        outs = []
        ths = [threading.Thread(target=lambda: outs.append(e.eng.on_fill(t))) for _ in range(6)]
        [x.start() for x in ths]; [x.join() for x in ths]
        self.assertEqual(outs.count(True), 1)
        self.assertEqual(len(e.ledger.fills_for(coid)), 1)
        self.assertEqual(e.mock.place_calls, 2)

    def test_reconciliation_while_fill_arrives(self):
        e = Env(); e.ready()
        r = e.eng.submit_entry(e.signal("sig-1")); coid = r.client_order_id
        t = e.mock.fill(coid, e.order(coid)["qty"], "77000")
        outs = []
        a = threading.Thread(target=lambda: outs.append(e.eng.reconcile().ok))
        b = threading.Thread(target=lambda: outs.append(e.eng.on_fill(t)))
        a.start(); b.start(); a.join(); b.join()
        self.assertEqual(len(e.ledger.fills_for(coid)), 1)
        self.assertEqual(e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(e.mock.open_orders_count(ACCT), 1)

    def test_close_while_protection_event_arrives(self):
        e = Env(); e.ready()
        coid, _ = e.open_and_fill("sig-1")
        target = e.ledger.get_protection(coid)["target_coid"]
        t = e.order(target)
        tr = e.mock.fill(target, t["qty"], t["price"])
        outs = []
        a = threading.Thread(target=lambda: outs.append(e.eng.close_position(coid, "TIMEOUT")))
        b = threading.Thread(target=lambda: outs.append(e.eng.on_fill(tr)))
        a.start(); b.start(); a.join(); b.join()
        self.assertEqual(e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(e.mock.position_qty(ACCT, "BTCUSDT-PERP", "long"), Decimal(0))
        closes = [o for o in e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE" and o["state"] == O.ACKNOWLEDGED.value]
        # if a close was submitted it must be reduce-only (venue rejects/ignores on a flat leg); never an opposite entry
        for c in closes:
            self.assertEqual(c["reduce_only"], 1)

    def test_retry_worker_while_original_unknown(self):
        e = Env(); e.ready()
        e.mock.script_place("accept_lost")
        r = e.eng.submit_entry(e.signal("sig-1"))
        outs = []
        ths = [threading.Thread(target=lambda: outs.append(e.eng.submit_entry(e.signal("sig-1")).status)) for _ in range(5)]
        [x.start() for x in ths]; [x.join() for x in ths]
        self.assertEqual(set(outs), {"DUPLICATE"})
        self.assertEqual(e.mock.place_calls, 1)

    def test_two_workers_two_engines_same_ledger(self):
        e = Env(); e.ready()
        e2 = e.eng.__class__(e.ledger, e.mock, e.meta, e.fresh, e.venue, e.config, clock=e.clock)   # second worker on the same ledger
        outs = []
        a = threading.Thread(target=lambda: outs.append(e.eng.submit_entry(e.signal("sig-1")).status))
        b = threading.Thread(target=lambda: outs.append(e2.submit_entry(e.signal("sig-1")).status))
        a.start(); b.start(); a.join(); b.join()
        self.assertEqual(sorted(outs), ["ACKNOWLEDGED", "DUPLICATE"])
        self.assertEqual(e.mock.place_calls, 1)
