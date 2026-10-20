import unittest
from runner import MARKETS
from v2 import PaperV2,RULES,EXCLUDED_ENTRIES,ACTIVE_MARKETS

class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.now=1800000000.
        self.p=PaperV2({m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5) for m in MARKETS},clock=lambda:self.now,record=False)
        self.p.connected=True
        for m in MARKETS:
            self.p.books[m]=dict(bids={100.:10},asks={100.1:10},received=self.now)
            self.p.history[m].append((self.now-31,100.05))
    def test_twenty_dollar_margin_and_fifty_slot_cap(self):
        from unittest.mock import patch
        import runner
        from runner import CONFIG
        with patch.object(runner,'MARKETS',ACTIVE_MARKETS),patch.dict(CONFIG,{'levels_per_side':4,'per_market_cap':1600,'entry_margin_usd':20,'max_entry_slots':50}),patch.dict(RULES,{'side_notional_limit':800}):
            self.p.maintain()
            entries=[o for o in self.p.orders.values() if not o['lot']]
            self.assertEqual(len(entries),50)
            self.assertEqual(self.p.occupied_entry_slots(),50)
            self.assertLessEqual(self.p.exposure(),10000)
            for o in entries:
                self.assertGreater(o['price']*o['qty'],198)
                self.assertLessEqual(o['price']*o['qty'],200)
            # The filled piece and unfilled remainder of one entry share a slot.
            parent=entries[0]
            self.p.lots['partial']=dict(market=parent['market'],buy=parent['buy'],entry=parent['price'],qty=.1,entry_order=parent['id'])
            self.assertEqual(self.p.occupied_entry_slots(),50)

    def test_large_quotes_concentrate_budget_without_increasing_exposure(self):
        from unittest.mock import patch
        import runner
        from runner import CONFIG
        with patch.object(runner,'MARKETS',ACTIVE_MARKETS),patch.dict(CONFIG,{'levels_per_side':1,'per_market_cap':10000/7}),patch.dict(RULES,{'side_notional_limit':10000/14}):
            self.p.maintain()
            entries=[o for o in self.p.orders.values() if not o['lot']]
            self.assertEqual(len(entries),14)
            self.assertLessEqual(self.p.exposure(),10000)
            self.assertGreater(self.p.exposure(),9980)
            for o in entries:
                self.assertGreater(o['price']*o['qty'],700)
                self.assertLessEqual(o['price']*o['qty'],10000/14)

    def test_removed_coins_block_entries_but_allow_maker_exits(self):
        self.assertEqual(len(ACTIVE_MARKETS),7)
        for m in EXCLUDED_ENTRIES:
            self.assertEqual(self.p.entry_reason(m,True),'market_removed')
            self.assertIsNone(self.p.place(m,True,100,1))
            self.assertIsNone(self.p.place(m,False,100.1,1))
            lid='old-'+m
            self.p.lots[lid]=dict(market=m,buy=True,entry=101,qty=1,opened=self.now-100,exit_id=None)
            oid=self.p.place(m,False,102,1,lid)
            self.assertIsNotNone(oid)
            self.assertEqual(self.p.orders[oid]['lot'],lid)

    def test_adverse_trend_blocks_buy_and_allows_short(self):
        m=MARKETS[0];self.p.history[m][0]=(self.now-31,100.2)
        self.assertEqual(self.p.entry_reason(m,True),'adverse_trend')
        self.assertIsNone(self.p.entry_reason(m,False))
        self.assertIsNone(self.p.place(m,True,100,1))
    def test_orderbook_pressure_blocks_wrong_side(self):
        m=MARKETS[0];self.p.books[m]['asks']={100.1:100}
        self.assertEqual(self.p.entry_reason(m,True),'adverse_orderbook')
        self.assertIsNone(self.p.entry_reason(m,False))
    def test_existing_inventory_limits_additional_same_side(self):
        m=MARKETS[0]
        self.p.lots['old']=dict(market=m,buy=True,entry=100,qty=5,opened=self.now,exit_id=None)
        self.assertIsNone(self.p.place(m,True,100,1))
        self.assertIsNotNone(self.p.place(m,False,100.1,1))
    def test_losing_position_gets_passive_loss_exit(self):
        m=MARKETS[0]
        self.p.lots['loser']=dict(market=m,buy=True,entry=101,qty=1,opened=self.now,exit_id=None)
        oid=self.p.place(m,False,102,1,'loser')
        self.assertAlmostEqual(self.p.orders[oid]['price'],100.1)
        self.assertGreater(self.p.orders[oid]['price'],max(self.p.books[m]['bids']))
        self.assertEqual(self.p.orders[oid]['exit_reason'],'passive_loss_or_time_exit')
    def test_timeout_reprices_existing_profit_only_exit(self):
        m=MARKETS[0]
        self.p.lots['old']=dict(market=m,buy=True,entry=100.05,qty=1,opened=self.now,exit_id=None)
        oid=self.p.place(m,False,101,1,'old');self.p.lots['old']['exit_id']=oid
        # Market falls and position ages; cancel first, then replace after latency.
        self.now+=100;self.p.books[m]=dict(bids={99.9:10},asks={99.91:10},received=self.now)
        self.p.maintain();self.assertIsNotNone(self.p.orders[oid]['cancel_at'])
        self.now+=.3;self.p.maintain()
        new=self.p.orders[self.p.lots['old']['exit_id']]
        self.assertAlmostEqual(new['price'],99.91)
        self.assertLess(new['price'],self.p.lots['old']['entry'])
    def test_adverse_flow_cancels_existing_entry(self):
        m=MARKETS[0];oid=self.p.place(m,True,100,1)
        self.p.books[m]['asks']={100.1:100}
        self.p.maintain();self.assertIsNotNone(self.p.orders[oid]['cancel_at'])
    def test_reconnect_warms_up_before_new_entries(self):
        self.p.disconnect();self.assertFalse(self.p.history)
        m=MARKETS[0];self.p.books[m]=dict(bids={100.:10},asks={100.1:10},received=self.now)
        self.assertEqual(self.p.entry_reason(m,True),'warmup')
    def test_paper_loss_is_actually_booked_not_erased(self):
        import datetime
        m=MARKETS[0];self.p.lots['loss']=dict(market=m,buy=True,entry=101,qty=1,opened=self.now,exit_id=None)
        self.p.maintain();self.now+=.3;self.p.maintain()
        self.p.trade(m,dict(price='100.2',amount='1',direction='buy',executed_at=datetime.datetime.fromtimestamp(self.now,datetime.timezone.utc).isoformat()))
        self.assertAlmostEqual(self.p.stats['realized_pnl'],-.9)
        self.assertEqual(self.p.stats['fees'],0)
        self.assertNotIn('loss',self.p.lots)

if __name__=='__main__':unittest.main()
