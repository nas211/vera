import unittest
from decimal import Decimal
from .harness import Env
from execution.state_machine import IllegalTransition, check_order_transition, check_position_transition, check_venue_transition
from execution.types import OrderState as O, PositionState as P, VenueState as V, VenueTrade


class TestStateMachine(unittest.TestCase):
    def test_illegal_transitions_rejected(self):
        for cur, new in [(O.FILLED, O.ACKNOWLEDGED), (O.INTENT, O.ACKNOWLEDGED), (O.UNKNOWN, O.ACKNOWLEDGED), (O.CANCELLED, O.FILLED),
                         (O.REJECTED, O.VALIDATED), (O.FAILED, O.SUBMITTING)]:
            with self.assertRaises(IllegalTransition):
                check_order_transition(cur, new)
        with self.assertRaises(IllegalTransition):
            check_position_transition(P.CLOSED, P.PROTECTED)
        with self.assertRaises(IllegalTransition):
            check_venue_transition(V.DISCONNECTED, V.READY)   # READY only via RECONCILING
        with self.assertRaises(IllegalTransition):
            check_venue_transition(V.RECOVERING, V.READY)

    def test_same_state_is_noop_except_more_fills(self):
        self.assertFalse(check_order_transition(O.ACKNOWLEDGED, O.ACKNOWLEDGED))
        self.assertTrue(check_order_transition(O.PARTIALLY_FILLED, O.PARTIALLY_FILLED))
        self.assertFalse(check_position_transition(P.PROTECTED, P.PROTECTED))

    def test_unknown_only_leads_to_reconciliation(self):
        self.assertTrue(check_order_transition(O.UNKNOWN, O.RECONCILIATION_REQUIRED))
        for s in O:
            if s not in (O.UNKNOWN, O.RECONCILIATION_REQUIRED):
                with self.assertRaises(IllegalTransition):
                    check_order_transition(O.UNKNOWN, s)

    def test_duplicate_events_idempotent_and_out_of_order_does_not_corrupt(self):
        e = Env(); e.ready()
        r = e.eng.submit_entry(e.signal("sig-1"))
        coid = r.client_order_id
        L = e.ledger
        # duplicate ack event -> no change, returns False
        self.assertFalse(L.transition_order(coid, O.ACKNOWLEDGED, f"ack:{coid}:v-1", venue_order_id="v-1"))
        o = e.order(coid)
        t = e.mock.fill(coid, o["qty"], "77000")
        self.assertTrue(e.eng.on_fill(t))
        self.assertFalse(e.eng.on_fill(t))          # duplicate fill delivery
        self.assertEqual(e.order(coid)["state"], O.FILLED.value)
        # a late "acknowledged" event after FILLED must not downgrade the order
        with self.assertRaises(IllegalTransition):
            L.transition_order(coid, O.ACKNOWLEDGED, "late-ack-event")
        self.assertEqual(e.order(coid)["state"], O.FILLED.value)
        self.assertEqual(Decimal(e.pos(coid)["qty"]), Decimal(o["qty"]))

    def test_fill_before_ack_is_legal_and_ack_after_fill_ignored(self):
        e = Env(); e.ready()
        e.mock.script_place("accept_lost")
        r = e.eng.submit_entry(e.signal("sig-1"))
        coid = r.client_order_id
        o = e.order(coid)
        t = e.mock.fill(coid, o["qty"], "77000")
        self.assertTrue(e.eng.on_fill(t))               # fill arrives while order is RECONCILIATION_REQUIRED
        self.assertEqual(e.order(coid)["state"], O.FILLED.value)
        e.eng.reconcile()                                # venue says filled: consistent
        self.assertEqual(e.order(coid)["state"], O.FILLED.value)
        self.assertEqual(len(e.ledger.fills_for(coid)), 1)

    def test_persistence_survives_restart(self):
        e = Env(); e.ready()
        r = e.eng.submit_entry(e.signal("sig-1"))
        counts = e.ledger.counts()
        e.restart()
        self.assertEqual(e.order(r.client_order_id)["state"], O.ACKNOWLEDGED.value)
        self.assertGreaterEqual(e.ledger.counts()["events"], counts["events"])
