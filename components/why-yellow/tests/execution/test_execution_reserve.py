import json
import unittest
from dataclasses import replace
from decimal import Decimal as D

from .harness import ACCT, Env
from execution.metadata import MarketFilters
from execution.trade_plan import build_plan, build_agent_plan, stored_plan, observed_exit_slippage
from execution.types import Capabilities


class ExecutionReserveTests(unittest.TestCase):
    def test_observed_slippage_increases_reserve_and_reduces_size(self):
        class History:
            def orders_where(self, *args, **kw):
                self.query=kw
                return [dict(state_ts=99,meta=json.dumps(dict(trigger_price='100')),
                             avg_price='100.4',side='buy')]
        ledger=History();slip=observed_exit_slippage(ledger,ACCT,'A',100)
        self.assertEqual(slip,D('.005'))
        self.assertEqual(ledger.query,dict(account_id=ACCT,market='A',purpose='STOP'))
        f=MarketFilters('A',D('.001'),D('.001'),D('.001'),D(5),D(20),'TRADING')
        ev=dict(version=1,as_of=100,hourly_atr_fraction=.006,
                median_6h_favorable_fraction=.04,samples=244)
        low=build_plan(account=ACCT,direction='short',price=D(100),filters=f,evidence=ev,now=100)
        high=build_plan(account=ACCT,direction='short',price=D(100),filters=f,
                        evidence=dict(ev,exit_slippage_fraction=str(slip)),now=100)
        self.assertLess(D(high['planned_qty']),D(low['planned_qty']))
        for bad in ('NaN','-.1','.02'):
            with self.assertRaises(ValueError):
                build_plan(account=ACCT,direction='short',price=D(100),filters=f,
                           evidence=dict(ev,exit_slippage_fraction=bad),now=100)

    def test_agent_plan_cannot_bypass_execution_reserve(self):
        f=MarketFilters('A',D('.001'),D('.001'),D('.001'),D(5),D(20),'TRADING')
        for side in ('long','short'):
            p=build_agent_plan(account=ACCT,direction=side,price=D(100),filters=f,
                               ai=dict(sl_bps=D(60),tp_bps=D(180),hold_bars=24),
                               equity=D(10000),now=100,exit_slippage_fraction=D('.002'))
            self.assertLess(D(p['trigger_budget_usd']),D('1.80'))
            self.assertGreaterEqual(D(p['execution_reserve_usd']),D(p['notional_usd'])*D('.002'))

    def test_long_short_stress_fill_fees_and_stricter_limits(self):
        f = MarketFilters('A', D('.001'), D('.001'), D('.001'), D(5), D(20), 'TRADING')
        for side in ('long', 'short'):
            for cap in (D('1'), D('2'), D('200')):
                with self.subTest(side=side, cap=cap):
                    p = build_plan(account=ACCT,direction=side,price=D(100),filters=f,
                                   evidence=dict(version=1,as_of=100,hourly_atr_fraction=.006,
                                                 median_6h_favorable_fraction=.04,samples=244),
                                   now=100,configured=cap)
                    q=D(p['planned_qty']); sign=1 if side=='long' else -1
                    stop=D(p['estimated_stop']); exit_price=stop-sign*D(100)*D('.001')
                    net=(exit_price-100)*q*sign-q*(100+exit_price)*D('.0004')
                    self.assertGreaterEqual(net,-min(cap,D('1.80')))
                    self.assertLess(D(p['trigger_budget_usd']),D(p['risk_usd']))
                    self.assertLess(q*100,500)
                    row=dict(account_id=ACCT,direction=side,meta=json.dumps(dict(detail=dict(risk_trade_plan=p))))
                    self.assertEqual(stored_plan(row),p)
                    for changes in ({'trigger_budget_usd':'2'}, {'execution_reserve_usd':'NaN'},
                                    {'execution_reserve_usd':'.00001'}, {'exit_slippage_fraction':'0'}):
                        row['meta']=json.dumps(dict(detail=dict(risk_trade_plan=dict(p,**changes))))
                        with self.assertRaises(ValueError):stored_plan(row)

    def test_existing_position_partial_exit_reconciliation_keeps_reserved_budget(self):
        for side in ('long','short'):
            with self.subTest(side=side):
                e=Env(capabilities=Capabilities(venue_stop_orders=True,conditional_take_profit=True))
                self.addCleanup(e.ledger.close);e.ready()
                f=MarketFilters('A',D('.001'),D('.001'),D('.001'),D(5),D(20),'TRADING')
                e.meta._f['A']=f; e.feed('A','100')
                p=build_plan(account=ACCT,direction=side,price=D(100),filters=f,
                             evidence=dict(version=1,as_of=e.clock(),hourly_atr_fraction=.006,
                                           median_6h_favorable_fraction=.04,samples=244),now=e.clock())
                sig=replace(e.signal('reserve',market='A',direction=side,strategy='WY-PULLBACK',price='100'),
                            notional_usd=D(p['notional_usd']),detail=dict(risk_trade_plan=p))
                r=e.eng.submit_entry(sig);self.assertEqual(r.status,'ACKNOWLEDGED')
                q=D(e.order(r.client_order_id)['qty'])
                e.eng.on_fill(e.mock.fill(r.client_order_id,q,'100',fee=str(q*100*D('.0004'))))
                prot=e.ledger.get_protection(r.client_order_id)
                half=f.round_qty_down(q/2);tp=D(prot['tp_price'])
                e.eng.on_fill(e.mock.fill(prot['target_coid'],half,tp,fee=str(half*tp*D('.0004'))))
                self.assertTrue(e.restart().ok)
                pos=e.pos(r.client_order_id);new=e.ledger.get_protection(r.client_order_id)
                rem=D(pos['qty']);sl=D(new['stop_price']);sign=1 if side=='long' else -1
                net=(tp-100)*half*sign+(sl-100)*rem*sign-q*100*D('.0004')-half*tp*D('.0004')-rem*sl*D('.0004')
                self.assertGreaterEqual(net,-D(p['trigger_budget_usd']))
                self.assertGreaterEqual((sl-D(prot['stop_price']))*sign,0)

    def test_unaffordable_minimum_lot_is_skipped(self):
        f=MarketFilters('A',D('.1'),D(1),D(10),D(1000),D(20),'TRADING')
        with self.assertRaises(ValueError):
            build_plan(account=ACCT,direction='long',price=D(100),filters=f,
                       evidence=dict(version=1,as_of=100,hourly_atr_fraction=.006,
                                     median_6h_favorable_fraction=.04,samples=244),now=100)

