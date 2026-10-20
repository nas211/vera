import json, unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from execution.trade_plan import build_plan, stored_plan
from execution.types import Capabilities
from execution.engine import EngineConfig
from tests.execution.harness import Env, ACCT
from tests.execution.test_tenant_fanout import Env as TenantEnv, ctx


def evidence(now, **kw):
    return dict(version=1,as_of=now,hourly_atr_fraction=.006,median_6h_favorable_fraction=.025,samples=244,**kw)

class PlanTests(unittest.TestCase):
    def setUp(self):
        self.e=Env(capabilities=Capabilities(venue_stop_orders=True,conditional_take_profit=True))
        self.addCleanup(self.e.ledger.close);self.e.ready();self.e.config.risk_plan_required=True
        self.e.config.limits=replace(self.e.config.limits,max_open_positions=1,max_notional_per_order=D(500))
    def signal(self,side='long',sid='risk',market='BTCUSDT-PERP',price='77000'):
        sig=self.e.signal(sid,market=market,direction=side,strategy='WY-PULLBACK',price=price)
        p=build_plan(account=ACCT,direction=side,price=D(price),filters=self.e.meta.filters(market),evidence=evidence(self.e.clock()),now=self.e.clock(),configured=self.e.config.max_planned_loss_usd)
        return replace(sig,notional_usd=D(p['notional_usd']),detail=dict(risk_trade_plan=p)),p
    def economics(self,pid):
        p=self.e.pos(pid);prot=self.e.ledger.get_protection(pid);entry=self.e.order(pid);plan=stored_plan(entry)
        paid=realized=D(0);sign=1 if p['direction']=='long' else -1
        orders={o['client_order_id']:o for o in self.e.ledger.orders_for_position(pid)};orders[pid]=entry
        for o in orders.values():
            paid+=D(o['fees'])
            if o['purpose']!='ENTRY':realized+=(D(o['avg_price'])-D(p['entry_price']))*D(o['filled_qty'])*sign
        calc=lambda px:realized+(D(px)-D(p['entry_price']))*D(p['qty'])*sign-paid-D(px)*D(p['qty'])*D('.0004')
        self.assertGreaterEqual(calc(prot['stop_price']),-D(plan['risk_usd']))
        goal=D(plan['target_net_usd'])*D(entry['filled_qty'])/D(plan['planned_qty'])
        self.assertGreaterEqual(calc(prot['tp_price']),goal)
        self.assertLessEqual(D(plan['notional_usd']),500)
        self.assertGreaterEqual(abs(D(prot['stop_price'])/D(p['entry_price'])-1),D('.004'))
    def test_both_directions_fees_partial_exits_and_restart(self):
        for side in ('long','short'):
            with self.subTest(side=side):
                if side=='short':self.setUp()
                sig,plan=self.signal(side);r=self.e.eng.submit_entry(sig);self.assertEqual(r.status,'ACKNOWLEDGED',r)
                q=D(self.e.order(r.client_order_id)['qty']);self.assertEqual(q,D(plan['planned_qty']))
                self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,q,'77000',fee=str(q*D(77000)*D('.0004'))))
                self.economics(r.client_order_id)
                before=self.e.ledger.get_protection(r.client_order_id)
                half=self.e.meta.filters(sig.market).round_qty_down(q/2)
                self.e.eng.on_fill(self.e.mock.fill(before['target_coid'],half,before['tp_price'],fee=str(half*D(before['tp_price'])*D('.0004'))))
                self.e.eng.reconcile();self.economics(r.client_order_id)
                after=self.e.ledger.get_protection(r.client_order_id)
                if side=='long':self.assertGreaterEqual(D(after['stop_price']),D(before['stop_price']))
                else:self.assertLessEqual(D(after['stop_price']),D(before['stop_price']))
                calls=self.e.mock.place_calls;self.assertTrue(self.e.restart().ok);self.assertEqual(self.e.mock.place_calls,calls)
    def test_partial_entry_and_stricter_existing_position_reconciliation(self):
        for side in ('long','short'):
            with self.subTest(side=side):
                if side=='short':self.setUp()
                sig,plan=self.signal(side);r=self.e.eng.submit_entry(sig)
                q=D(self.e.order(r.client_order_id)['qty']);part=self.e.meta.filters(sig.market).round_qty_down(q/2)
                self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,part,'77000',fee=str(part*D(77000)*D('.0004'))))
                self.economics(r.client_order_id)
                remaining=q-part
                self.e.eng.on_fill(self.e.mock.fill(r.client_order_id,remaining,'77000',fee=str(remaining*D(77000)*D('.0004'))))
                self.e.config.max_planned_loss_usd=D('1')
                self.assertTrue(self.e.eng.reconcile().ok)
                pos=self.e.pos(r.client_order_id);prot=self.e.ledger.get_protection(r.client_order_id)
                stop=D(prot['stop_price']);sign=1 if side=='long' else -1
                net=(stop-D(77000))*q*sign-q*D(77000)*D('.0004')-stop*q*D('.0004')
                self.assertGreaterEqual(net,-D(1))
                self.assertTrue(self.e.restart().ok)

    def test_missing_plan_cannot_bypass(self):
        self.assertEqual(self.e.eng.submit_entry(self.e.signal()).reason,'risk_plan_required')
    def test_stale_or_extreme_market_skipped(self):
        f=self.e.meta.filters('BTCUSDT-PERP')
        for change in ({'as_of':self.e.clock()-121},{'samples':3},{'hourly_atr_fraction':.03},{'hourly_atr_fraction':float('nan')}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                build_plan(account=ACCT,direction='long',price=D(77000),filters=f,evidence=dict(evidence(self.e.clock()),**change),now=self.e.clock())
    def test_target_beyond_6h_reach_no_longer_skipped(self):
        f=self.e.meta.filters('BTCUSDT-PERP')
        for side in ('long','short'):
            with self.subTest(side=side):
                p=build_plan(account=ACCT,direction=side,price=D(77000),filters=f,
                             evidence=dict(evidence(self.e.clock()),median_6h_favorable_fraction=.0001),now=self.e.clock())
                self.assertGreater(D(p['target_fraction']),D('.0001'))
                self.assertLessEqual(D(p['risk_usd']),D('1.80'))
    def test_one_pending_entry_blocks_second_market(self):
        sig,_=self.signal();self.assertEqual(self.e.eng.submit_entry(sig).status,'ACKNOWLEDGED')
        other,_=self.signal(sid='other',market='ETHUSDT-PERP',price='2500')
        self.assertEqual(self.e.eng.submit_entry(other).reason,'sizing:max_open_positions')
    def test_stricter_cap_kept_and_bad_plans_rejected(self):
        f=self.e.meta.filters('ETHUSDT-PERP')
        p=build_plan(account=ACCT,direction='short',price=D(2500),filters=f,evidence=dict(evidence(self.e.clock()),median_6h_favorable_fraction=.15),now=self.e.clock(),configured=D(1))
        self.assertEqual(p['risk_usd'],'1')
        for change in ({'account_id':'foreign'},{'risk_usd':'3'},{'stop_fraction':'NaN'}):
            with self.assertRaises(ValueError):stored_plan(dict(account_id=ACCT,direction='short',meta=json.dumps(dict(detail=dict(risk_trade_plan=dict(p,**change))))))

class AccountPolicyTests(unittest.TestCase):
    def setUp(self):
        self.e=TenantEnv();self.addCleanup(self.e.cleanup)
        for aid in ('acctA','acctB'):self.e.accounts.set(ctx(aid,100))
        self.e.ready();self.a=self.e.reg.tenants['acctA'];self.b=self.e.reg.tenants['acctB']
        Path(self.a.dir,'fixed_entry_notional.json').write_text(json.dumps(dict(account_id='acctA',notional_usd='500')))
        Path(self.a.dir,'risk_policy.json').write_text(json.dumps(dict(account_id='acctA',version=1,mode='volatility_risk',max_open_positions=1)))
        self.a.apply_context(self.a.ctx)
    def test_only_enabled_account_changes_and_fixed_override_does_not_win(self):
        self.assertTrue(self.a.eng.cfg.risk_plan_required);self.assertFalse(self.b.eng.cfg.risk_plan_required)
        self.assertEqual(self.a.eng.cfg.limits.max_open_positions,1)
        self.assertIsNone(self.a.runtime_info()['fixed_entry_notional_usd'])
        sig=replace(self.e.sig(strategy='WY-PULLBACK'),detail=dict(live_entry_filter={'version':1},risk_evidence=evidence(self.e.clock())))
        r=self.a.submit(sig);self.assertEqual(r.status,'ACKNOWLEDGED',r)
        o=self.a.ledger.get_order(r.client_order_id);self.assertLess(D(o['qty'])*D(77000),500)
        self.assertIn('risk_trade_plan',json.loads(o['meta'])['detail'])
    def test_unconfirmed_and_ai_entries_cannot_bypass(self):
        self.assertEqual(self.a.submit(self.e.sig(strategy='WY-PULLBACK')).reason,'risk_plan_requires_confirmed_pullback')
    def test_risk_plan_timeout_closes_after_six_hours(self):
        sig=replace(self.e.sig(strategy='WY-PULLBACK'),detail=dict(live_entry_filter={'version':1},risk_evidence=evidence(self.e.clock())))
        r=self.a.submit(sig);self.assertEqual(r.status,'ACKNOWLEDGED')
        self.e.fill_entries();self.e.clock.advance(21601);self.e.feed()
        self.a.manage()
        exits=[o for o in self.a.ledger.orders_for_position(r.client_order_id) if o['purpose']=='CLOSE']
        self.assertEqual(len(exits),1)
        self.assertIn('RISK_PLAN_TIMEOUT',exits[0]['meta'])

    def test_invalid_policy_fails_closed(self):
        Path(self.a.dir,'risk_policy.json').write_text('{bad')
        with self.assertRaises(ValueError):self.a.apply_context(self.a.ctx)
        self.assertEqual(self.a.submit(self.e.sig(strategy='WY-PULLBACK')).reason,'risk_policy_invalid')
