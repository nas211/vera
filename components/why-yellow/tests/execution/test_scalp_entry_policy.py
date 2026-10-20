import dataclasses
import unittest
from decimal import Decimal as D
from tests.execution.test_scalp_emergency import EmergencyClose
from tests.execution.test_scalp_live import M,book


class CoinEntryPolicyTests(unittest.TestCase):
    setUp=EmergencyClose.setUp
    runtime=EmergencyClose.runtime
    seed_entry=EmergencyClose.seed_entry

    def active(self):
        self.r.ctx=dataclasses.replace(self.ctx,state='ACTIVE',entries_paused=False)
        book(self.r.model,self.clock)

    def entry(self,oid='next'):
        return dict(kind='maker',id=oid,market=M,buy=True,qty=1.,price=100.,lot=None,requested=self.clock())

    def exit_order(self,lid,oid='exit',price='99.9'):
        self.r.submit(dict(self.entry(oid),lot=lid,buy=False,kind='ioc',price=price,qty=self.r.model.lots[lid]['qty']))
        return self.r.store.orders()[-1]

    def close(self,price='99.9',fee='0'):
        lid=self.seed_entry()[0]
        row=self.exit_order(lid,price=price)
        self.mock.fill(row['venue_id'],'1',price,trade_id='closed',fee=fee)
        self.assertTrue(self.r.reconcile())

    def test_loss_blocks_only_same_coin_for_exactly_15_seconds(self):
        self.close();self.active()
        self.assertEqual(self.r.entry_policy.block_reason(M),'coin_loss_cooldown')
        self.assertIsNone(self.r.entry_policy.block_reason('ETHUSDT-PERP'))
        with self.assertRaisesRegex(ValueError,'coin_loss_cooldown'):self.r.submit(self.entry())
        self.clock.advance(14.999);book(self.r.model,self.clock)
        with self.assertRaisesRegex(ValueError,'coin_loss_cooldown'):self.r.submit(self.entry())
        self.clock.advance(.001);book(self.r.model,self.clock)
        self.r.submit(self.entry());self.assertEqual(self.r.store.orders()[-1]['state'],'open')

    def test_winner_and_break_even_have_no_cooldown(self):
        for price in ('100','100.1'):
            with self.subTest(price=price):
                parent='p'+price;lid=self.seed_entry(parent=parent)[0]
                row=self.exit_order(lid,oid='e'+price,price=price)
                self.mock.fill(row['venue_id'],'1',price,trade_id='win'+price)
                self.assertTrue(self.r.reconcile())
                self.assertIsNone(self.r.entry_policy.block_reason(M))

    def test_trading_fees_are_included(self):
        self.close(price='100',fee='.01')
        self.assertEqual(self.r.entry_policy.block_reason(M),'coin_loss_cooldown')

    def test_partial_loss_followed_by_profit_uses_whole_parent_result(self):
        lid=self.seed_entry()[0];row=self.exit_order(lid)
        self.mock.fill(row['venue_id'],'.4','99.9',trade_id='partial-loss')
        self.assertTrue(self.r.reconcile())
        self.assertEqual(self.r.model.entry_cooldowns,{})
        self.assertIsNone(self.r.entry_policy.block_reason(M))
        self.clock.advance(10)
        self.mock.fill(row['venue_id'],'.6','100.2',trade_id='partial-profit')
        self.assertTrue(self.r.reconcile());self.assertIsNone(self.r.entry_policy.block_reason(M))

    def test_partial_entry_lots_wait_for_final_exit_and_restart_preserves_timer(self):
        lids=self.seed_entry(fills=['.4','.6'])
        first=self.exit_order(lids[0],oid='first')
        self.mock.fill(first['venue_id'],'.4','99.9',trade_id='first-close');self.r.reconcile()
        self.assertEqual(self.r.model.entry_cooldowns,{})
        self.clock.advance(5)
        last=self.exit_order(lids[1],oid='last')
        self.mock.fill(last['venue_id'],'.6','99.9',trade_id='last-close');self.r.reconcile()
        until=self.clock()+15
        self.assertEqual(self.r.model.entry_cooldowns[M],until)
        self.clock.advance(4);restarted=self.runtime();self.assertTrue(restarted.reconcile())
        self.assertEqual(restarted.model.entry_cooldowns[M],until)
        self.assertEqual(restarted.entry_policy.block_reason(M),'coin_loss_cooldown')

    def test_resting_entry_allows_more_entries_in_both_directions(self):
        self.active();self.r.submit(self.entry('first'))
        self.r.submit(dict(self.entry('second'),buy=False))
        self.r.submit(self.entry('third'))
        self.assertEqual(self.mock.place_calls,3)
        self.assertEqual(self.r.entry_policy.excess_entries(),set())

    def test_open_position_allows_new_entry_and_preserves_exit(self):
        lid=self.seed_entry()[0];self.active()
        self.r.submit(self.entry())
        self.assertEqual(self.r.store.orders()[-1]['state'],'open')
        row=self.exit_order(lid);self.assertEqual(row['state'],'open')

    def test_proposals_allow_a_grid_on_one_coin(self):
        self.active();self.r.model.maintain()
        proposals=[p for p in self.r.model.intents if not p.get('lot')]
        self.assertGreater(len(proposals),1)
        self.assertEqual({p['market'] for p in proposals},{M})
        self.assertEqual({p['buy'] for p in proposals},{True,False})

    def test_existing_stacked_positions_preserve_entries_and_exits(self):
        a=self.seed_entry('legacy-a')[0];b=self.seed_entry('legacy-b')[0]
        exitrow=self.exit_order(a)
        p=dict(self.entry('legacy-pending'),filled_qty='0')
        req=self.r._request('pending',p);ack=self.mock.place_order(req)
        with self.r.store.db:self.r.store.put('pending','open',p,ack.venue_order_id)
        self.assertEqual(self.r.entry_policy.excess_entries(),set())
        self.assertEqual(len(self.r.model.lots),2)
        self.assertNotIn(exitrow['payload']['id'],self.r.entry_policy.excess_entries())

    def test_loss_with_another_open_lot_cancels_entries_but_preserves_exits(self):
        losing=self.seed_entry('losing')[0]
        remaining=self.seed_entry('remaining')[0]
        self.active()
        self.r.submit(self.entry('pending'))
        remaining_exit=self.exit_order(remaining,oid='remaining-exit',price='100.1')
        losing_exit=self.exit_order(losing,oid='losing-exit')
        self.mock.fill(losing_exit['venue_id'],'1','99.9',trade_id='overlap-loss')
        self.assertTrue(self.r.reconcile())
        self.assertEqual(self.r.entry_policy.excess_entries(),{'pending'})
        self.assertNotIn(remaining_exit['payload']['id'],self.r.entry_policy.excess_entries())
        self.assertIn(remaining,self.r.model.lots)
        with self.assertRaisesRegex(ValueError,'coin_loss_cooldown'):
            self.r.submit(self.entry('blocked'))
        self.clock.advance(15);book(self.r.model,self.clock)
        self.r.submit(self.entry('after-cooldown'))
        self.assertEqual(self.r.store.orders()[-1]['state'],'open')

    def test_strategy_cooldown_blocks_grid_but_still_proposes_exit(self):
        self.seed_entry();self.active()
        self.r.model.entry_cooldowns[M]=self.clock()+15
        self.r.model.maintain()
        self.assertTrue(any(p.get('lot') for p in self.r.model.intents))
        self.assertFalse(any(not p.get('lot') for p in self.r.model.intents))

    def test_status_reports_overlap_with_loss_cooldown(self):
        status=self.r.status()
        self.assertFalse(status['one_trade_per_coin'])
        self.assertEqual(status['loss_cooldown_seconds'],15)
