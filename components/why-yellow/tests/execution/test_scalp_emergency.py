import dataclasses
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal as D

from tests.execution.harness import FakeClock, make_metadata, NETWORK_ATTEMPTS
from tests.execution.test_scalp_live import F, M, book
from execution.mock_adapter import MockYellowAdapter
from execution.sizing import HARD_LOSS_LIMIT_ACCOUNT
from customer.context import CustomerExecutionContext
from customer.scalp.runtime import Runtime
from customer.scalp.strategy import CONFIG, RULES, BASE, LiveStrategy


class EmergencyClose(unittest.TestCase):
    def setUp(self):
        # These admission tests isolate the emergency guard. Production scalp
        # availability remains disabled and is covered by test_scalp_unavailable.
        availability=patch('customer.scalp.availability.SCALP_LIVE_AVAILABLE',True)
        availability.start();self.addCleanup(availability.stop)
        self.clock=FakeClock()
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx=CustomerExecutionContext('user', HARD_LOSS_LIMIT_ACCOUNT.split(':',1)[1], 'fake', D(100), 'PAUSED', True, trading_style='scalp')
        self.mock=MockYellowAdapter(clock=self.clock)
        self.mock.set_account(self.ctx.namespace,'1000','1000')
        self.r=self.runtime()
        self.assertTrue(self.r.reconcile())

    def runtime(self):
        r=Runtime(self.ctx,self.mock,make_metadata(),self.tmp.name,self.clock)
        self.addCleanup(r.store.db.close)
        r.model.filters=F
        book(r.model,self.clock)
        return r

    def seed_entry(self, parent='entry', qty='1', fills=None, buy=True, fee='0'):
        # Seed confirmed exchange inventory independently of entry admission.
        p=dict(kind='maker',id=parent,market=M,buy=buy,price='100',qty=qty,lot=None,
               requested=self.clock(),filled_qty='0')
        req=self.r._request(parent,p)
        ack=self.mock.place_order(req)
        with self.r.store.db:
            self.r.store.put(parent,'open',p,ack.venue_order_id)
        for i,q in enumerate(fills or [qty]):
            self.mock.fill(ack.venue_order_id,q,'100',trade_id=f'{parent}-{i}',fee=fee)
        self.r.reconcile()
        return [lid for lid,l in self.r.model.lots.items() if l['entry_order']==parent]

    def trigger(self, bid=98.9, ask=99.1):
        book(self.r.model,self.clock,bid,ask)
        self.r.emergency.check()
        self.r._set_pending_exits()

    def closes(self):
        return [r for r in self.r.store.orders() if r['payload'].get('reason')=='EMERGENCY_STOP']

    def test_no_action_below_one_dollar_and_original_rules_unchanged(self):
        self.seed_entry()
        self.trigger(99.5,99.7)
        self.r.emergency.process()
        self.assertFalse(self.r.emergency.engaged)
        self.assertEqual(self.closes(),[])
        self.assertEqual(CONFIG['entry_margin_usd'],10)
        self.assertEqual((RULES['max_hold_seconds'],RULES['adverse_exit_bps']),(120,5))
        self.assertEqual(LiveStrategy.exit_price.__code__.co_code,BASE.exit_price.__code__.co_code)

    def test_other_accounts_existing_lots_have_two_dollar_guard(self):
        self.ctx=dataclasses.replace(self.ctx,account_id='future-account')
        self.mock.set_account(self.ctx.namespace,'1000','1000')
        self.r=self.runtime()
        self.assertTrue(self.r.reconcile())
        self.assertTrue(self.r.emergency.enabled)
        self.assertEqual(self.r.emergency.loss_limit,D(2))
        self.seed_entry()
        self.trigger(98.5,98.7)
        self.assertFalse(self.r.emergency.engaged)
        self.trigger(97.9,98.1)
        self.assertTrue(self.r.emergency.engaged)

    def test_paused_account_emergency_closes_at_market_without_resizing(self):
        self.seed_entry()
        self.trigger()
        self.r.emergency.process()
        row=self.closes()[0]
        order=self.mock.orders[row['venue_id']]
        self.assertEqual((order['qty'],order['order_type'],order['reduce_only']),(D(1),'market',True))
        self.assertTrue(self.r.emergency.engaged)
        self.mock.fill(row['venue_id'],'1','98.9',trade_id='emergency-fill')
        self.r.reconcile()
        self.assertFalse(self.r.emergency.engaged)
        self.assertEqual(self.r.results()['trades'][0]['reason'],'EMERGENCY_STOP')

    def test_short_loss_uses_ask_and_long_loss_uses_bid(self):
        self.seed_entry(buy=False)
        self.trigger(100.8,101.1)
        self.assertTrue(self.r.emergency.engaged)
        self.r.emergency.process()
        self.assertTrue(self.closes()[0]['payload']['buy'])

    def test_partial_entry_lots_share_one_parent_loss_trigger(self):
        self.seed_entry(fills=['.5','.5'])
        self.trigger()
        self.assertEqual(self.r.emergency.triggered(),{'entry'})
        self.r.emergency.process()
        self.assertEqual(len(self.closes()),2)
        self.assertEqual(sum(D(r['payload']['qty']) for r in self.closes()),D(1))

    def test_separate_entries_do_not_share_one_dollar_budget(self):
        self.seed_entry('first',qty='.5')
        self.seed_entry('second',qty='.5')
        self.trigger()
        self.assertFalse(self.r.emergency.engaged)

    def test_fee_estimate_can_trigger_at_less_than_one_dollar_gross_loss(self):
        self.seed_entry(fee='.02')
        self.trigger(99.02,99.22)
        self.assertTrue(self.r.emergency.engaged)

    def test_cancel_ack_is_not_enough_to_send_close(self):
        lid=self.seed_entry()[0]
        self.r.submit(dict(kind='maker',id='normal-exit',market=M,buy=False,price=100.2,qty=1,lot=lid,requested=self.clock()))
        self.trigger()
        self.mock.cancel_script.append('ack_stays_open')
        self.r.emergency.process()
        self.assertEqual(self.closes(),[])
        self.r.emergency.process()
        self.assertEqual(len(self.closes()),1)
        self.assertGreaterEqual(self.mock.cancel_calls,2)

    def test_partial_exit_during_cancel_closes_only_remaining_quantity(self):
        lid=self.seed_entry()[0]
        self.r.submit(dict(kind='maker',id='normal-exit',market=M,buy=False,price=100.2,qty=1,lot=lid,requested=self.clock()))
        exitrow=self.r.store.orders()[-1]
        original=self.mock.cancel_order
        def cancel(account,market,vid):
            if vid==exitrow['venue_id']:
                self.mock.fill(vid,'.4','100.2',trade_id='cancel-race-fill')
            return original(account,market,vid)
        self.mock.cancel_order=cancel
        self.trigger()
        self.r.emergency.process()
        self.assertEqual(D(self.closes()[0]['payload']['qty']),D('.6'))

    def test_lost_close_ack_is_never_blindly_retried_even_after_restart(self):
        self.seed_entry()
        self.trigger()
        self.mock.place_script.append('accept_lost')
        self.r.emergency.process()
        calls=self.mock.place_calls
        self.r.emergency.process()
        self.assertEqual(self.mock.place_calls,calls)
        self.r=self.runtime()
        self.assertTrue(self.r.reconcile())
        self.r.emergency.process()
        self.assertEqual(self.mock.place_calls,calls)

    def test_latched_emergency_survives_restart_and_price_recovery(self):
        self.seed_entry()
        self.trigger()
        self.r=self.runtime()
        self.r.reconcile()
        self.r.emergency.process()
        self.assertEqual(len(self.closes()),1)

    def test_stale_book_cannot_be_used_as_current_loss(self):
        self.seed_entry()
        book(self.r.model,self.clock,98.9,99.1)
        self.clock.advance(16)
        self.r.emergency.check()
        self.assertFalse(self.r.emergency.engaged)
        self.assertEqual(self.r.last_error,'scalp_emergency_market_data_unavailable')

    def feed_alive(self):
        # Another coin's increment proves the socket is still delivering.
        self.r.model.books['ETHUSDT-PERP']={'bids':{2000.:1},'asks':{2000.1:1},'received':self.clock()}

    def test_quiet_book_on_live_feed_is_current_loss(self):
        self.seed_entry()
        book(self.r.model,self.clock,98.9,99.1)
        self.clock.advance(20);self.feed_alive()
        self.r.emergency.check()
        self.assertTrue(self.r.emergency.engaged)
        self.assertNotEqual(self.r.last_error,'scalp_emergency_market_data_unavailable')

    def test_quiet_book_on_live_feed_does_not_flag_unavailable_below_limit(self):
        self.seed_entry()
        book(self.r.model,self.clock,99.5,99.7)
        self.clock.advance(20);self.feed_alive()
        self.r.emergency.check()
        self.assertFalse(self.r.emergency.engaged)
        self.assertNotEqual(self.r.last_error,'scalp_emergency_market_data_unavailable')

    def test_book_unchanged_too_long_is_unavailable_even_on_live_feed(self):
        self.seed_entry()
        book(self.r.model,self.clock,98.9,99.1)
        self.clock.advance(121);self.feed_alive()
        self.r.emergency.check()
        self.assertFalse(self.r.emergency.engaged)
        self.assertEqual(self.r.last_error,'scalp_emergency_market_data_unavailable')

    def test_disconnected_feed_is_unavailable(self):
        self.seed_entry()
        book(self.r.model,self.clock,98.9,99.1);self.r.model.connected=False
        self.r.emergency.check()
        self.assertFalse(self.r.emergency.engaged)
        self.assertEqual(self.r.last_error,'scalp_emergency_market_data_unavailable')

    def test_approved_account_enters_without_resizing_and_guard_enabled(self):
        self.r.ctx=dataclasses.replace(self.ctx,state='ACTIVE',entries_paused=False)
        p=dict(kind='maker',id='new-entry',market=M,buy=True,price=100,qty=1,lot=None,requested=self.clock())
        with patch.object(self.mock,'place_order',wraps=self.mock.place_order) as place:
            self.r.submit(p)
        self.assertEqual(place.call_args.args[0].leverage,10)
        row=self.r.store.orders()[0]
        order=self.mock.orders[row['venue_id']]
        self.assertEqual((order['qty'],order['price']),(D(1),D(100)))
        self.assertTrue(self.r.emergency.enabled)

    def test_approved_account_fails_closed_if_emergency_guard_disabled(self):
        self.r.ctx=dataclasses.replace(self.ctx,state='ACTIVE',entries_paused=False)
        self.r.emergency.enabled=False
        p=dict(kind='maker',id='new-entry',market=M,buy=True,price=100,qty=1,lot=None,requested=self.clock())
        with self.assertRaisesRegex(ValueError,'account_hard_loss_policy_requires_protected_execution'):
            self.r.submit(p)
        self.assertEqual(self.mock.place_calls,0)

    def test_normal_take_profit_still_cancels_maker_and_captures_profit(self):
        lid=self.seed_entry()[0]
        self.r.frame()
        maker=next(r for r in self.r.store.orders() if r['payload'].get('lot')==lid)
        self.assertEqual(maker['payload']['kind'],'maker')
        self.clock.advance(.5)
        book(self.r.model,self.clock,100.2,100.4)
        self.r.frame()
        self.clock.advance(.3)
        self.r.frame()
        profit=[r for r in self.r.store.orders() if r['payload']['kind']=='ioc']
        self.assertEqual(len(profit),1)
        self.assertEqual((D(profit[0]['payload']['qty']),D(profit[0]['payload']['price'])),(D(1),D('100.2')))
        self.assertEqual(next(r for r in self.r.store.orders() if r['id']==maker['id'])['state'],'cancelled')
        self.assertEqual(self.closes(),[])
        self.mock.fill(profit[0]['venue_id'],'1','100.2',trade_id='normal-profit',fee='.01503')
        self.r.reconcile()
        self.assertEqual(self.r.model.lots,{})
        self.assertAlmostEqual(self.r.model.stats['realized_pnl'],.18497)

    def test_emergency_in_progress_blocks_new_entries(self):
        self.seed_entry()
        self.trigger()
        self.r.ctx=dataclasses.replace(self.ctx,state='ACTIVE',entries_paused=False)
        with self.assertRaisesRegex(ValueError,'scalp_emergency_close_pending'):
            self.r.submit(dict(kind='maker',id='blocked-entry',market=M,buy=True,price=100,qty=1,lot=None,requested=self.clock()))

    def test_fill_reconciliation_is_idempotent_for_emergency_cashflows(self):
        self.seed_entry(fee='.02')
        self.r.reconcile()
        self.r.reconcile()
        row=self.r.store.db.execute('select net from emergency_pnl where entry_id=?',('entry',)).fetchone()
        self.assertEqual(D(row[0]),D('-.02'))
        self.assertEqual(NETWORK_ATTEMPTS['count'],0)

    def test_confirmed_partial_exit_loss_counts_toward_parent_threshold(self):
        lid=self.seed_entry()[0]
        self.r.submit(dict(kind='ioc',id='partial-exit',market=M,buy=False,price=98.8,qty=.5,lot=lid,requested=self.clock()))
        row=self.r.store.orders()[-1]
        self.mock.fill(row['venue_id'],'.5','98.8',trade_id='partial-loss')
        self.r.reconcile()
        self.trigger(99.1,99.3)
        self.assertTrue(self.r.emergency.engaged)
        self.r.emergency.process()
        self.assertEqual(D(self.closes()[0]['payload']['qty']),D('.5'))

    def test_full_frame_runs_emergency_before_sending_normal_exits(self):
        self.seed_entry()
        book(self.r.model,self.clock,98.9,99.1)
        self.r.frame()
        exits=[r for r in self.r.store.orders() if r['payload'].get('lot')]
        self.assertEqual(len(exits),1)
        self.assertEqual(exits[0]['payload']['reason'],'EMERGENCY_STOP')
        self.assertFalse(self.r.status()['entries_allowed'])

    def test_three_definitive_close_rejections_stop_retries_and_flag_attention(self):
        self.seed_entry()
        self.trigger()
        self.mock.place_script.extend(['reject']*3)
        for i in range(5):
            self.r.emergency.process()
        self.assertEqual(len(self.closes()),3)
        self.assertEqual(self.r.last_error,'scalp_emergency_close_requires_attention')
