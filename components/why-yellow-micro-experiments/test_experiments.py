import datetime,json,tempfile,unittest
from pathlib import Path
import runner,v2
from experiments import Experiment,VARIANTS

class ExperimentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1800000000.;self.market=runner.MARKETS[0]
        self.filters={m:dict(tick=.01,step=.01,min_qty=.01,min_notional=5) for m in runner.MARKETS}
    def account(self,name='baseline',target=10,hold=90):
        p=Experiment(name,target,hold,self.filters,clock=lambda:self.now,root=Path(self.tmp.name))
        p.connected=True;p.books[self.market]=dict(bids={100.:0},asks={100.02:0},received=self.now)
        p.last_mid[self.market]=100.01
        return p
    def test_variants_are_isolated_and_timeout_differs(self):
        p=self.account();q=self.account('hold180',10,180)
        lot=dict(market=self.market,buy=True,entry=100.,qty=1.,opened=self.now-100)
        with p.settings():self.assertEqual(p.exit_price(lot)[1],'timeout')
        with q.settings():self.assertEqual(q.exit_price(lot)[1],'target')
        self.assertEqual(v2.RULES['max_hold_seconds'],90)
        self.assertIsNot(p.lots,q.lots)
    def test_targets(self):
        lot=dict(market=self.market,buy=True,entry=100.,qty=1.,opened=self.now)
        for name,target,hold in VARIANTS:
            p=self.account(name,target,hold)
            with p.settings():self.assertAlmostEqual(p.exit_price(lot)[0],100+target/100)
    def test_partial_fills_accounting_and_restart(self):
        p=self.account();p.halted=True
        p.lots['lot-test']=dict(market=self.market,buy=True,entry=100.,qty=1.,opened=self.now-100,entry_order='entry-test',exit_id=None)
        with p.settings():
            p.maintain();self.now+=.3;p.maintain()
            for qty in (.4,.6):
                p.trade(self.market,dict(price='100.03',amount=str(qty),direction='buy',executed_at=datetime.datetime.fromtimestamp(self.now,datetime.timezone.utc).isoformat()))
            p.save()
        self.assertAlmostEqual(p.stats['realized_pnl'],.02)
        self.assertEqual(p.stats['closed_lots'],1)
        self.assertAlmostEqual(p.metrics['by_exit_reason']['timeout']['realized_pnl'],.02)
        self.assertEqual(p.metrics['by_exit_reason']['timeout']['closed_lots'],1)
        rows=[json.loads(line) for line in (p.directory/'events.jsonl').read_text().splitlines()]
        fills=[r for r in rows if r['event']=='MAKER_EXIT_FILL']
        self.assertEqual([r['closed'] for r in fills],[False,True])
        self.assertEqual(fills[0]['entry_order'],'entry-test')
        restored=self.account();restored.restore()
        self.assertEqual(restored.stats,p.stats);self.assertEqual(restored.metrics,p.metrics)
    def test_drawdown_and_confirmation(self):
        p=self.account();p.stats['realized_pnl']=10;p.observe()
        p.stats['realized_pnl']=-5;p.observe()
        self.assertEqual(p.metrics['max_drawdown_usd'],15)
        self.now+=12*3600;p.observe()
        p.stats['realized_pnl']=-2
        with p.settings():self.assertEqual(p.snapshot()['confirmation_pnl'],3)
    def test_loss_priority_is_recorded_without_changing_quote(self):
        p=self.account();lot=dict(market=self.market,buy=True,entry=101.,qty=1.,opened=self.now)
        with p.settings():self.assertEqual(p.exit_price(lot),(100.02,'loss'))

if __name__=='__main__':unittest.main()
