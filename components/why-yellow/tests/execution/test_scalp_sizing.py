import copy
import unittest
from decimal import Decimal as D
from tests.execution.test_scalp_live import FakeClock,F,M,book,lot
from tests.execution import test_scalp_live as fixtures
from customer.scalp.strategy import LiveStrategy,CONFIG,RULES,MODULES
from customer.scalp.sizing import minimum_quantity,budget_for_equity
from execution.sizing import SizingError

class MinimumClips(unittest.TestCase):
    def model(self,equity):
        clock=FakeClock();model=LiveStrategy(F,clock);model.set_equity(equity);book(model,clock)
        return model

    def test_step_and_notional_minimum_round_up_only_as_required(self):
        cases=[('85',dict(step='.01',min_qty='.01',min_notional='5'),'0.06'),
               ('83000',dict(step='.001',min_qty='.001',min_notional='1'),'0.001'),
               ('2500',dict(step='.001',min_qty='.001',min_notional='1'),'0.001'),
               ('0.085',dict(step='1',min_qty='1',min_notional='5'),'59'),
               ('2.3',dict(step='.1',min_qty='.1',min_notional='5'),'2.2')]
        for price,f,want in cases:
            with self.subTest(price=price):
                q=minimum_quantity(price,f)
                self.assertEqual(q,D(want))
                self.assertGreaterEqual(q*D(price),D(f['min_notional']))
                smaller=q-D(f['step'])
                self.assertTrue(smaller<D(f['min_qty']) or smaller*D(price)<D(f['min_notional']))

    def test_entry_size_is_coin_minimum_at_small_and_large_balances(self):
        for equity in (50,1000,10000):
            model=self.model(equity);model.maintain()
            self.assertTrue(model.intents)
            for p in model.intents:
                self.assertAlmostEqual(p['qty'],float(minimum_quantity(p['price'],F[M])))

    def test_no_orders_without_valid_account_equity(self):
        for equity in (0,-1,None,'NaN','Infinity','bad'):
            model=self.model(equity);model.maintain();self.assertEqual(model.intents,[])

    def test_account_budgets_are_isolated_and_original_settings_unchanged(self):
        before=copy.deepcopy((CONFIG,RULES))
        a,b=self.model(50),self.model(1000)
        b.set_equity(2000)
        self.assertEqual(a.budget['equity_usd'],D(50))
        self.assertEqual((CONFIG,RULES),before)

    def test_existing_inventory_and_exit_quantity_are_not_resized(self):
        model=self.model(50);model.lots['old']=lot();before=copy.deepcopy(model.lots)
        model.set_equity(100)
        oid=model.place(M,False,100.2,1,'old')
        self.assertEqual(model.orders[oid]['qty'],1)
        self.assertEqual(model.lots,before)

    def test_market_budget_can_accommodate_a_higher_coin_minimum(self):
        budget=budget_for_equity(50)
        self.assertGreaterEqual(budget['per_market_notional_cap_usd'],D(83))
        self.assertEqual(budget['gross_notional_cap_usd'],D(500))

    def test_all_eight_markets_can_propose_before_funds_are_allocated(self):
        model=self.model(5)
        model.entry_reason=lambda *args:None
        for market in MODULES['v2'].ACTIVE_MARKETS:
            model.filters[market]=dict(F[M])
            model.books[market]=copy.deepcopy(model.books[M])
        model.maintain()
        self.assertEqual({p['market'] for p in model.intents},set(MODULES['v2'].ACTIVE_MARKETS))

class FairMarketAllocation(unittest.TestCase):
    setUp=fixtures.LiveBoundary.setUp

    def batch(self,counts=2):
        return [dict(id=f'{m}-{i}',market=m,lot=None) for m in MODULES['v2'].ACTIVE_MARKETS for i in range(counts)]

    def test_one_clip_per_eligible_market_before_second_clip(self):
        sent=[];self.r.submit=lambda p:sent.append(p['market'])
        self.r._submit_intents(self.batch())
        markets=MODULES['v2'].ACTIVE_MARKETS
        self.assertEqual(sent,markets+markets)

    def test_expensive_market_keeps_turn_until_funds_are_released(self):
        sent=[];funds=[D(9)]
        prices={'BTCUSDT-PERP':D('8.3'),'LINKUSDT-PERP':D(2)}
        def submit(p):
            need=prices.get(p['market'],D('.5'))
            if need>funds[0]:raise SizingError('insufficient_available_balance')
            funds[0]-=need;sent.append(p['market'])
        self.r.submit=submit
        self.r.model.entry_reason=lambda *args:None
        self.r._submit_intents(self.batch())
        self.assertEqual(self.r.model.waiting_entry_market,'BTCUSDT-PERP')
        previous=list(sent)
        self.r._submit_intents([dict(id='cheap',market='ETHUSDT-PERP',lot=None)])
        self.assertEqual(sent,previous)
        funds[0]=D(10)
        self.r._submit_intents([dict(id='btc',market='BTCUSDT-PERP',lot=None),dict(id='eth',market='ETHUSDT-PERP',lot=None)])
        self.assertEqual(sent[-2:],['BTCUSDT-PERP','ETHUSDT-PERP'])
        self.assertIsNone(self.r.model.waiting_entry_market)

    def test_waiting_market_does_not_block_exits(self):
        self.r.model.waiting_entry_market='BTCUSDT-PERP'
        self.r.model.entry_reason=lambda *args:None
        sent=[];self.r.submit=lambda p:sent.append(p)
        exit_order=dict(id='exit',market='ETHUSDT-PERP',lot='owned')
        self.r._submit_intents([dict(id='entry',market='HYPEUSDT-PERP',lot=None),exit_order])
        self.assertEqual(sent,[exit_order])

    def test_waiting_market_is_released_when_its_signal_no_longer_allows_entry(self):
        self.r.model.waiting_entry_market='BTCUSDT-PERP'
        self.r.model.entry_reason=lambda *args:'adverse_trend'
        sent=[];self.r.submit=lambda p:sent.append(p['market'])
        self.r._submit_intents([dict(id='entry',market='ETHUSDT-PERP',lot=None)])
        self.assertEqual(sent,['ETHUSDT-PERP'])

    def test_market_turn_survives_restart_without_resizing_positions(self):
        self.r.model.entry_cursor=5;self.r.model.waiting_entry_market='BTCUSDT-PERP'
        state=self.r.model.checkpoint()
        model=LiveStrategy(F,self.clock);model.restore_checkpoint(state)
        self.assertEqual((model.entry_cursor,model.waiting_entry_market),(5,'BTCUSDT-PERP'))

    def test_stale_waiting_market_does_not_freeze_other_markets(self):
        self.r.model.waiting_entry_market='BTCUSDT-PERP'
        self.r.model.books[M]['received']=self.clock()-16
        self.r.model.entry_reason=lambda *args:None
        sent=[];self.r.submit=lambda p:sent.append(p['market'])
        self.r._submit_intents([dict(id='entry',market='ETHUSDT-PERP',lot=None)])
        self.assertEqual(sent,['ETHUSDT-PERP'])
