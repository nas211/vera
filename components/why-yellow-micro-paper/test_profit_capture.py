import datetime,unittest
from unittest.mock import patch
import runner,v2,profit_capture
class ProfitTests(unittest.TestCase):
 def setUp(self):
  for ctx in [patch.dict(runner.CONFIG,{}),patch.dict(v2.RULES,{}),patch.object(runner,'MARKETS',list(runner.MARKETS)),patch.object(v2,'ACTIVE_MARKETS',list(v2.ACTIVE_MARKETS)),patch.object(v2,'EXCLUDED_ENTRIES',set(v2.EXCLUDED_ENTRIES))]:
   ctx.start();self.addCleanup(ctx.stop)
  profit_capture.configure();self.now=1800000000.;self.m=runner.MARKETS[0]
  self.p=profit_capture.ProfitCapturePaper({self.m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5)},clock=lambda:self.now,record=False)
  self.p.connected=True;self.p.halted=True;self.book(100.12,100.14);self.lot()
 def book(self,bid,ask,qty=10):self.p.books[self.m]=dict(bids={bid:qty},asks={ask:qty},received=self.now)
 def lot(self,lid='lot',buy=True,qty=1,age=0):self.p.lots[lid]=dict(market=self.m,buy=buy,entry=100.,qty=qty,opened=self.now-age,exit_id=None,entry_order='entry')
 def advance(self):self.now+=.26;self.p.maintain()
 def test_net_target_after_fee_and_latency(self):
  self.p.maintain();self.assertIn('lot',self.p.lots);self.assertIn('lot',self.p.profit_requests)
  self.advance();self.assertNotIn('lot',self.p.lots)
  self.assertAlmostEqual(self.p.stats['realized_pnl'],.12-100.12*.00015)
  self.assertGreaterEqual(self.p.stats['realized_pnl'],.1)
  self.assertEqual(self.p.stats['net_target_ioc_fills'],1)
 def test_midpoint_touch_or_gross_target_insufficient(self):
  for bid,ask in [(100.,100.3),(100.1,100.14),(100.11,100.14)]:
   self.book(bid,ask);self.p.maintain();self.assertFalse(self.p.profit_requests)
 def test_price_reversal_after_latency_does_not_sell_below_target(self):
  self.p.maintain();self.book(99.9,99.92);self.advance()
  self.assertIn('lot',self.p.lots);self.assertEqual(self.p.stats['realized_pnl'],0)
  self.assertTrue(any(o['lot']=='lot' for o in self.p.orders.values()))
 def test_partial_fill_and_shared_depth(self):
  self.book(100.12,100.14,qty=.4);self.p.books[self.m]['bids'][100.1]=10;self.lot('other')
  self.p.maintain();self.advance()
  self.assertAlmostEqual(self.p.lots['lot']['qty'],.6);self.assertEqual(self.p.lots['other']['qty'],1)
  self.assertEqual(self.p.stats['closed_lots'],0)
  self.p.maintain();self.advance();self.assertAlmostEqual(self.p.lots['lot']['qty'],.6)
 def test_short_net_target(self):
  self.lot(buy=False);self.book(99.86,99.88);self.p.maintain();self.advance()
  self.assertNotIn('lot',self.p.lots);self.assertAlmostEqual(self.p.stats['realized_pnl'],.12-99.88*.00015)
 def test_stale_book_no_ioc(self):
  self.p.books[self.m]['received']=self.now-20;self.p.maintain();self.assertFalse(self.p.profit_requests)
 def test_stale_at_execution_no_fill(self):
  self.p.maintain();self.now+=20;self.p.maintain();self.assertIn('lot',self.p.lots);self.assertEqual(self.p.stats['realized_pnl'],0)
 def test_maker_fill_during_cancel_latency_does_not_double_exit(self):
  self.book(100.,100.02);self.p.maintain();self.advance()
  self.book(100.12,100.14);self.p.maintain();self.now+=.1
  self.p.trade(self.m,dict(price='100.11',amount='1',direction='buy',executed_at=datetime.datetime.fromtimestamp(self.now,datetime.timezone.utc).isoformat()))
  self.advance();self.assertEqual(self.p.stats['closed_lots'],1);self.assertEqual(self.p.stats['exit_fills'],1)
  self.assertAlmostEqual(self.p.stats['realized_pnl'],.1);self.assertEqual(self.p.stats['fees'],0)
 def test_timeout_still_prioritizes_executable_profit(self):
  self.lot(age=100);self.p.maintain();self.assertIn('lot',self.p.profit_requests);self.advance()
  self.assertGreaterEqual(self.p.stats['realized_pnl'],.1)
 def test_disconnect_retains_lot_clears_requests(self):
  self.p.maintain();self.p.disconnect();self.assertIn('lot',self.p.lots);self.assertFalse(self.p.profit_requests)
 def test_tao_remains_paused(self):self.assertIn('TAOUSDT-PERP',v2.EXCLUDED_ENTRIES)
 def test_sui_entries_paused_exits_kept(self):
  self.assertIn('SUIUSDT-PERP',v2.EXCLUDED_ENTRIES);self.assertNotIn('SUIUSDT-PERP',v2.ACTIVE_MARKETS)
  self.assertIn('SUIUSDT-PERP',runner.MARKETS)
 def test_original_target_and_hold(self):
  self.assertEqual(runner.CONFIG['target_bps'],10);self.assertEqual(v2.RULES['max_hold_seconds'],120)
  self.assertEqual(v2.RULES['adverse_exit_bps'],5)
 def update(self,sequence,bid=100.12,ask=100.14,qty=10,snapshot=False):
  self.p.receive(dict(header={'type':'orderbook.snapshot' if snapshot else 'orderbook.increment'},market=self.m,sequence_num=sequence,bids=[[str(bid),str(qty)]],asks=[[str(ask),str(qty)]]))
 def test_each_book_update_enforces_target_without_maintenance(self):
  for n in range(20):
   self.lot('lot-'+str(n))
   self.update(n*2+1,snapshot=True)
   self.assertIn('lot-'+str(n),self.p.profit_requests)
   self.now+=.26;self.update(n*2+2)
   self.assertNotIn('lot-'+str(n),self.p.lots)
  self.assertEqual(self.p.stats['closed_lots'],21)
  self.assertGreaterEqual(self.p.stats['realized_pnl'],2.1)
 def test_partial_exit_retries_on_replenished_depth(self):
  self.update(1,qty=.4,snapshot=True);self.now+=.26;self.update(2,qty=.4)
  self.assertAlmostEqual(self.p.lots['lot']['qty'],.6)
  self.assertFalse(self.p.profit_requests)
  self.update(3,qty=.6);self.assertIn('lot',self.p.profit_requests)
  self.now+=.26;self.update(4,qty=.6)
  self.assertNotIn('lot',self.p.lots);self.assertEqual(self.p.stats['closed_lots'],1)
  self.assertGreaterEqual(self.p.stats['realized_pnl'],.1)
 def test_duplicate_book_does_not_replenish_consumed_depth(self):
  self.update(1,qty=.4,snapshot=True);self.now+=.26;self.update(2,qty=.4)
  self.update(2,qty=100)
  self.assertFalse(self.p.profit_requests);self.assertAlmostEqual(self.p.lots['lot']['qty'],.6)
 def test_sequence_gap_cannot_trigger_capture(self):
  self.update(1,bid=100.,snapshot=True)
  with self.assertRaises(RuntimeError):self.update(3)
  self.assertFalse(self.p.profit_requests)
if __name__=='__main__':unittest.main()
