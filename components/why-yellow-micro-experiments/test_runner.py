import unittest
from pathlib import Path
from runner import Paper,MARKETS,CONFIG

class PaperTests(unittest.TestCase):
    def setUp(self):
        self.now=1800000000.
        self.p=Paper({m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5) for m in MARKETS},clock=lambda:self.now,record=False)
        self.p.connected=True
        for m in MARKETS:self.p.books[m]=dict(bids={100.:2.},asks={100.1:2.},received=self.now)
    def tick(self,seconds=.3):self.now+=seconds;self.p.maintain()
    def trade(self,price,qty,side='sell'):
        import datetime
        return dict(price=str(price),amount=str(qty),direction=side,executed_at=datetime.datetime.fromtimestamp(self.now,datetime.timezone.utc).isoformat())
    def test_200_quotes_and_shared_budget(self):
        self.p.maintain();self.assertEqual(len(self.p.orders),200)
        self.assertGreater(self.p.exposure(),9950)
        self.assertLessEqual(self.p.exposure(),10000)
        self.assertLessEqual(self.p.exposure()/10,1000)
    def test_all_ten_markets_including_bitcoin_minimum_lot(self):
        self.assertEqual(len(MARKETS),10)
        self.p.filters['BTCUSDT-PERP']=dict(tick=.1,step=.001,min_qty=.001,min_notional=1)
        self.p.books['BTCUSDT-PERP']=dict(bids={70000.:1},asks={70001.:1},received=self.now)
        self.p.maintain()
        for m in MARKETS:
            orders=[o for o in self.p.orders.values() if o['market']==m]
            self.assertTrue(orders,m)
            self.assertLessEqual(self.p.exposure(m),1000)
        btc=[o for o in self.p.orders.values() if o['market']=='BTCUSDT-PERP']
        self.assertTrue(all(o['qty']>=.001 for o in btc))
        self.assertLessEqual(self.p.exposure(),10000)

    def test_inventory_and_pending_quotes_share_full_budget(self):
        self.p.lots['existing']=dict(market=MARKETS[0],buy=True,entry=100,qty=4,opened=self.now,exit_id=None)
        self.p.maintain()
        self.assertLessEqual(self.p.exposure(),10000)
        self.assertLessEqual(self.p.exposure(MARKETS[0]),1000)
        self.assertGreater(self.p.exposure(),9900)
        self.assertTrue(self.p.lots['existing']['exit_id'])
    def test_equity_drop_cancels_pending_capacity_without_permanent_halt(self):
        self.p.maintain()
        self.p.stats['realized_pnl']=-100
        self.tick()
        self.assertTrue(any(o['cancel_at'] for o in self.p.orders.values()))
        self.assertFalse(self.p.halted)
        self.tick()
        self.assertLessEqual(self.p.exposure(),9000)

    def test_exact_stop_at_1000_even_with_multiple_possible_exits(self):
        m=MARKETS[0]
        for i in range(2):
            lid='existing-'+str(i)
            self.p.lots[lid]=dict(market=m,buy=True,entry=100,qty=1,opened=self.now,exit_id=None)
        self.p.stats['closed_lots']=999
        self.tick();self.tick()
        self.p.trade(m,self.trade(101,100,'buy'))
        self.assertEqual(self.p.stats['closed_lots'],1000)
        self.assertIsNotNone(self.p.finished_at)
        self.assertFalse(self.p.orders)
        self.assertEqual(len(self.p.lots),1)
        before=self.p.stats.copy()
        self.p.trade(m,self.trade(101,100,'buy'));self.p.maintain()
        self.assertEqual(self.p.stats,before)
        self.assertFalse(self.p.orders)
    def test_partial_exit_is_not_a_completed_trade(self):
        m=MARKETS[0]
        self.p.lots['partial']=dict(market=m,buy=True,entry=100,qty=1,opened=self.now,exit_id=None)
        self.p.stats['closed_lots']=999
        self.tick();self.tick()
        self.p.trade(m,self.trade(101,.5,'buy'))
        self.assertEqual(self.p.stats['closed_lots'],999)
        self.assertIsNone(self.p.finished_at)
    def test_completion_is_persisted_and_cannot_resume_on_restart(self):
        import tempfile,json
        from unittest.mock import patch
        import runner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'state').mkdir()
            with patch.object(runner,'ROOT',root),patch.object(runner,'PUBLIC',root/'public.json'):
                self.p.record=True;self.p.stats['closed_lots']=1000
                self.assertTrue(self.p.finish_if_limit())
                self.assertTrue((root/'state/final-results.json').exists())
                restored=Paper({},clock=lambda:self.now,record=False);restored.restore()
                self.assertTrue(restored.finish_if_limit())
                self.assertEqual(restored.finished_at,self.p.finished_at)

    def test_market_cross_rejected_even_after_latency(self):
        self.assertIsNone(self.p.place(MARKETS[0],True,100.1,1))
        oid=self.p.place(MARKETS[0],True,100,1)
        self.p.books[MARKETS[0]]['asks']={99.9:10}
        self.tick();self.assertNotIn(oid,self.p.orders)
    def test_queue_and_partial_fill_and_no_volume_reuse(self):
        m=MARKETS[0];oid=self.p.place(m,True,100,1);self.tick()
        self.p.trade(m,self.trade(100,1));self.assertEqual(self.p.stats['entry_fills'],0)
        self.p.trade(m,self.trade(100,1.4));self.assertAlmostEqual(self.p.orders[oid]['qty'],.6)
        self.assertAlmostEqual(sum(l['qty'] for l in self.p.lots.values()),.4)
    def test_old_trades_and_snapshots_never_fill(self):
        m=MARKETS[0];self.p.place(m,True,100,1);t=self.trade(99,50);self.tick();self.p.trade(m,t)
        self.assertFalse(self.p.lots)
        self.p.receive(dict(header=dict(type='trades.snapshot'),market=m,sequence_num=4,trades=[self.trade(99,50)]))
        self.assertFalse(self.p.lots)
    def test_maker_round_trip_and_pnl(self):
        m=MARKETS[0];self.p.place(m,True,100,1);self.tick()
        self.p.trade(m,self.trade(99.99,.5));self.tick()
        exits=[o for o in self.p.orders.values() if o['lot']];self.assertEqual(len(exits),1)
        self.tick();self.p.trade(m,self.trade(100.2,.5,'buy'))
        self.assertEqual(self.p.stats['closed_lots'],1)
        self.assertAlmostEqual(self.p.stats['realized_pnl'],.05)
        self.assertEqual(self.p.stats['fees'],0)
    def test_disconnect_preserves_inventory_and_resets_orders(self):
        m=MARKETS[0];self.p.place(m,True,100,1);self.tick();self.p.trade(m,self.trade(99.99,.5))
        self.p.disconnect();self.assertEqual(len(self.p.lots),1);self.assertFalse(self.p.orders)
    def test_sequence_gap_fails_closed(self):
        m=MARKETS[0];self.p.seq[(m,'trades')]=1
        with self.assertRaises(RuntimeError):self.p.receive(dict(header=dict(type='trades.increment'),market=m,sequence_num=3,trades=[]))
    def test_total_trade_volume_is_shared(self):
        m=MARKETS[0];self.p.place(m,True,100,1);self.p.place(m,True,99.98,1);self.tick()
        self.p.trade(m,self.trade(99.97,1.1))
        self.assertAlmostEqual(sum(l['qty'] for l in self.p.lots.values()),1.1)

if __name__=='__main__':unittest.main()
