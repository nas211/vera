import unittest
from unittest.mock import patch
import runner,v2,taker_exit

class TakerExitTests(unittest.TestCase):
    def setUp(self):
        for ctx in [patch.dict(runner.CONFIG,{}),patch.dict(v2.RULES,{}),patch.object(runner,'MARKETS',list(runner.MARKETS)),patch.object(v2,'ACTIVE_MARKETS',list(v2.ACTIVE_MARKETS)),patch.object(v2,'EXCLUDED_ENTRIES',set(v2.EXCLUDED_ENTRIES))]:
            ctx.start();self.addCleanup(ctx.stop)
        taker_exit.configure();self.now=1800000000.;self.m=runner.MARKETS[0]
        self.p=taker_exit.MakerTakerPaper({self.m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5)},clock=lambda:self.now,record=False)
        self.p.connected=True;self.book({100.1:2},{100.2:2})
    def book(self,bids,asks):self.p.books[self.m]=dict(bids=bids,asks=asks,received=self.now)
    def lot(self,lid='lot',buy=True,entry=100,qty=1,age=0):
        self.p.lots[lid]=dict(market=self.m,buy=buy,entry=entry,qty=qty,opened=self.now-age,exit_id=None)
    def test_target_uses_executable_bid_not_mid(self):
        self.lot();self.book({100.0:10},{100.3:10})
        self.assertIsNone(self.p.trigger(self.p.lots['lot']))
    def test_latency_and_vip_fee_on_actual_proceeds(self):
        self.lot();self.book({100.11:2},{100.12:2});self.p.maintain()
        self.assertIn('lot',self.p.lots);self.assertFalse(self.p.orders)
        self.now+=.26;self.p.maintain()
        self.assertNotIn('lot',self.p.lots)
        self.assertAlmostEqual(self.p.stats['fees'],100.11*.00015)
        self.assertAlmostEqual(self.p.stats['realized_pnl'],.11-100.11*.00015)
    def test_depth_slippage_partial_fill_and_no_double_spend(self):
        self.lot(qty=3);self.book({100.1:1,100.0:.5},{100.2:10})
        self.p.execute_exit('lot',dict(reason='TIMEOUT'))
        self.assertAlmostEqual(self.p.lots['lot']['qty'],1.5)
        self.assertAlmostEqual(self.p.stats['fees'],150.1*.00015)
        self.assertAlmostEqual(self.p.stats['realized_pnl'],.1-150.1*.00015)
        self.p.execute_exit('lot',dict(reason='TIMEOUT'))
        self.assertAlmostEqual(self.p.lots['lot']['qty'],1.5)
        self.lot('other');self.p.execute_exit('other',dict(reason='STOP'))
        self.assertEqual(self.p.lots['other']['qty'],1)
    def test_short_exit_buys_asks_and_charges_fee(self):
        self.lot(buy=False,entry=101);self.p.execute_exit('lot',dict(reason='TARGET'))
        self.assertAlmostEqual(self.p.stats['realized_pnl'],.8-100.2*.00015)
    def test_stale_book_blocks_market_fill(self):
        self.lot(age=100);self.p.books[self.m]['received']=self.now-20
        self.p.maintain();self.assertFalse(self.p.market_exits);self.assertIn('lot',self.p.lots)
    def test_stop_and_time_triggers(self):
        self.lot(entry=101);self.assertEqual(self.p.trigger(self.p.lots['lot']),'STOP')
        self.lot(entry=100.1,age=91);self.assertEqual(self.p.trigger(self.p.lots['lot']),'TIMEOUT')
    def test_no_passive_exit_is_created(self):
        self.lot();self.assertIsNone(self.p.place(self.m,False,100.2,1,'lot'))
        self.assertFalse(self.p.orders)

if __name__=='__main__':unittest.main()
