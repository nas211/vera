"""Exchange stops must survive loss of the bot, and never be inferred from a POST ack."""
import unittest
from decimal import Decimal
from .harness import Env, ACCT
from .test_adapter_yellow_exec import client, Resp
from execution.types import Capabilities, OrderState as O, PositionState as P, OrderRequest
from execution.yellow_transport import YellowTransport, normalise_order


class TestVenueStops(unittest.TestCase):
    def setUp(self):
        self.e = Env(capabilities=Capabilities(venue_stop_orders=True))
        self.e.ready()

    def tearDown(self):
        self.e.ledger.close()

    def open(self, **kw):
        coid, _ = self.e.open_and_fill(**kw)
        return coid, self.e.ledger.get_protection(coid)

    def test_long_and_short_install_verified_stop_before_target(self):
        for side in ('long', 'short'):
            coid, p = self.open(sid=side, direction=side)
            self.assertEqual(p['stop_kind'], 'venue')
            self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)
            stop = self.e.order(p['stop_coid'])
            self.assertEqual(stop['order_type'], 'trigger_market')
            self.assertEqual(stop['side'], 'sell' if side == 'long' else 'buy')
            self.assertEqual(stop['reduce_only'], 1)
            calls = [event[1] for event in self.e.mock.log if event[0] == 'place']
            self.assertLess(calls.index(p['stop_coid']), calls.index(p['target_coid']))
            vo = next(o for o in self.e.mock.orders.values() if o['client_order_id'] == p['stop_coid'])
            self.assertEqual(vo['trigger_type'], 'stop_loss')
            self.assertEqual(vo['trigger_price'], Decimal(p['stop_price']))

    def test_stop_can_execute_with_bot_and_feed_offline(self):
        coid, p = self.open()
        self.e.eng.on_disconnect('server_down')
        stop = self.e.order(p['stop_coid'])
        # Exchange executes its stored stop; no engine on_mark/on_fill call occurs.
        self.e.mock.fill(stop['client_order_id'], stop['qty'], p['stop_price'])
        self.assertEqual(self.e.mock.position_qty(ACCT, 'BTCUSDT-PERP', 'long'), 0)
        self.assertTrue(self.e.restart().ok)
        self.assertEqual(self.e.pos(coid)['state'], P.CLOSED.value)
        self.assertEqual(self.e.order(p['target_coid'])['state'], O.CANCELLED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 0)

    def test_cancelled_stop_restored_without_duplicate_target(self):
        coid, p = self.open()
        self.e.mock.manual_cancel(p['stop_coid'])
        self.e.eng.reconcile()
        new = self.e.ledger.get_protection(coid)
        self.assertNotEqual(new['stop_coid'], p['stop_coid'])
        self.assertEqual(new['target_coid'], p['target_coid'])
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 2)

    def test_yellow_trigger_canceled_history_allows_protective_close(self):
        from dataclasses import replace
        coid, p = self.open()
        original = self.e.mock.get_orders
        def yellow_history(*args, **kwargs):
            rows, cursor = original(*args, **kwargs)
            return [replace(row, state=normalise_order({
                'state': 'trigger_canceled', 'type': 'stop_loss',
                'fill_amount': '0',
            }).state) if row.client_order_id == p['stop_coid'] else row
                    for row in rows], cursor
        self.e.mock.get_orders = yellow_history
        self.e.mock.manual_cancel(p['stop_coid'])
        self.assertEqual(self.e.eng.close_position(coid, 'PROTECTION_FAILED'), 'CLOSE_SUBMITTED')
        self.assertEqual(self.e.order(p['stop_coid'])['state'], O.CANCELLED.value)
        closes = [o for o in self.e.ledger.orders_for_position(coid) if o['purpose'] == 'CLOSE']
        self.assertEqual(len(closes), 1)
        self.assertTrue(closes[0]['reduce_only'])
        self.assertEqual(self.e.eng.close_position(coid, 'PROTECTION_FAILED'), 'CLOSE_PENDING')
        self.assertFalse(self.e.ledger.flags('CANCEL_NOT_EFFECTIVE'))

    def test_unknown_trigger_status_remains_unknown(self):
        self.assertEqual(normalise_order({'state': 'trigger_unrecognised'}).state, 'unknown')

    def test_stop_ack_lost_recovers_without_second_stop(self):
        self.e.mock.script_place('accept', 'accept_lost')
        coid, p = self.open()
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTION_PENDING.value)
        self.assertIsNone(p['target_coid'])
        self.e.eng.ensure_protection(coid)
        self.assertEqual(self.e.mock.place_calls, 2)
        self.assertTrue(self.e.restart().ok)
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)
        self.assertEqual(self.e.ledger.get_protection(coid)['stop_coid'], p['stop_coid'])
        self.assertEqual(self.e.mock.place_calls, 3)

    def test_readback_failure_blocks_protected_and_new_entries(self):
        r = self.e.eng.submit_entry(self.e.signal())
        self.e.mock.script_read('timeout')
        self.e.eng.on_fill(self.e.mock.fill(r.client_order_id, self.e.order(r.client_order_id)['qty'], '77000'))
        self.assertEqual(self.e.pos(r.client_order_id)['state'], P.PROTECTION_PENDING.value)
        self.e.venue.on_reconnect(); self.e.venue.begin_reconcile(); self.e.venue.reconcile_ok()
        r2 = self.e.eng.submit_entry(self.e.signal('new', market='ETHUSDT-PERP', price='2500'))
        self.assertEqual(r2.reason, 'position_protection_unverified')
        self.assertEqual(self.e.mock.place_calls, 2)
        self.e.eng.reconcile()
        self.assertEqual(self.e.pos(r.client_order_id)['state'], P.PROTECTED.value)

    def test_wrong_trigger_price_or_permission_never_counts_as_protected(self):
        for field, bad in [('trigger_price', Decimal('1')), ('reduce_only', False),
                           ('direction', 'short'), ('trigger_type', 'take_profit'), ('qty', Decimal('1'))]:
            # Each corrupt-stop scenario needs its own ledger. A manual close
            # without exit fills deliberately blocks later entries under the
            # loss-history gate, so it cannot serve as cleanup between cases.
            self.e.ledger.close()
            self.e = Env(capabilities=Capabilities(venue_stop_orders=True))
            self.e.ready()
            coid, p = self.open(sid=field)
            record = next(o for o in self.e.mock.orders.values() if o['client_order_id'] == p['stop_coid'])
            record[field] = bad
            self.e.eng.check_protection()
            self.assertEqual(self.e.pos(coid)['state'], P.PROTECTION_PENDING.value)

    def test_rejected_stop_flattens_after_bounded_attempts(self):
        self.e.mock.script_place('accept', 'reject', 'reject', 'reject', 'accept')
        coid, p = self.open()
        self.assertIsNone(p['target_coid'])
        self.e.eng.check_protection(); self.e.eng.check_protection()
        self.assertEqual(self.e.pos(coid)['state'], P.CLOSE_PENDING.value)
        self.assertTrue(self.e.ledger.flags('POSITION_WITHOUT_PROTECTION'))

    def test_unreadable_acknowledged_stop_is_not_duplicated_and_times_out(self):
        coid, p = self.open()
        self.e.mock.get_open_orders = lambda *a, **kw: ([], None)
        self.e.mock.get_orders = lambda *a, **kw: ([], None)
        calls = self.e.mock.place_calls
        self.e.eng.check_protection(); self.e.eng.check_protection()
        self.assertEqual(self.e.mock.place_calls, calls)
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTION_PENDING.value)
        self.e.clock.advance(61)
        self.e.eng.check_protection()
        self.assertTrue(self.e.ledger.flags('POSITION_WITHOUT_PROTECTION'))

    def test_partial_entry_resizes_both_legs(self):
        r = self.e.eng.submit_entry(self.e.signal())
        q = Decimal(self.e.order(r.client_order_id)['qty'])
        self.e.eng.on_fill(self.e.mock.fill(r.client_order_id, Decimal('.012'), '77000'))
        old = self.e.ledger.get_protection(r.client_order_id)
        self.e.eng.on_fill(self.e.mock.fill(r.client_order_id, q - Decimal('.012'), '77100'))
        new = self.e.ledger.get_protection(r.client_order_id)
        for key in ('stop_coid', 'target_coid'):
            self.assertNotEqual(old[key], new[key])
            self.assertEqual(self.e.order(old[key])['state'], O.CANCELLED.value)
            self.assertEqual(Decimal(self.e.order(new[key])['qty']), q)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 2)

    def test_stop_fill_cancels_target_and_target_fill_cancels_stop(self):
        for key in ('stop_coid', 'target_coid'):
            coid, p = self.open(sid=key)
            o = self.e.order(p[key])
            self.e.eng.on_fill(self.e.mock.fill(o['client_order_id'], o['qty'], p['stop_price'] if key=='stop_coid' else p['tp_price']))
            other = p['target_coid' if key=='stop_coid' else 'stop_coid']
            self.assertEqual(self.e.pos(coid)['state'], P.CLOSED.value)
            self.assertEqual(self.e.order(other)['state'], O.CANCELLED.value)

    def test_partial_exit_resizes_remaining_stop_and_target(self):
        coid, old = self.open()
        o = self.e.order(old['target_coid'])
        half = Decimal('.012')
        self.e.eng.on_fill(self.e.mock.fill(o['client_order_id'], half, old['tp_price']))
        remaining = Decimal(o['qty']) - half
        self.e.eng.reconcile()
        new = self.e.ledger.get_protection(coid)
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)
        for key in ('stop_coid', 'target_coid'):
            self.assertEqual(self.e.order(old[key])['state'], O.CANCELLED.value)
            self.assertEqual(Decimal(self.e.order(new[key])['qty']), remaining)
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 2)

    def test_crash_after_stop_is_confirmed_before_target_recovers(self):
        self.e.mock.script_place('accept', 'accept', 'accept_lost')
        coid, old = self.open()
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTION_PENDING.value)
        self.assertTrue(self.e.restart().ok)
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)
        self.assertEqual(self.e.ledger.get_protection(coid)['stop_coid'], old['stop_coid'])
        self.assertEqual(self.e.ledger.get_protection(coid)['target_coid'], old['target_coid'])
        self.assertEqual(self.e.mock.place_calls, 3)

    def test_stop_migration_preserves_existing_target(self):
        self.e.mock.capabilities = Capabilities(venue_stop_orders=False)
        coid, old = self.open()
        self.assertEqual(old['stop_kind'], 'software')
        self.e.mock.capabilities = Capabilities(venue_stop_orders=True)
        self.e.eng.reconcile()
        new = self.e.ledger.get_protection(coid)
        self.assertEqual(new['stop_kind'], 'venue')
        self.assertEqual(new['target_coid'], old['target_coid'])
        self.assertTrue(new['stop_coid'])
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)

    def test_conditional_history_readback_works_when_not_in_open_order_list(self):
        real = self.e.mock.get_open_orders
        def without_triggers(*args, **kwargs):
            rows, cursor = real(*args, **kwargs)
            return [x for x in rows if not x.order_type.startswith('trigger')], cursor
        self.e.mock.get_open_orders = without_triggers
        coid, p = self.open()
        self.assertEqual(self.e.pos(coid)['state'], P.PROTECTED.value)
        self.assertIsNotNone(p['stop_coid'])

    def test_verified_stops_remain_managed_with_entries_paused(self):
        coid, p = self.open()
        self.e.eng.set_switches(allow_new_entries=False, reason='pause')
        self.assertTrue(self.e.eng.manage_existing()['managed'])
        self.assertEqual(self.e.mock.open_orders_count(ACCT), 2)
        self.assertEqual(self.e.eng.close_position(coid, 'manual'), 'CLOSE_SUBMITTED')
        for key in ('stop_coid', 'target_coid'):
            self.assertEqual(self.e.order(p[key])['state'], O.CANCELLED.value)


