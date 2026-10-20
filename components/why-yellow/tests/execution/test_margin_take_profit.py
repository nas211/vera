import json
import unittest
from decimal import Decimal as D
from pathlib import Path
from .harness import Env, ACCT
from .test_tenant_fanout import Env as TenantEnv, ctx
from execution.types import Capabilities, OrderState as O, PositionState as P
from execution import geometry
from customer.tenants import margin_take_profit


class MarginTargetTests(unittest.TestCase):
    def setUp(self):
        self.e = Env(capabilities=Capabilities(venue_stop_orders=True, conditional_take_profit=True))
        self.addCleanup(self.e.ledger.close)
        self.e.config.take_profit_margin_pct = D('3')
        self.e.config.take_profit_taker_fee = D('.0004')
        self.e.ready()

    def opened(self, side='long', sid='one', leverage=10, strategy='WY-PULLBACK'):
        self.e.config.leverage = leverage
        r = self.e.eng.submit_entry(self.e.signal(sid, direction=side, strategy=strategy))
        self.assertEqual(r.status,'ACKNOWLEDGED',r)
        o = self.e.order(r.client_order_id)
        fee = D(o['qty'])*D('77000')*D('.0004')
        self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,o['qty'],'77000',fee=str(fee)))
        return r.client_order_id

    def roi(self, pid):
        pos=self.e.pos(pid);entry=self.e.order(pos['entry_coid']);p=self.e.ledger.get_protection(pid)
        qty=D(pos['qty']);px=D(p['tp_price']);n=qty*D(pos['entry_price'])
        gross=(px-D(pos['entry_price']))*qty*(1 if pos['direction']=='long' else -1)
        fees=D(entry['fees'])*qty/D(entry['filled_qty']) + qty*px*D('.0004')
        return (gross-fees)/(n/D(entry['leverage']))*100

    def test_long_short_target_is_three_percent_net_margin_not_coin_move(self):
        for side in ('long','short'):
            pid=self.opened(side,side)
            roi=self.roi(pid)
            self.assertGreaterEqual(roi,D(3));self.assertLess(roi,D('3.02'))
            p=self.e.ledger.get_protection(pid)
            self.assertLess(abs(D(p['tp_price'])/D(77000)-1),D('.004'))
            entry=self.e.order(pid);qty=D(entry['qty']);stop=D(p['stop_price'])
            net=(stop-D(77000))*qty*(1 if side=='long' else -1)-D(entry['fees'])-stop*qty*D('.0004')
            self.assertGreaterEqual(net,D(-2))
            o=self.e.order(p['target_coid'])
            self.assertEqual(o['order_type'],'trigger_market');self.assertEqual(o['reduce_only'],1)
            self.assertEqual(json.loads(o['meta'])['trigger_type'],'take_profit')

    def test_uses_entry_leverage_when_account_setting_changes(self):
        pid=self.opened(leverage=5)
        self.e.config.leverage=10
        self.e.eng.check_protection()
        self.assertGreaterEqual(self.roi(pid),D(3));self.assertLess(self.roi(pid),D('3.02'))

    def test_individual_target_executes_with_other_trade_open_and_bot_offline(self):
        first=self.opened('long','first');second=self.opened('short','second')
        p=self.e.ledger.get_protection(first);o=self.e.order(p['target_coid'])
        self.e.eng.on_disconnect('offline')
        self.e.mock.fill(o['client_order_id'],o['qty'],p['tp_price'])
        self.assertEqual(self.e.mock.position_qty(ACCT,'BTCUSDT-PERP','long'),0)
        self.assertGreater(self.e.mock.position_qty(ACCT,'BTCUSDT-PERP','short'),0)
        self.assertTrue(self.e.eng.reconcile().ok)
        self.assertEqual(self.e.pos(first)['state'],P.CLOSED.value)
        self.assertNotEqual(self.e.pos(second)['state'],P.CLOSED.value)

    def test_existing_position_reprices_target_preserves_stop(self):
        self.e.config.take_profit_margin_pct=None
        pid=self.opened();old=self.e.ledger.get_protection(pid)
        self.e.config.take_profit_margin_pct=D(3)
        self.e.eng.check_protection();new=self.e.ledger.get_protection(pid)
        self.assertNotEqual(new['target_coid'],old['target_coid'])
        self.assertEqual(new['stop_coid'],old['stop_coid'])
        self.assertEqual(self.e.order(old['target_coid'])['state'],O.CANCELLED.value)
        self.assertGreaterEqual(self.roi(pid),D(3))
        calls=self.e.mock.place_calls;self.e.eng.check_protection()
        self.assertEqual(self.e.mock.place_calls,calls)

    def test_restart_preserves_target_without_duplicate_order(self):
        pid=self.opened();target=self.e.ledger.get_protection(pid)['target_coid'];calls=self.e.mock.place_calls
        self.e.restart()
        self.assertEqual(self.e.ledger.get_protection(pid)['target_coid'],target)
        self.assertEqual(self.e.mock.place_calls,calls)

    def test_target_ack_lost_is_not_submitted_twice(self):
        self.e.mock.script_place('accept','accept','accept_lost')
        pid=self.opened();calls=self.e.mock.place_calls
        self.e.eng.ensure_protection(pid)
        self.assertEqual(self.e.mock.place_calls,calls)
        self.e.eng.reconcile()
        self.assertEqual(self.e.pos(pid)['state'],P.PROTECTED.value)
        self.assertEqual(self.e.mock.place_calls,calls)

    def test_partial_entry_recomputes_after_fees_and_quantity_change(self):
        r=self.e.eng.submit_entry(self.e.signal('partial',strategy='WY-PULLBACK'))
        o=self.e.order(r.client_order_id);q=D(o['qty']);first=D('.012')
        for qty,price in [(first,D(77000)),(q-first,D(77100))]:
            fee=qty*price*D('.0004')
            self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,qty,price,fee=str(fee)))
        self.assertGreaterEqual(self.roi(r.client_order_id),D(3))
        self.assertLess(self.roi(r.client_order_id),D('3.02'))

    def test_invalid_percentage_rejected(self):
        pid=self.opened()
        for pct in ('NaN','Infinity','-1','0'):
            self.e.config.take_profit_margin_pct=D(pct)
            with self.assertRaises(ValueError):self.e.eng.protection_prices(self.e.pos(pid),self.e.meta.filters('BTCUSDT-PERP'))

    def test_all_strategy_sleeves_use_margin_target(self):
        for side,strategy in [('long','WY-REVERT'),('short','WY-PULLBACK')]:
            pid=self.opened(side,strategy,strategy=strategy)
            self.assertGreaterEqual(self.roi(pid),D(3));self.assertLess(self.roi(pid),D('3.02'))


