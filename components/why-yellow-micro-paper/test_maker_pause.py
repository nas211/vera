import unittest
from unittest.mock import patch
import runner,v2,maker_only
class PauseTests(unittest.TestCase):
 def setUp(self):
  for ctx in [patch.dict(runner.CONFIG,{}),patch.dict(v2.RULES,{}),patch.object(runner,'MARKETS',list(runner.MARKETS)),patch.object(v2,'EXCLUDED_ENTRIES',set(v2.EXCLUDED_ENTRIES)),patch.object(v2,'ACTIVE_MARKETS',list(v2.ACTIVE_MARKETS))]:
   ctx.start();self.addCleanup(ctx.stop)
  maker_only.configure();self.now=1800000000.;self.m='TAOUSDT-PERP'
  self.p=maker_only.MakerOnlyPaper({m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5) for m in runner.MARKETS},clock=lambda:self.now,record=False)
  self.p.connected=True;self.p.books[self.m]=dict(bids={100.:10},asks={100.02:10},received=self.now)
 def test_tao_entry_pause_allows_existing_exit(self):
  self.assertIn(self.m,runner.MARKETS);self.assertNotIn(self.m,v2.ACTIVE_MARKETS)
  self.assertEqual(self.p.entry_reason(self.m,True),'market_removed')
  self.assertIsNone(self.p.place(self.m,True,100,1))
  self.p.lots['old']=dict(market=self.m,buy=True,entry=100.,qty=1.,opened=self.now,exit_id=None)
  self.assertIsNotNone(self.p.place(self.m,False,100.1,1,'old'))
  self.assertTrue(self.p.snapshot()['markets'][self.m]['exit_only'])
 def test_target_timeout_and_maker_fill_semantics(self):
  lot=dict(market=self.m,buy=True,entry=100.,qty=1.,opened=self.now,exit_id=None)
  self.assertAlmostEqual(self.p.exit_price(lot)[0],100.1)
  self.now+=91
  self.assertEqual(self.p.exit_price(lot),(100.02,'passive_loss_or_time_exit'))
  self.p.lots['old']=lot;self.p.maintain();self.now+=.3;self.p.maintain()
  self.assertEqual(self.p.stats['closed_lots'],0)
  self.assertIn('old',self.p.lots)
 def test_resting_profit_quote_does_not_chase_market(self):
  for buy in (True,False):
   with self.subTest(buy=buy):
    self.p.orders.clear();self.p.lots.clear();self.p.halted=True
    self.p.books[self.m]=dict(bids={99.99:10},asks={100.01:10},received=self.now)
    self.p.lots['old']=dict(market=self.m,buy=buy,entry=100.,qty=1.,opened=self.now,exit_id=None)
    self.p.maintain();oid=self.p.lots['old']['exit_id'];self.now+=.3;self.p.maintain()
    self.now+=6
    self.p.books[self.m]=dict(bids={100.11 if buy else 99.87:10},asks={100.13 if buy else 99.89:10},received=self.now)
    self.p.maintain()
    self.assertIsNone(self.p.orders[oid]['cancel_at'])
    self.assertAlmostEqual(self.p.orders[oid]['price'],100.1 if buy else 99.9)
if __name__=='__main__':unittest.main()