class TestYellowTriggerWire(unittest.TestCase):
    def test_adapter_sends_exact_stop_fields_and_close_direction(self):
        for direction in ('long', 'short'):
            c = client(entries=False)
            req = OrderRequest('acct-A','BTCUSDT-PERP','sell' if direction=='long' else 'buy',direction,
                               'trigger_market',Decimal('.01'),10,'why-stop',reduce_only=True,
                               time_in_force='ioc',trigger_price=Decimal('75000'),trigger_type='stop_loss')
            YellowTransport(c).place_order(req)
            body = c.s.calls[0]['body']
            self.assertEqual(body['trigger_price'], '75000')
            self.assertEqual(body['trigger_type'], 'stop_loss')
            self.assertEqual(body['type'], 'trigger_market')
            self.assertEqual(body['direction'], direction)
            self.assertTrue(body['reduce_only'])
            self.assertEqual(len(c.s.calls), 1)

    def test_trigger_invalid_or_missing_never_reaches_exchange(self):
        for price in (None, 'NaN', 'Infinity', '0', '-1'):
            c = client()
            with self.assertRaises(ValueError):
                c.place('BTCUSDT-PERP',False,'long','.01',typ='trigger_market',reduce_only=True,
                        coid='why-stop',trigger_price=price,trigger_type='stop_loss')
            self.assertEqual(c.s.calls, [])

    def test_documented_conditional_order_readback(self):
        o = normalise_order(dict(order_id='v1',type='stop_loss',state='trigger_wait',market='BTCUSDT-PERP',
                                side='sell',direction='long',origin_amount='.03',amount='.02',fill_amount='.01',
                                trigger_price='75000',price='0',reduce_only=True))
        self.assertEqual(o.state,'open')
        self.assertEqual(o.order_type,'trigger_market')
        self.assertEqual(o.trigger_type,'stop_loss')
        self.assertEqual(o.trigger_price,Decimal('75000'))
        self.assertEqual(o.qty-o.filled_qty,Decimal('.02'))
        self.assertTrue(o.reduce_only)
        self.assertFalse(normalise_order(dict(reduce_only='false')).reduce_only)
        self.assertEqual(normalise_order(dict(state='unrecognised')).state,'unknown')

    def test_cancel_does_not_treat_conditional_history_as_cancelled(self):
        c = client([Resp(body={}), Resp(body={'orders':[],'has_more':False}),
                    Resp(body={'orders':[{'order_id':'v1','state':'trigger_wait'}],'has_more':False})])
        import yellow_exec
        yellow_exec.time.sleep = lambda _: None
        self.assertEqual(c.cancel_verified('BTCUSDT-PERP','v1',tries=1), 'STILL_OPEN')