class TenantMarginSettingTests(unittest.TestCase):
    def setUp(self):
        self.e=TenantEnv();self.addCleanup(self.e.cleanup)
        for aid in ('acctA','acctB'):self.e.accounts.set(ctx(aid,100))
        self.e.ready();self.a=self.e.reg.tenants['acctA'];self.b=self.e.reg.tenants['acctB']

    def setting(self,**kw):
        data=dict(account_id='acctA',scope='individual_positions',return_on_margin_pct='3',estimated_exit_taker_fee_rate='.0004');data.update(kw)
        Path(self.a.dir,'take_profit_margin.json').write_text(json.dumps(data))

    def test_only_selected_account_and_persists_across_restart(self):
        self.setting();self.a.apply_context(self.a.ctx)
        self.assertEqual(self.a.eng.cfg.take_profit_margin_pct,D(3));self.assertIsNone(self.b.eng.cfg.take_profit_margin_pct)
        self.assertEqual(self.a.runtime_info()['take_profit_scope'],'individual_positions')
        self.a.shutdown();self.b.shutdown();self.e.build();self.e.ready()
        self.assertEqual(self.e.reg.tenants['acctA'].eng.cfg.take_profit_margin_pct,D(3))
        self.assertIsNone(self.e.reg.tenants['acctB'].eng.cfg.take_profit_margin_pct)

    def test_bad_scope_account_or_number_rejected(self):
        for change in [dict(scope='combined'),dict(account_id='acctB'),dict(return_on_margin_pct='NaN'),dict(estimated_exit_taker_fee_rate='-1')]:
            self.setting(**change)
            with self.assertRaises(ValueError):margin_take_profit(self.a.dir,'acctA')
