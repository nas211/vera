import unittest
from decimal import Decimal as D
from dataclasses import replace
from .test_tenant_fanout import Env, ctx
from execution.sizing import margin_percentage_notional, SizingError
from execution.metadata import MarketFilters
from execution.types import OrderState as O

class TradeSizeMathTests(unittest.TestCase):
    def test_percentage_fee_buffer_and_linear_scale(self):
        full = margin_percentage_notional(D(55),100,10,D('.0004'),D(5))
        self.assertLess(full,D(550))
        self.assertAlmostEqual((full / 10 * D("1.06") + full * D('.0008')) * D('1.01'),D(55))
        self.assertAlmostEqual(margin_percentage_notional(D(55),25,10,D('.00015'),D(5)),full/4)
        self.assertLess(margin_percentage_notional(D(55),100,10,D('.001'),D(5)), full)

    def test_invalid_and_insufficient_budget(self):
        for pct in [0,101,True,1.5,'100']:
            with self.assertRaises(SizingError):margin_percentage_notional(D(55),pct,10,D('.0004'),D(5))
        for balance in [D(0),D(-1),D('NaN'),D('Infinity'),D('.001')]:
            with self.assertRaises(SizingError):margin_percentage_notional(balance,100,10,D('.0004'),D(5))

class TradeSizeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.e=Env();self.addCleanup(self.e.cleanup)
        self.e.accounts.set(replace(ctx('acctA',20),trade_margin_pct=100,max_open_positions=8))
        self.e.accounts.set(ctx('acctB',20));self.e.ready()
        for market in ['ONEUSDT-PERP','TWOUSDT-PERP']:
            self.e.meta._f[market]=MarketFilters(market,D('.01'),D(1),D(1),D(5),D(20),'TRADING')
            self.e.feed(market,'1')
        for aid in ['acctA','acctB']:self.e.mocks[aid].set_account(self.e.accounts.ctx[aid].namespace,'55','55')

    def test_full_size_and_pending_margin_reservation(self):
        out=self.e.reg.dispatch(self.e.sig('one',market='ONEUSDT-PERP',price='1'))
        self.assertTrue(out['acctA'].startswith('ACKNOWLEDGED'),out)
        a=self.e.reg.tenants['acctA'];b=self.e.reg.tenants['acctB']
        qty=D(a.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]['qty'])
        self.assertGreater(qty,D(500));self.assertLess(qty,D(550))
        self.assertLessEqual((qty/10*D("1.06")+qty*D('.0008'))*D('1.01'),D(55))
        self.assertLess(D(b.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]['qty']),D(10))
        # Mock venue deliberately still reports the original available balance.
        out=self.e.reg.dispatch(self.e.sig('two',market='TWOUSDT-PERP',price='1'))
        self.assertEqual(out['acctA'],'REJECTED:sizing:trade_budget_below_coin_minimum')

    def test_refresh_percentage_and_preserve_existing_position(self):
        self.e.accounts.set(replace(self.e.accounts.ctx['acctA'],trade_margin_pct=25))
        self.e.reg.refresh()
        out=self.e.reg.dispatch(self.e.sig('quarter',market='ONEUSDT-PERP',price='1'))
        self.assertTrue(out['acctA'].startswith('ACKNOWLEDGED'),out)
        a=self.e.reg.tenants['acctA']
        qty=D(a.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]['qty'])
        self.assertGreater(qty,D(125));self.assertLess(qty,D(130))
        self.e.fill_entries(price='1');before=a.open_positions()
        self.e.accounts.set(replace(self.e.accounts.ctx['acctA'],trade_margin_pct=None))
        self.e.reg.refresh();self.assertEqual(a.open_positions(),before)
        self.assertIsNone(a.eng.cfg.trade_margin_pct)
