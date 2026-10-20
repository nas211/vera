import json
import unittest
from pathlib import Path
from decimal import Decimal as D
from dataclasses import replace
from .test_tenant_fanout import Env, ctx
from execution.metadata import MarketFilters
from execution.types import OrderState as O

class FixedEntryTests(unittest.TestCase):
    def setUp(self):
        self.e=Env();self.addCleanup(self.e.cleanup)
        for a in ('acctA','acctB'):self.e.accounts.set(ctx(a,100))
        self.e.ready()
        self.a=self.e.reg.tenants['acctA'];self.b=self.e.reg.tenants['acctB']
        self.e.meta._f['ONEUSDT-PERP']=MarketFilters('ONEUSDT-PERP',D('.01'),D('.01'),D('.01'),D(5),D(20),'TRADING')
        self.e.feed('ONEUSDT-PERP','3')
    def setting(self,**kw):
        Path(self.a.dir,'fixed_entry_notional.json').write_text(json.dumps(dict(account_id='acctA',notional_usd='50',**kw)))
    def send(self):return self.e.reg.dispatch(self.e.sig('fixed-test',market='ONEUSDT-PERP',price='3'))
    def test_fifty_rounds_down_and_only_changes_selected_account(self):
        self.setting();out=self.send();self.assertTrue(out['acctA'].startswith('ACKNOWLEDGED'),out)
        a=self.a.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]
        b=self.b.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]
        self.assertEqual(D(a['qty'])*3,D('49.98'))
        self.assertLess(D(b['qty'])*3,D(6))
        self.assertEqual(json.loads(a['meta'])['detail']['fixed_entry_notional_usd'],'50')
        self.assertEqual(self.a.runtime_info()['fixed_entry_notional_usd'],'50')
    def test_minimum_above_fifty_rejected_without_upsizing(self):
        self.setting()
        out=self.e.reg.dispatch(self.e.sig('btc-too-large'))
        self.assertEqual(out['acctA'],'REJECTED:fixed_entry_below_coin_minimum')
        self.assertEqual(self.e.mocks['acctA'].place_calls,0)
    def test_insufficient_balance_rejected(self):
        self.setting();self.e.mocks['acctA'].set_account(self.a.ctx.namespace,'1','1')
        self.assertEqual(self.send()['acctA'],'REJECTED:sizing:insufficient_available_balance')
    def test_account_mismatch_blocks(self):
        Path(self.a.dir,'fixed_entry_notional.json').write_text(json.dumps(dict(account_id='acctB',notional_usd='50')))
        self.assertEqual(self.send()['acctA'],'BLOCKED:invalid_fixed_entry_notional')
    def test_corrupt_configuration_blocks(self):
        Path(self.a.dir,'fixed_entry_notional.json').write_text('{bad')
        self.assertEqual(self.send()['acctA'],'BLOCKED:invalid_fixed_entry_notional')
    def test_refresh_preserves_existing_position(self):
        self.send();self.e.fill_entries(price='3');before=self.a.open_positions()
        self.setting();self.e.reg.refresh();self.assertEqual(self.a.open_positions(),before)
    def test_explicit_percentage_takes_precedence(self):
        self.setting()
        self.e.mocks['acctA'].set_account(self.a.ctx.namespace,'1000','1000')
        self.e.accounts.set(replace(self.e.accounts.ctx['acctA'],trade_margin_pct=1));self.e.reg.refresh()
        self.assertIsNone(self.a.runtime_info()['fixed_entry_notional_usd'])
        self.assertTrue(self.send()['acctA'].startswith('ACKNOWLEDGED'))
        a=self.a.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]
        self.assertNotIn('fixed_entry_notional_usd',json.loads(a['meta'])['detail'])

    def test_percentage_size_cannot_exceed_dollar_stop_fee_budget(self):
        self.setting()
        self.e.accounts.set(replace(self.e.accounts.ctx['acctA'],trade_margin_pct=1));self.e.reg.refresh()
        self.assertEqual(self.send()['acctA'],'REJECTED:sizing:fees_exceed_dollar_loss_budget')
        self.assertEqual(self.e.mocks['acctA'].place_calls,0)
