import unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.types import OrderState as O, PositionState as P, VenueState as V


class TestSwitches(unittest.TestCase):
    def test_7_stop_new_entries_still_manages_positions(self):
        e = Env(); e.ready()
        coid, r = e.open_and_fill("sig-1")
        e.eng.set_switches(allow_new_entries=False, reason="incident")
        self.assertEqual(e.eng.submit_entry(e.signal("sig-2", market="ETHUSDT-PERP", price="2500")).status, "BLOCKED")
        # observe + reconcile still run
        e.mock.manual_cancel(e.ledger.get_protection(coid)["target_coid"])
        summary = e.eng.manage_existing()
        self.assertTrue(summary["managed"]); self.assertTrue(summary["reconcile_ok"])
        self.assertEqual(e.pos(coid)["state"], P.PROTECTED.value)         # re-protected while entries are OFF
        # software stop still closes while entries are OFF
        stop = Decimal(e.ledger.get_protection(coid)["stop_price"])
        closed = e.eng.on_mark("BTCUSDT-PERP", stop - 1, e.clock())
        self.assertEqual(closed, [coid])
        self.assertEqual(e.pos(coid)["state"], P.CLOSE_PENDING.value)
        close = [o for o in e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE"][0]
        e.eng.on_fill(e.mock.fill(close["client_order_id"], close["qty"], "75000"))
        self.assertEqual(e.pos(coid)["state"], P.CLOSED.value)
        # explicit close path also works with entries OFF
        coid2 = None
        e.eng.set_switches(allow_new_entries=True); coid2, _ = e.open_and_fill("sig-3", price="77000")
        e.eng.set_switches(allow_new_entries=False)
        self.assertEqual(e.eng.close_position(coid2, "TIMEOUT"), "CLOSE_SUBMITTED")
        self.assertEqual(e.eng.submit_entry(e.signal("sig-4", market="ETHUSDT-PERP", price="2500")).status, "BLOCKED")

    def test_switch_persisted_and_mirrored(self):
        e = Env(); e.ready()
        e.eng.set_switches(allow_new_entries=False, reason="x")
        e.restart()
        sw = e.ledger.get_switches()
        self.assertFalse(sw.allow_new_entries); self.assertTrue(sw.manage_existing_positions)
        from execution.switches import read_mirror
        import os
        m = read_mirror(os.path.join(e.dir, "exec_switches.json"))
        self.assertFalse(m.allow_new_entries); self.assertTrue(m.manage_existing_positions)

    def test_default_is_entries_off_management_on(self):
        e = Env()
        sw = e.ledger.get_switches()
        self.assertFalse(sw.allow_new_entries); self.assertTrue(sw.manage_existing_positions)

    def test_management_switch_off_is_explicit(self):
        e = Env(); e.ready()
        coid, r = e.open_and_fill("sig-1")
        e.eng.set_switches(manage_existing_positions=False, reason="credential incident")
        stop = Decimal(e.ledger.get_protection(coid)["stop_price"])
        self.assertEqual(e.eng.on_mark("BTCUSDT-PERP", stop - 1, e.clock()), [])
        self.assertEqual(e.eng.manage_existing()["managed"], False)


class TestVenueState(unittest.TestCase):
    def test_12_reconnect_reconciles_before_entries_resume(self):
        e = Env(); e.ready()
        e.mock.disconnect(); e.eng.on_disconnect()
        self.assertEqual(e.venue.state, V.DISCONNECTED)
        self.assertEqual(e.eng.submit_entry(e.signal("sig-1")).status, "BLOCKED")
        e.mock.reconnect()
        e.feed()
        rep = e.eng.on_reconnect()
        self.assertTrue(rep.ok)
        states = [h[2] for h in e.venue.history[-3:]]
        self.assertEqual(states, ["RECOVERING", "RECONCILING", "READY"])
        self.assertEqual(e.mock.place_calls, 0)
        self.assertEqual(e.eng.submit_entry(e.signal("sig-1")).status, "ACKNOWLEDGED")

    def test_reconnect_with_failing_reads_stays_not_ready(self):
        e = Env(); e.ready()
        e.mock.disconnect(); e.eng.on_disconnect()
        rep = e.eng.on_reconnect()          # transport still down
        self.assertFalse(rep.ok)
        self.assertIn(e.venue.state, (V.DEGRADED,))
        self.assertEqual(e.eng.submit_entry(e.signal("sig-1")).status, "BLOCKED")

    def test_5xx_429_timeouts_on_submit(self):
        for b, expect in [("http_500", "UNKNOWN"), ("http_502", "UNKNOWN"), ("http_503", "UNKNOWN"), ("http_429", "FAILED"), ("timeout_before_accept", "UNKNOWN")]:
            e = Env(); e.ready()
            e.mock.script_place(b)
            r = e.eng.submit_entry(e.signal("sig-1"))
            self.assertEqual(r.status, expect, b)
            self.assertEqual(e.mock.place_calls, 1, b)
            self.assertEqual(e.venue.state, V.DEGRADED, b)
            self.assertEqual(e.eng.submit_entry(e.signal("sig-2", market="ETHUSDT-PERP", price="2500")).status, "BLOCKED", b)
            e.clock.advance(10)
            rep = e.eng.on_reconnect()
            self.assertTrue(rep.ok); self.assertEqual(e.venue.state, V.READY)
            self.assertEqual(e.mock.place_calls, 1, b)   # reconciliation never resubmits by itself

    def test_no_new_risk_until_ready_after_start(self):
        e = Env(); e.feed()
        self.assertEqual(e.venue.state, V.DISCONNECTED)
        e.eng.set_switches(allow_new_entries=True)
        self.assertEqual(e.eng.submit_entry(e.signal("sig-1")).status, "BLOCKED")
        e.eng.start()
        self.assertEqual(e.eng.submit_entry(e.signal("sig-1")).status, "ACKNOWLEDGED")

    def test_management_possible_while_degraded(self):
        e = Env(); e.ready()
        coid, r = e.open_and_fill("sig-1")
        e.mock.script_place("http_503")
        e.eng.submit_entry(e.signal("sig-2", market="ETHUSDT-PERP", price="2500"))
        self.assertEqual(e.venue.state, V.DEGRADED)
        self.assertTrue(e.venue.management_possible)
        self.assertEqual(e.eng.close_position(coid, "TIMEOUT"), "CLOSE_SUBMITTED")
