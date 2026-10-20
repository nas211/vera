import json
import os
import unittest
from decimal import Decimal as D
from dataclasses import replace
from .test_trade_size import TradeSizeExecutionTests
from execution.sizing import margin_percentage_notional, SizingError
from execution.types import OrderState as O
from customer.tenants import confirmed_taker_fee

class VipFeeTests(TradeSizeExecutionTests):
    def profile(self, account='acctA', **updates):
        t=self.e.reg.tenants[account]
        data=dict(account_id=account,confirmed=True,taker_fee_rate='0.00015')
        data.update(updates)
        with open(os.path.join(t.dir,'fee_profile.json'),'w') as f:json.dump(data,f)
        self.e.reg.refresh()
        return t

    def test_vip_sizing_is_scoped_and_keeps_budget_and_one_slot(self):
        self.e.accounts.set(replace(self.e.accounts.ctx['acctA'],max_open_positions=1))
        a=self.profile()
        self.assertEqual(a.eng.cfg.confirmed_taker_fee,D('.00015'))
        self.assertIsNone(self.e.reg.tenants['acctB'].eng.cfg.confirmed_taker_fee)
        out=self.e.reg.dispatch(self.e.sig('vip',market='ONEUSDT-PERP',price='1'))
        self.assertTrue(out['acctA'].startswith('ACKNOWLEDGED'),out)
        qty=D(a.ledger.orders_where([O.ACKNOWLEDGED],purpose='ENTRY')[0]['qty'])
        self.assertEqual(qty,D(512))
        self.assertLessEqual((qty/10*D("1.06")+qty*D('.0003'))*D('1.01'),D(55))
        self.e.fill_entries(price='1')
        self.e.mocks['acctA'].set_account(self.e.accounts.ctx['acctA'].namespace,'55','55')
        out=self.e.reg.dispatch(self.e.sig('second',market='TWOUSDT-PERP',price='1'))
        self.assertEqual(out['acctA'],'REJECTED:sizing:max_open_positions')

    def test_profile_reload_and_invalid_profiles_use_public_fees(self):
        a=self.profile()
        for data in [dict(account_id='acctB'),dict(confirmed=False),dict(taker_fee_rate='NaN'),dict(taker_fee_rate='-1'),dict(taker_fee_rate='Infinity'),dict(taker_fee_rate='0.5')]:
            self.profile(**data)
            self.assertIsNone(a.eng.cfg.confirmed_taker_fee)
        self.profile()
        os.unlink(os.path.join(a.dir,'fee_profile.json'))
        self.e.reg.refresh()
        self.assertIsNone(a.eng.cfg.confirmed_taker_fee)
        with open(os.path.join(a.dir,'fee_profile.json'),'w') as f:f.write('{broken')
        self.assertIsNone(confirmed_taker_fee(a.dir,'acctA'))

    def test_vip_pending_entries_cannot_reuse_balance(self):
        self.profile()
        out=self.e.reg.dispatch(self.e.sig('pending-one',market='ONEUSDT-PERP',price='1'))
        self.assertTrue(out['acctA'].startswith('ACKNOWLEDGED'),out)
        out=self.e.reg.dispatch(self.e.sig('pending-two',market='TWOUSDT-PERP',price='1'))
        self.assertFalse(out['acctA'].startswith('ACKNOWLEDGED'),out)

class VipMathTests(unittest.TestCase):
    def test_verified_fee_and_default_floor(self):
        args=(D(55),100,10,D('.0004'),D(5))
        vip=margin_percentage_notional(*args,confirmed_taker_fee=D('.00015'))
        public=margin_percentage_notional(*args)
        self.assertGreater(vip,public)
        self.assertAlmostEqual((vip/10*D("1.06")+vip*D('.0003'))*D('1.01'),D(55))
        for fee in [D('NaN'),D('Infinity'),D('-0.1'),D('1')]:
            with self.assertRaises(SizingError):margin_percentage_notional(*args,confirmed_taker_fee=fee)
