"""Reproduce Yellow's observed reduce-only reservation conflict without sockets.

A resting limit target locks its remaining quantity; conditional orders do not
lock until triggered. The production incident returned no_position_to_close for
the stop while the target remained live, then accepted a close after cancellation.
"""
import unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution.transport import VenueRejected
from execution.types import Capabilities, PositionState as P, OrderState as O, VENUE_OPEN, VENUE_REJECTED
from execution.yellow_transport import YellowTransport, normalise_order
from .test_adapter_yellow_exec import client


class TestYellowExitReservations(unittest.TestCase):
    def setUp(self):
        self.e = Env(capabilities=YellowTransport.capabilities)
        self.e.ready()
        original_place = self.e.mock.place_order

        def place(req):
            if req.reduce_only and req.qty > self.available(req.market, req.direction):
                raise VenueRejected('no_position_to_close', 'position reserved by resting order', 400)
            return original_place(req)

        self.e.mock.place_order = place

    def tearDown(self):
        self.e.ledger.close()

    def available(self, market, direction, exclude=None):
        locked = sum((o['qty'] - o['filled_qty'] for o in self.e.mock.orders.values()
                      if o['account_id'] == ACCT and o['market'] == market and o['direction'] == direction
                      and o['reduce_only'] and o['order_type'] == 'limit' and o['state'] == VENUE_OPEN
                      and o['client_order_id'] != exclude), Decimal(0))
        return self.e.mock.position_qty(ACCT, market, direction) - locked

    def trigger(self, coid):
        order = next(o for o in self.e.mock.orders.values() if o['client_order_id'] == coid)
        qty = order['qty'] - order['filled_qty']
        if qty > self.available(order['market'], order['direction'], coid):
            order['state'] = VENUE_REJECTED
            raise VenueRejected('no_position_to_close', 'position reserved by resting target', 400)
        return self.e.mock.fill(coid, qty, order['trigger_price'])

    def test_legacy_limit_target_reproduces_stop_rejection(self):
        self.e.mock.capabilities = Capabilities(venue_stop_orders=True)
        pid, _ = self.e.open_and_fill()
        prot = self.e.ledger.get_protection(pid)
        self.assertEqual(self.available('BTCUSDT-PERP', 'long'), 0)
        with self.assertRaises(VenueRejected):
            self.trigger(prot['stop_coid'])
        self.assertGreater(self.e.mock.position_qty(ACCT, 'BTCUSDT-PERP', 'long'), 0)

    def test_yellow_stop_executes_offline_without_target_reservation(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                self.e.feed()
                pid, _ = self.e.open_and_fill(sid=direction, direction=direction)
                prot = self.e.ledger.get_protection(pid)
                target = self.e.order(prot['target_coid'])
                self.assertEqual(target['order_type'], 'trigger_market')
                self.assertEqual(self.available('BTCUSDT-PERP', direction), Decimal(self.e.pos(pid)['qty']))
                self.e.eng.on_disconnect('bot offline')
                self.trigger(prot['stop_coid'])
                self.assertEqual(self.e.mock.position_qty(ACCT, 'BTCUSDT-PERP', direction), 0)
                self.assertTrue(self.e.restart().ok)
                self.assertEqual(self.e.pos(pid)['state'], P.CLOSED.value)
                self.assertEqual(self.e.order(prot['target_coid'])['state'], O.CANCELLED.value)

    def test_take_profit_executes_offline_and_cleans_sibling_on_restart(self):
        for direction in ('long', 'short'):
            self.e.feed()
            pid, _ = self.e.open_and_fill(sid=direction, direction=direction)
            prot = self.e.ledger.get_protection(pid)
            self.assertEqual(self.e.order(prot['target_coid'])['order_type'], 'trigger_market')
            self.trigger(prot['target_coid'])
            self.assertTrue(self.e.restart().ok)
            self.assertEqual(self.e.pos(pid)['state'], P.CLOSED.value)
            self.assertEqual(self.e.order(prot['stop_coid'])['state'], O.CANCELLED.value)

    def test_partial_entry_growth_does_not_reject_replacement_stop(self):
        result = self.e.eng.submit_entry(self.e.signal())
        pid = result.client_order_id
        qty = Decimal(self.e.order(pid)['qty'])
        self.e.eng.on_fill(self.e.mock.fill(pid, '.012', '77000'))
        self.e.eng.on_fill(self.e.mock.fill(pid, qty - Decimal('.012'), '77100'))
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTED.value)
        prot = self.e.ledger.get_protection(pid)
        for key in ('stop_coid', 'target_coid'):
            self.assertEqual(Decimal(self.e.order(prot[key])['qty']), qty)
        self.assertFalse(self.e.ledger.flags('ORDER_REJECTED_DETAIL'))

    def test_existing_limit_target_is_cancelled_before_replacing_rejected_stop(self):
        self.e.mock.capabilities = Capabilities(venue_stop_orders=True)
        pid, _ = self.e.open_and_fill()
        old = self.e.ledger.get_protection(pid)
        with self.assertRaises(VenueRejected):
            self.trigger(old['stop_coid'])
        self.e.mock.capabilities = YellowTransport.capabilities
        self.assertTrue(self.e.restart().ok)
        prot = self.e.ledger.get_protection(pid)
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTED.value)
        self.assertEqual(self.e.order(old['target_coid'])['state'], O.CANCELLED.value)
        self.assertEqual(self.e.order(prot['target_coid'])['order_type'], 'trigger_market')
        self.assertFalse(self.e.ledger.flags('POSITION_WITHOUT_PROTECTION'))

    def test_migration_keeps_verified_stop_and_blocks_if_target_cancel_fails(self):
        self.e.mock.capabilities = Capabilities(venue_stop_orders=True)
        pid, _ = self.e.open_and_fill()
        old = self.e.ledger.get_protection(pid)
        self.e.mock.capabilities = YellowTransport.capabilities
        calls = self.e.mock.place_calls
        self.e.mock.script_cancel(*(['ack_stays_open'] * 3))
        self.e.eng.check_protection()
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTION_PENDING.value)
        self.assertEqual(self.e.mock.place_calls, calls)
        self.assertEqual(self.e.order(old['stop_coid'])['state'], O.ACKNOWLEDGED.value)
        result = self.e.eng.submit_entry(self.e.signal('blocked', market='ETHUSDT-PERP', price='2500'))
        self.assertEqual(result.status, 'BLOCKED')
        self.e.eng.check_protection()
        prot = self.e.ledger.get_protection(pid)
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTED.value)
        self.assertEqual(prot['stop_coid'], old['stop_coid'])
        self.assertEqual(self.e.order(prot['target_coid'])['order_type'], 'trigger_market')

    def test_target_wire_and_readback_for_both_directions(self):
        original = self.e.mock.place_order
        requests = []
        def capture(req):
            requests.append(req)
            return original(req)
        self.e.mock.place_order = capture
        for direction in ('long', 'short'):
            pid, _ = self.e.open_and_fill(sid=direction, direction=direction)
            prot = self.e.ledger.get_protection(pid)
            request = next(r for r in requests if r.client_order_id == prot['target_coid'])
            c = client(entries=False)
            YellowTransport(c).place_order(request)
            body = c.s.calls[0]['body']
            self.assertEqual(body['type'], 'trigger_market')
            self.assertEqual(body['trigger_type'], 'take_profit')
            self.assertEqual(body['trigger_price'], prot['tp_price'])
            self.assertEqual(body['time_in_force'], 'ioc')
            self.assertEqual(body['direction'], direction)
            self.assertEqual(body['side'], 'sell' if direction == 'long' else 'buy')
            self.assertTrue(body['reduce_only'])
            self.assertNotIn('price', body)
            self.assertEqual(len(c.s.calls), 1)
            order = normalise_order(dict(order_id='v-target', type='take_profit', state='trigger_wait',
                                         trigger_price=prot['tp_price'], reduce_only=True))
            self.assertEqual(order.order_type, 'trigger_market')
            self.assertEqual(order.trigger_type, 'take_profit')

    def test_incorrect_conditional_target_is_never_verified(self):
        pid, _ = self.e.open_and_fill()
        prot = self.e.ledger.get_protection(pid)
        target = next(o for o in self.e.mock.orders.values() if o['client_order_id'] == prot['target_coid'])
        target['trigger_type'] = 'stop_loss'
        self.e.eng.check_protection()
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTION_PENDING.value)
        self.assertTrue(self.e.ledger.flags('PROTECTION_NOT_CONFIRMED'))

    def test_lost_conditional_target_response_is_not_duplicated(self):
        self.e.mock.script_place('accept', 'accept', 'accept_lost')
        pid, _ = self.e.open_and_fill()
        old = self.e.ledger.get_protection(pid)
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTION_PENDING.value)
        self.e.eng.ensure_protection(pid)
        self.assertEqual(self.e.mock.place_calls, 3)
        self.assertTrue(self.e.restart().ok)
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTED.value)
        self.assertEqual(self.e.ledger.get_protection(pid)['target_coid'], old['target_coid'])
        self.assertEqual(self.e.mock.place_calls, 3)

    def test_full_fill_during_legacy_target_cancel_does_not_reopen_or_close_again(self):
        self.e.mock.capabilities = Capabilities(venue_stop_orders=True)
        pid, _ = self.e.open_and_fill()
        old = self.e.ledger.get_protection(pid)
        target = self.e.order(old['target_coid'])
        original = self.e.mock.cancel_order
        def fill_then_cancel(account, market, vid):
            if vid == target['venue_order_id']:
                self.e.mock.fill(vid, target['qty'], old['tp_price'])
            return original(account, market, vid)
        self.e.mock.cancel_order = fill_then_cancel
        self.e.mock.capabilities = YellowTransport.capabilities
        calls = self.e.mock.place_calls
        self.e.eng.check_protection()
        self.assertEqual(self.e.pos(pid)['state'], P.CLOSED.value)
        self.assertEqual(self.e.mock.place_calls, calls)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 0)

    def test_partial_fill_during_migration_protects_only_remaining_position(self):
        self.e.mock.capabilities = Capabilities(venue_stop_orders=True)
        pid, _ = self.e.open_and_fill()
        old = self.e.ledger.get_protection(pid)
        target = self.e.order(old['target_coid'])
        original = self.e.mock.cancel_order
        def fill_then_cancel(account, market, vid):
            if vid == target['venue_order_id']:
                self.e.mock.fill(vid, '.012', old['tp_price'])
            return original(account, market, vid)
        self.e.mock.cancel_order = fill_then_cancel
        self.e.mock.capabilities = YellowTransport.capabilities
        self.e.eng.check_protection()
        remaining = Decimal(target['qty']) - Decimal('.012')
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTED.value)
        self.assertEqual(Decimal(self.e.pos(pid)['qty']), remaining)
        new = self.e.ledger.get_protection(pid)
        for key in ('stop_coid', 'target_coid'):
            self.assertEqual(Decimal(self.e.order(new[key])['qty']), remaining)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 2)

    def test_partial_conditional_exit_is_resized_after_ioc_cancel(self):
        pid, _ = self.e.open_and_fill()
        old = self.e.ledger.get_protection(pid)
        target = self.e.order(old['target_coid'])
        self.e.eng.on_fill(self.e.mock.fill(target['client_order_id'], '.012', old['tp_price']))
        self.e.mock.manual_cancel(target['client_order_id'])
        self.assertTrue(self.e.eng.reconcile().ok)
        new = self.e.ledger.get_protection(pid)
        self.assertEqual(self.e.pos(pid)['state'], P.PROTECTED.value)
        remaining = Decimal(target['qty']) - Decimal('.012')
        for key in ('stop_coid', 'target_coid'):
            self.assertEqual(Decimal(self.e.order(new[key])['qty']), remaining)
        self.trigger(new['stop_coid'])
        self.assertEqual(self.e.mock.position_qty(ACCT, 'BTCUSDT-PERP', 'long'), 0)
