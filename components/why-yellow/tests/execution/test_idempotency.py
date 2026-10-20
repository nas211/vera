import threading, time, unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.types import OrderState as O


class SlowMock:
    """Wrap place_order with a delay so two workers overlap inside the transport."""
    def __init__(self, mock, delay=0.05):
        self.m, self.delay = mock, delay
    def __getattr__(self, k):
        return getattr(self.m, k)
    def place_order(self, req):
        time.sleep(self.delay)
        return self.m.place_order(req)


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self.e = Env(); self.e.ready()

    def test_1_duplicate_signal_cannot_duplicate_entry(self):
        r1 = self.e.eng.submit_entry(self.e.signal("sig-1"))
        r2 = self.e.eng.submit_entry(self.e.signal("sig-1"))
        r3 = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r1.status, "ACKNOWLEDGED")
        self.assertEqual((r2.status, r3.status), ("DUPLICATE", "DUPLICATE"))
        self.assertEqual(self.e.mock.place_calls, 1)
        self.assertEqual(len(self.e.mock.orders_for_coid(r1.client_order_id)), 1)

    def test_2_timeout_after_accept_cannot_duplicate_entry(self):
        self.e.mock.script_place("accept_lost")
        r1 = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r1.status, "UNKNOWN")
        self.assertEqual(self.e.order(r1.client_order_id)["state"], O.RECONCILIATION_REQUIRED.value)
        # repeated retry invocations: nothing goes to the venue
        for _ in range(5):
            self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "DUPLICATE")
        self.assertEqual(self.e.mock.place_calls, 1)
        rep = self.e.eng.reconcile()
        self.assertTrue(rep.ok)
        self.assertEqual(self.e.order(r1.client_order_id)["state"], O.ACKNOWLEDGED.value)
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "DUPLICATE")
        self.assertEqual(self.e.mock.place_calls, 1)
        self.assertEqual(len(self.e.mock.orders_for_coid(r1.client_order_id)), 1)

    def test_timeout_before_accept_resubmits_only_after_reconciliation(self):
        self.e.mock.script_place("timeout_before_accept")
        r1 = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r1.status, "UNKNOWN")
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "DUPLICATE")
        rep = self.e.eng.reconcile()   # inside grace: still unknown
        self.assertEqual(self.e.order(r1.client_order_id)["state"], O.RECONCILIATION_REQUIRED.value)
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "DUPLICATE")
        self.e.clock.advance(10)
        rep = self.e.eng.reconcile()   # proven absent -> FAILED -> VALIDATED (one resubmission allowed)
        self.assertEqual(self.e.order(r1.client_order_id)["state"], O.VALIDATED.value)
        self.assertEqual(self.e.mock.place_calls, 1)
        r2 = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r2.status, "ACKNOWLEDGED")
        self.assertEqual(r2.client_order_id, r1.client_order_id)     # same identity
        self.assertEqual(self.e.mock.place_calls, 2)
        self.assertEqual(len(self.e.mock.orders_for_coid(r1.client_order_id)), 1)

    def test_lost_response_then_venue_fill_is_attributed(self):
        self.e.mock.script_place("accept_lost")
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        o = self.e.order(r.client_order_id)
        t = self.e.mock.fill(r.client_order_id, o["qty"], "77000")
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.order(r.client_order_id)["state"], O.FILLED.value)
        self.assertIsNotNone(self.e.pos(r.client_order_id))
        self.assertEqual(self.e.mock.place_calls, 2)   # entry + protection target
        self.assertEqual(len(self.e.mock.orders_for_coid(r.client_order_id)), 1)

    def test_duplicate_worker_threads_same_intent(self):
        self.e.eng.t = SlowMock(self.e.mock)
        results = []
        def worker():
            results.append(self.e.eng.submit_entry(self.e.signal("sig-1")).status)
        ths = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.assertEqual(sorted(results), ["ACKNOWLEDGED", "DUPLICATE", "DUPLICATE", "DUPLICATE"])
        self.assertEqual(self.e.mock.place_calls, 1)

    def test_3_restart_after_submit_cannot_duplicate_entry(self):
        self.e.mock.script_place("accept_lost")
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r.status, "UNKNOWN")
        rep = self.e.restart()
        self.assertTrue(rep.ok)
        self.assertEqual(self.e.order(r.client_order_id)["state"], O.ACKNOWLEDGED.value)
        self.e.feed(); self.e.eng.set_switches(allow_new_entries=True)
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "DUPLICATE")
        self.assertEqual(self.e.mock.place_calls, 1)

    def test_4_unknown_state_forces_reconciliation_and_blocks_new_risk(self):
        self.e.mock.script_place("http_503")
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(self.e.order(r.client_order_id)["state"], O.RECONCILIATION_REQUIRED.value)
        self.assertEqual(self.e.venue.state.value, "DEGRADED")
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-2", market="ETHUSDT-PERP", price="2500")).status, "BLOCKED")
        self.assertEqual(self.e.mock.place_calls, 1)
        flags = [f["code"] for f in self.e.ledger.flags()]
        self.assertIn("UNKNOWN_ORDER_STATUS", flags)

    def test_no_retry_on_429(self):
        self.e.mock.script_place("http_429")
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(r.status, "FAILED")
        self.assertEqual(self.e.mock.place_calls, 1)
        self.assertEqual(self.e.order(r.client_order_id)["state"], O.FAILED.value)

    def test_deterministic_client_order_id(self):
        from execution.ledger import make_coid
        a = make_coid(ACCT, "sig-1", "ENTRY", "BTCUSDT-PERP", "long")
        self.assertEqual(a, make_coid(ACCT, "sig-1", "ENTRY", "BTCUSDT-PERP", "long"))
        self.assertNotEqual(a, make_coid("acct-B", "sig-1", "ENTRY", "BTCUSDT-PERP", "long"))
        self.assertNotEqual(a, make_coid(ACCT, "sig-1", "ENTRY", "BTCUSDT-PERP", "short"))
        self.assertTrue(a.startswith("why-") and len(a) == 32)
