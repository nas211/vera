import unittest
from unittest.mock import patch
import runner,v2,overnight

class OvernightTests(unittest.TestCase):
    def setUp(self):
        for ctx in [patch.dict(runner.CONFIG,{}),patch.dict(v2.RULES,{}),
                    patch.object(runner,'MARKETS',list(runner.MARKETS)),
                    patch.object(v2,'ACTIVE_MARKETS',list(v2.ACTIVE_MARKETS)),
                    patch.object(v2,'EXCLUDED_ENTRIES',set(v2.EXCLUDED_ENTRIES))]:
            ctx.start();self.addCleanup(ctx.stop)
        overnight.configure();self.now=1800000000.
        self.p=v2.PaperV2({m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5) for m in runner.MARKETS},clock=lambda:self.now,record=False)
        self.p.connected=True
        for m in runner.MARKETS:
            self.p.books[m]=dict(bids={100.:10},asks={100.02:10},received=self.now)
            self.p.history[m].append((self.now-31,100.01))
    def test_all_ten_coins_one_hundred_ten_dollar_slots(self):
        self.p.maintain();entries=[o for o in self.p.orders.values() if not o['lot']]
        self.assertEqual(len(entries),100)
        self.assertEqual(set(o['market'] for o in entries),set(overnight.MARKETS))
        self.assertLessEqual(self.p.exposure(),10000)
        for o in entries:
            self.assertGreater(o['qty']*o['price'],98)
            self.assertLessEqual(o['qty']*o['price'],100)
    def test_no_count_stop_overnight(self):
        self.p.stats['closed_lots']=1500
        self.assertFalse(self.p.finish_if_limit())
        self.assertIsNone(self.p.finished_at)
    def test_tp_is_point_one_percent_and_does_not_decay_after_thirty_seconds(self):
        lot=dict(market=runner.MARKETS[0],buy=True,entry=100,qty=1,opened=self.now-45)
        price,reason=self.p.exit_price(lot)
        self.assertAlmostEqual(price,100.1)
        self.assertEqual(reason,'target')
        self.assertAlmostEqual((price-100)*1,.1)
    def test_existing_loss_and_time_exits_stay_enabled(self):
        lot=dict(market=runner.MARKETS[0],buy=True,entry=100,qty=1,opened=self.now-91)
        price,reason=self.p.exit_price(lot)
        self.assertEqual(reason,'passive_loss_or_time_exit')
        self.assertEqual(price,100.02)

if __name__=='__main__':unittest.main()
