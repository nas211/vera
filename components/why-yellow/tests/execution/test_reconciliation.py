import unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.types import OrderState as O, PositionState as P, VenueState as V


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.e = Env(); self.e.ready()

    def test_local_open_venue_closed(self):
        coid, r = self.e.open_and_fill("sig-1")
        self.e.mock.manual_close(ACCT, "BTCUSDT-PERP", "long")
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSED.value)
        self.assertEqual(self.e.pos(coid)["reason"], "VENUE_CLOSED")

    def test_local_closed_venue_open_orphan_blocks_market(self):
        self.e.mock.manual_open(ACCT, "ETHUSDT-PERP", "short", "1", "2500")
        rep = self.e.eng.reconcile()
        self.assertIn("LOCAL_CLOSED_VENUE_OPEN", [m["kind"] for m in rep.mismatches])
        orphan = self.e.ledger.positions_where([P.RECONCILIATION_REQUIRED])[0]
        self.assertEqual((orphan["origin"], orphan["strategy"]), ("venue", "UNMANAGED"))
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-x", market="ETHUSDT-PERP", direction="short", price="2500")).status, "BLOCKED")
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-y")).status, "ACKNOWLEDGED")   # other market unaffected
        self.assertEqual(self.e.mock.place_calls, 1)

    def test_unknown_order_resolved_from_venue(self):
        self.e.mock.script_place("accept_lost")
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        self.assertEqual(self.e.venue.state, V.DEGRADED)
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.order(r.client_order_id)["state"], O.ACKNOWLEDGED.value)
        self.assertEqual(self.e.venue.state, V.READY)

    def test_quantity_mismatch_reprotects(self):
        coid, r = self.e.open_and_fill("sig-1")
        p = self.e.mock._pos(ACCT, "BTCUSDT-PERP", "long")
        p["qty"] = Decimal("0.012")                   # partial manual close without a trade record
        rep = self.e.eng.reconcile()
        self.assertIn("QTY_MISMATCH", [m["kind"] for m in rep.mismatches])
        pos = self.e.pos(coid)
        self.assertEqual(Decimal(pos["qty"]), p["qty"])
        self.assertEqual(pos["state"], P.PROTECTED.value)
        self.assertEqual(Decimal(self.e.order(self.e.ledger.get_protection(coid)["target_coid"])["qty"]), p["qty"])

    def test_side_mismatch_flagged_and_blocked(self):
        coid, r = self.e.open_and_fill("sig-1")
        self.e.mock.manual_close(ACCT, "BTCUSDT-PERP", "long")
        self.e.mock.manual_open(ACCT, "BTCUSDT-PERP", "short", "0.02", "77000")
        rep = self.e.eng.reconcile()
        kinds = [m["kind"] for m in rep.mismatches]
        self.assertIn("SIDE_MISMATCH", kinds); self.assertIn("LOCAL_CLOSED_VENUE_OPEN", kinds)
        self.assertEqual(self.e.pos(coid)["state"], P.CLOSED.value)
        self.assertIn("BTCUSDT-PERP", self.e.eng.blocked_markets[ACCT])
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-2")).status, "BLOCKED")

    def test_partial_fill_then_ioc_expiry(self):
        r = self.e.eng.submit_entry(self.e.signal("sig-1"))
        coid = r.client_order_id; q = Decimal(self.e.order(coid)["qty"])
        part = Decimal("0.006")
        self.e.mock.fill(coid, part, "77000")
        self.e.mock.expire_ioc(coid)
        rep = self.e.eng.reconcile()
        o = self.e.order(coid)
        self.assertEqual(o["state"], O.CANCELLED.value); self.assertEqual(Decimal(o["filled_qty"]), part)
        pos = self.e.pos(coid)
        self.assertEqual(Decimal(pos["qty"]), part); self.assertEqual(pos["state"], P.PROTECTED.value)

    def test_orphan_order_with_our_prefix_is_cancelled(self):
        from execution.types import OrderRequest
        req = OrderRequest(ACCT, "BTCUSDT-PERP", "sell", "long", "limit", Decimal("0.01"), 10, "why-deadbeefdeadbeefdeadbeefdead", Decimal("80000"), True, "gtc")
        self.e.mock.place_order(req)
        rep = self.e.eng.reconcile()
        self.assertIn("ORPHAN_ORDER", [m["kind"] for m in rep.mismatches])
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 0)

    def test_foreign_manual_order_recorded_not_touched(self):
        from execution.types import OrderRequest
        req = OrderRequest(ACCT, "BTCUSDT-PERP", "buy", "long", "limit", Decimal("0.01"), 10, "", Decimal("70000"), False, "gtc")
        rec = self.e.mock._create(req); rec["client_order_id"] = None
        rep = self.e.eng.reconcile()
        self.assertIn("FOREIGN_OPEN_ORDER", [m["kind"] for m in rep.mismatches])
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 1)

    def test_duplicate_events_during_reconcile(self):
        coid, r = self.e.open_and_fill("sig-1")
        t = self.e.mock.duplicate_last_trade()
        rep1 = self.e.eng.reconcile(); rep2 = self.e.eng.reconcile()
        self.assertEqual(len(self.e.ledger.fills_for(coid)), 1)
        self.assertEqual(self.e.pos(coid)["state"], P.PROTECTED.value)
        self.assertEqual(self.e.mock.place_calls, 2)

    def test_reconcile_reads_all_pages(self):
        e = Env(page_size=1); e.ready()
        coids = []
        for i in range(4):
            r = e.eng.submit_entry(e.signal(f"sig-{i}", market="ETHUSDT-PERP" if i % 2 else "BTCUSDT-PERP", price="2500" if i % 2 else "77000", direction="long" if i < 2 else "short"))
            coids.append(r.client_order_id)
        self.assertEqual(e.mock.read_calls > 0, True)
        rep = e.eng.reconcile()
        self.assertTrue(rep.ok)
        for c in coids:
            self.assertEqual(e.order(c)["state"], O.ACKNOWLEDGED.value)

    def test_reconcile_failure_keeps_venue_not_ready(self):
        self.e.mock.script_read("http_503", "http_503", "http_503")
        rep = self.e.eng.reconcile()
        self.assertFalse(rep.ok)
        self.assertEqual(self.e.venue.state, V.DEGRADED)
        self.assertEqual(self.e.eng.submit_entry(self.e.signal("sig-1")).status, "BLOCKED")
