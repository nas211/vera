import unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.types import OrderState as O, PositionState as P


class TestCancel(unittest.TestCase):
    def setUp(self):
        self.e = Env(page_size=1); self.e.ready()
        self.coid, _ = self.e.open_and_fill("sig-1")
        self.target = self.e.ledger.get_protection(self.coid)["target_coid"]

    def test_16_cancel_ack_verified_against_venue(self):
        self.assertEqual(self.e.eng.cancel_order(self.target), "CANCELLED")
        self.assertEqual(self.e.order(self.target)["state"], O.CANCELLED.value)

    def test_cancel_already_filled(self):
        t = self.e.order(self.target)
        self.e.mock.fill(self.target, t["qty"], t["price"])       # filled on venue, fill not yet delivered
        self.assertEqual(self.e.eng.cancel_order(self.target), "FILLED")
        self.assertEqual(self.e.order(self.target)["state"], O.FILLED.value)
        self.assertEqual(self.e.pos(self.coid)["state"], P.CLOSED.value)    # fill synced -> position closed

    def test_cancel_already_cancelled(self):
        self.e.mock.manual_cancel(self.target)
        self.assertEqual(self.e.eng.cancel_order(self.target), "CANCELLED")

    def test_partial_fill_then_cancel(self):
        t = self.e.order(self.target)
        self.e.mock.fill(self.target, Decimal(t["qty"]) / 5, t["price"])
        self.assertEqual(self.e.eng.cancel_order(self.target), "PARTIALLY_FILLED_CANCELLED")
        o = self.e.order(self.target)
        self.assertEqual(o["state"], O.CANCELLED.value); self.assertEqual(Decimal(o["filled_qty"]), Decimal(t["qty"]) / 5)
        self.assertEqual(Decimal(self.e.pos(self.coid)["qty"]), Decimal(t["qty"]) - Decimal(t["qty"]) / 5)

    def test_timeout_after_cancel_resolved_by_lookup(self):
        self.e.mock.script_cancel("ambiguous")
        self.assertEqual(self.e.eng.cancel_order(self.target), "CANCELLED")
        self.assertEqual(self.e.mock.cancel_calls, 1)

    def test_unknown_cancel_result_when_venue_unreadable(self):
        self.e.mock.script_cancel("ambiguous_no_cancel"); self.e.mock.script_read("http_503")
        self.assertEqual(self.e.eng.cancel_order(self.target), "UNKNOWN")
        self.assertEqual(self.e.order(self.target)["state"], O.RECONCILIATION_REQUIRED.value)
        self.e.mock.script_cancel("ack")
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.order(self.target)["state"], O.ACKNOWLEDGED.value)     # venue still shows it open

    def test_ack_but_order_stays_open_is_not_trusted(self):
        self.e.mock.script_cancel("ack_stays_open", "ack_stays_open", "ack_stays_open")
        self.assertEqual(self.e.eng.cancel_order(self.target), "STILL_OPEN")
        self.assertEqual(self.e.order(self.target)["state"], O.ACKNOWLEDGED.value)
        self.assertIn("CANCEL_NOT_EFFECTIVE", [f["code"] for f in self.e.ledger.flags()])
        self.assertEqual(self.e.mock.cancel_calls, 3)

    def test_lookup_walks_all_pages(self):
        # 6 open orders on a page_size=1 venue; the target is found regardless of position
        from execution.types import OrderRequest
        for i in range(5):
            self.e.mock.place_order(OrderRequest(ACCT, "BTCUSDT-PERP", "buy", "long", "limit", Decimal("0.001"), 10, f"manual-{i}", Decimal("70000"), False, "gtc"))
        self.assertGreater(self.e.mock.open_orders_count(ACCT), 5)
        self.e.mock.script_cancel("ack_stays_open", "ack")
        self.assertEqual(self.e.eng.cancel_order(self.target), "CANCELLED")


class TestClose(unittest.TestCase):
    def setUp(self):
        self.e = Env(); self.e.ready()
        self.coid, _ = self.e.open_and_fill("sig-1")

    def test_close_uses_live_leg_quantity(self):
        p = self.e.mock._pos(ACCT, "BTCUSDT-PERP", "long"); p["qty"] = Decimal("0.012")
        self.assertEqual(self.e.eng.close_position(self.coid, "TIMEOUT"), "CLOSE_SUBMITTED")
        close = [o for o in self.e.ledger.orders_for_position(self.coid) if o["purpose"] == "CLOSE"][0]
        self.assertEqual(Decimal(close["qty"]), p["qty"])
        self.assertEqual(close["reduce_only"], 1)

    def test_close_when_venue_flat(self):
        self.e.mock.manual_close(ACCT, "BTCUSDT-PERP", "long")
        self.assertEqual(self.e.eng.close_position(self.coid, "TIMEOUT"), "CLOSED")
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 0)

    def test_close_twice_is_idempotent(self):
        self.assertEqual(self.e.eng.close_position(self.coid, "TIMEOUT"), "CLOSE_SUBMITTED")
        self.assertEqual(self.e.eng.close_position(self.coid, "TIMEOUT"), "CLOSE_PENDING")
        close_orders = [o for o in self.e.ledger.orders_for_position(self.coid) if o["purpose"] == "CLOSE"]
        self.assertEqual(len(close_orders), 1)

    def test_close_ambiguous_requires_reconciliation(self):
        self.e.mock.script_place("accept_lost")
        self.assertEqual(self.e.eng.close_position(self.coid, "TIMEOUT"), "RECONCILIATION_REQUIRED")
        self.assertEqual(self.e.pos(self.coid)["state"], P.RECONCILIATION_REQUIRED.value)
        close = [o for o in self.e.ledger.orders_for_position(self.coid) if o["purpose"] == "CLOSE"][0]
        self.e.mock.fill(close["client_order_id"], close["qty"], "76000")
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.pos(self.coid)["state"], P.CLOSED.value)
        self.assertEqual(len([o for o in self.e.ledger.orders_for_position(self.coid) if o["purpose"] == "CLOSE"]), 1)

    def test_close_rejected_by_venue_goes_to_reconciliation(self):
        self.e.mock.script_place("reject:no_position_to_close")
        self.assertEqual(self.e.eng.close_position(self.coid, "TIMEOUT"), "RECONCILIATION_REQUIRED")
        rep = self.e.eng.reconcile()
        self.assertEqual(self.e.pos(self.coid)["state"], P.PROTECTED.value)   # venue still shows the leg; re-protected

    def test_close_while_target_fill_arrives(self):
        target = self.e.ledger.get_protection(self.coid)["target_coid"]
        self.e.mock.script_cancel("ack_stays_open")
        t = self.e.order(target)
        self.e.mock.fill(target, t["qty"], t["price"])        # target fills just before our cancel
        res = self.e.eng.close_position(self.coid, "TIMEOUT")
        self.assertEqual(res, P.CLOSED.value)
        self.assertEqual(len([o for o in self.e.ledger.orders_for_position(self.coid) if o["purpose"] == "CLOSE"]), 0)
        self.assertEqual(self.e.mock.position_qty(ACCT, "BTCUSDT-PERP", "long"), Decimal(0))
