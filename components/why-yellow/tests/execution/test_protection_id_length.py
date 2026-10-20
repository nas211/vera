import unittest
from decimal import Decimal as D
from execution.engine import protection_coid
from execution.types import Capabilities
from tests.execution.harness import Env, ACCT

class ProtectionIdTests(unittest.TestCase):
    def test_legacy_ids_stable_and_long_revisions_distinct(self):
        pid='why-9d39acc5f487d824b28e032df8a6'
        self.assertEqual(protection_coid(pid,'sl',9),pid+'-sl9')
        ids=[protection_coid(p,k,v) for p in (pid,pid+'other') for k in ('sl','tp') for v in (9,10,99,100,1000000)]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(all(len(x)<=36 for x in ids))
        self.assertEqual(protection_coid(pid,'tp',10),protection_coid(pid,'tp',10))

    def test_many_partial_fills_remain_protected_and_restart_idempotent(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                e=Env(capabilities=Capabilities(venue_stop_orders=True,conditional_take_profit=True))
                self.addCleanup(e.ledger.close);e.ready();e.config.take_profit_margin_pct=D(5)
                original=e.mock.place_order
                def strict(req):
                    self.assertLessEqual(len(req.client_order_id),36)
                    return original(req)
                e.mock.place_order=strict
                sig=e.signal(direction=direction)
                r=e.eng.submit_entry(sig);self.assertEqual(r.status,'ACKNOWLEDGED')
                qty=D(e.order(r.client_order_id)['qty']);part=D('.002')
                for n in range(7):
                    fill_qty=part if n<6 else qty-part*6
                    e.eng.on_fill(e.mock.fill(r.client_order_id,fill_qty,'77000',fee=str(fill_qty*D(77000)*D('.0004'))))
                    self.assertEqual(e.pos(r.client_order_id)['state'],'PROTECTED')
                p=e.ledger.get_protection(r.client_order_id);self.assertGreater(p['version'],9)
                count=e.mock.place_calls;self.assertTrue(e.restart().ok)
                self.assertEqual(e.mock.place_calls,count)
                self.assertEqual(e.pos(r.client_order_id)['state'],'PROTECTED')
                entry=e.order(r.client_order_id);q=D(entry['filled_qty']);ep=D(entry['avg_price']);fees=D(entry['fees']);sign=1 if direction=='long' else -1
                stop=D(p['stop_price']);target=D(p['tp_price'])
                self.assertGreaterEqual((stop-ep)*q*sign-fees-stop*q*D('.0004'),D(-2))
                target_net=(target-ep)*q*sign-fees-target*q*D('.0004')
                margin=ep*q/D(entry['leverage'])
                self.assertGreaterEqual(target_net,margin*D('.05'))

    def test_existing_target_reconciles_to_five_percent_without_widening_stop(self):
        for direction in ('long','short'):
            with self.subTest(direction=direction):
                e=Env(capabilities=Capabilities(venue_stop_orders=True,conditional_take_profit=True))
                self.addCleanup(e.ledger.close);e.ready();e.config.take_profit_margin_pct=D(3)
                pid,_=e.open_and_fill(direction=direction)
                before=e.ledger.get_protection(pid);e.config.take_profit_margin_pct=D(5)
                e.eng.check_protection();after=e.ledger.get_protection(pid)
                self.assertNotEqual(before['tp_price'],after['tp_price'])
                self.assertEqual(before['stop_price'],after['stop_price'])
                self.assertEqual(before['stop_coid'],after['stop_coid'])
                self.assertEqual(e.pos(pid)['state'],'PROTECTED')
                calls=e.mock.place_calls;self.assertTrue(e.restart().ok);self.assertEqual(e.mock.place_calls,calls)
