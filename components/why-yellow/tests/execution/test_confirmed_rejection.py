import unittest
from dataclasses import replace
from .harness import Env
from execution.engine import ReconcileReport
from execution.types import OrderState as O, VENUE_REJECTED

class TestConfirmedRejection(unittest.TestCase):
 def test_accepted_order_later_rejected_is_terminal_without_resubmission(self):
  e=Env(); e.ready()
  r=e.eng.submit_entry(e.signal('confirmed-reject'))
  order=e.order(r.client_order_id)
  vo=e.mock.get_open_orders(order['account_id'])[0][0]
  rejected=replace(vo,state=VENUE_REJECTED)
  calls=e.mock.place_calls
  rep=ReconcileReport(ok=False,reconcile_id='reconcile:test')
  e.eng._sync_order_from_venue(order,rejected,rep)
  self.assertEqual(e.order(r.client_order_id)['state'],O.REJECTED.value)
  e.eng._sync_order_from_venue(e.order(r.client_order_id),rejected,rep)
  self.assertEqual(e.mock.place_calls,calls)
  self.assertEqual(e.eng.submit_entry(e.signal('confirmed-reject')).status,'DUPLICATE')
