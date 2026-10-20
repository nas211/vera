import copy,unittest
from discovery.engine import Engine, plan_entry
from discovery.store import Store
from discovery.fees import FEE_RATE,effective_net
from discovery import rules
from tests.test_discovery import Clock,MARKET,LIMITS,SPEC

class FeeEconomicsTests(unittest.TestCase):
    def engine(self,side='long'):
        clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS}
        e.action('alice','start');spec=dict(SPEC,side=side);cid=e.propose('alice',spec,{'fixture':True});c=e.accounts['alice']['candidates'][cid]
        e.books.books[MARKET]=dict(bids={100.:100.},asks={100.02:100.},received=clock.t,source=clock.t,seq=1)
        e.books.marks[MARKET]=dict(rate=0,next=clock.t+28800,source=clock.t,received=clock.t)
        order=dict(qty=1.,stop=99. if side=='long' else 101.,created=clock.t,diagnostics={})
        e.entry_fill(c,order,1.,100.,clock.t)
        return e,c,clock
    def test_both_directions_charge_entry_and_exit_and_preserve_net_identity(self):
        for side in ('long','short'):
            e,c,clock=self.engine(side);self.assertAlmostEqual(c['position']['fees_paid'],.04)
            px=101 if side=='long' else 99
            e.exit_fill('alice',c,1.,px,clock.t+1,'TARGET');t=c['trades'][0]
            self.assertAlmostEqual(t['fees'],(100+px)*FEE_RATE)
            self.assertAlmostEqual(t['net'],1-t['fees'])
            self.assertEqual(t['fee_model_version'],2)
            self.assertEqual(effective_net(t),t['net'])
    def test_partial_exit_consumes_budget_and_fees_count_once(self):
        for side in ('long','short'):
            e,c,clock=self.engine(side);p=c['position'];price=99.5 if side=='long' else 100.5
            e.exit_fill('alice',c,.4,price,clock.t+1,'STOP')
            sign=1 if side=='long' else -1
            net=p['gross']+(p['stop']-p['entry'])*p['qty']*sign-p['fees_paid']-p['stop']*p['qty']*FEE_RATE
            self.assertGreaterEqual(net,-2-1e-9)
            self.assertAlmostEqual(p['fees_paid'],.04+.4*price*FEE_RATE)
            stop=p['stop'];e.exit_fill('alice',c,.6,stop,clock.t+2,'STOP');t=c['trades'][0]
            self.assertAlmostEqual(t['fees'],.04+.4*price*FEE_RATE+.6*stop*FEE_RATE)
    def test_legacy_screening_subtracts_cost_without_mutating_history(self):
        t=dict(net=.05,fees=0,qty=1.,entry=100.,exit=100.05,valid=True,reason='TARGET')
        before=copy.deepcopy(t);self.assertLess(effective_net(t),0)
        score=rules.metrics([copy.deepcopy(t) for _ in range(10)])
        self.assertEqual(score['wins'],0);self.assertFalse(score['qualified']);self.assertEqual(t,before)
    def test_fee_aware_sizing_and_inward_rounding(self):
        for side in ('long','short'):
            spec=dict(SPEC,side=side,stop_bps=41)
            plan=plan_entry(spec,100,100.02,LIMITS)
            actual=plan['qty']*(abs(plan['price']-plan['stop'])+(plan['price']+plan['stop'])*FEE_RATE)
            self.assertLessEqual(actual,2)
            raw=(100 if side=='long' else 100.02)*(1-.0041 if side=='long' else 1+.0041)
            if side=='long':self.assertGreaterEqual(plan['stop'],raw-1e-9)
            else:self.assertLessEqual(plan['stop'],raw+1e-9)
    def test_breakeven_includes_both_sides_fees(self):
        for side in ('long','short'):
            e,c,clock=self.engine(side);c['spec']['breakeven_bps']=5
            e.manage_stop('alice',c,100.2,100.22) if side=='long' else e.manage_stop('alice',c,99.78,99.8)
            p=c['position'];sign=1 if side=='long' else -1
            net=(p['stop']-p['entry'])*p['qty']*sign-p['fees_paid']-p['stop']*p['qty']*FEE_RATE
            self.assertGreaterEqual(net,-1e-9)
    def test_partial_fees_survive_restart(self):
        e,c,clock=self.engine();fee=c['position']['fees_paid'];e.commit()
        restored=Engine(e.store,clock);rc=restored.accounts['alice']['candidates'][c['id']]
        self.assertEqual(rc['position']['fees_paid'],fee)
        self.assertEqual(rc['position']['invalid_reason'],'RESTART_GAP')

    def test_legacy_open_fee_migration_is_idempotent_after_partial_exit(self):
        for side in ('long','short'):
            e,c,clock=self.engine(side)
            e.exit_fill('alice',c,.4,99.5 if side=='long' else 100.5,clock.t+1,'STOP')
            p=c['position'];expected=p['fees_paid']
            for key in ('fees_paid','fee_events','fee_model_version'):p.pop(key)
            e.dirty=True;e.commit();restored=Engine(e.store,clock)
            rc=restored.accounts['alice']['candidates'][c['id']]
            self.assertAlmostEqual(rc['position']['fees_paid'],expected)
            restored=Engine(e.store,clock);rc=restored.accounts['alice']['candidates'][c['id']]
            self.assertAlmostEqual(rc['position']['fees_paid'],expected)
            restored.markets={MARKET:LIMITS};restored.cap_stop(rc)
            pos=rc['position'];sign=1 if side=='long' else -1
            net=pos['gross']+(pos['stop']-pos['entry'])*pos['qty']*sign-pos['fees_paid']-pos['stop']*pos['qty']*FEE_RATE
            self.assertGreaterEqual(net,-2-1e-9)

    def test_archived_trials_keep_cost_adjusted_risk_and_raw_history(self):
        e,c,clock=self.engine();e.exit_fill('alice',c,1.,100.05,clock.t+1,'TARGET')
        t=c['trades'][0];t.pop('fee_model_version');t['fees']=0.;t['net']=.05
        c['state']='EXPIRED'
        from unittest.mock import patch
        with patch('discovery.engine.ACTIVE_VERSION_LIMIT',1):e.archive_completed('alice')
        e.commit()
        a=e.accounts['alice'];self.assertAlmostEqual(a['archive']['realized'],.05)
        self.assertLess(a['archive']['risk_realized'],0)
        self.assertLess(e.daily(a,clock.t),0)
        del a['archive']['risk_realized'];del a['archive']['risk_daily'];e.dirty=True;e.commit()
        restored=Engine(e.store,clock);a=restored.accounts['alice']
        self.assertLess(a['archive']['risk_realized'],0)
        self.assertEqual(e.store.archives('alice')[0]['candidate']['trades'][0]['net'],.05)
