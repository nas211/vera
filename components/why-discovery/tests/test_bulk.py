import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from discovery import bulk_screen as b,bulk_shadow as shadow,bulk_bridge,rules
from discovery.bulk_history import history
from discovery.engine import Engine,PAPER_TRIAL_LIMIT
from discovery.store import Store
from tests.test_discovery import Clock,SPEC,MARKET,LIMITS

class BulkTests(unittest.TestCase):
    def candles(self,n=1200):
        rng=np.random.default_rng(42);close=100*np.exp(np.cumsum(rng.normal(0,.002,n)));op=np.r_[100,close[:-1]]
        return np.array([[i*300000,op[i],max(op[i],close[i])*1.002,min(op[i],close[i])*.998,close[i],float(1+i%7)] for i in range(n)])
    def test_grid_exact_count_unique_and_dsl_valid(self):
        cs,gs=b.catalog();self.assertEqual(len(cs)*len(gs)*20,1800000)
        self.assertEqual(len({b.digest(c) for c in cs}),600);self.assertEqual(len({b.digest(g) for g in gs}),150)
        for side in ('long','short'):
            for g in gs:rules.validate(b.specification(MARKET,side,cs[0],g),{MARKET})
    def test_data_fails_on_gap_duplicate_future_or_bad_geometry(self):
        a=self.candles();now=(a[-1,0]+300000)/1000
        self.assertEqual(len(b.validate_candles(a,now)),1200)
        for field,value in [(0,a[-2,0]),(2,1),(5,-1)]:
            bad=a.copy();bad[-1,field]=value
            with self.assertRaises(ValueError):b.validate_candles(bad,now)
        with self.assertRaises(ValueError):b.validate_candles(a,now-301)
    def test_signal_features_are_causal_and_next_bar(self):
        a=self.candles();f=b.features(a);changed=a.copy();changed[900:,4]*=2;g=b.features(changed)
        for k in f:np.testing.assert_array_equal(f[k][:900],g[k][:900])
        condition=[dict(feature='return_1',op='gt',value=-100000)]
        self.assertGreaterEqual(b.signal_indices(f,condition)[0],2)
    def test_batched_simulator_matches_scalar_both_directions_and_fees(self):
        a=self.candles();cs,gs=b.catalog();f=b.features(a);indices=[b.signal_indices(f,c) for c in cs[:25]];nexts=b.next_entries(indices,len(a))
        for side in ('long','short'):
            for g in gs[::35]:
                path=b.outcomes(a,side,g,LIMITS);batch=b.portfolios(nexts,path,30,1000)
                self.assertTrue(np.all(path['stop_net'][path['valid']]>=-1.8-1e-7))
                self.assertTrue(np.all(path['fees'][path['valid']]>0))
                for i,x in enumerate(indices):
                    ref=b.portfolio(x,path,30,1000,True)
                    for k,v in batch[i].items():
                        if v is None:self.assertIsNone(ref[k])
                        else:self.assertAlmostEqual(v,ref[k],places=7)
                    self.assertTrue(all(start>end for start,end in zip(ref['entries'][1:],ref['exits'][:-1])))
    def test_stop_first_and_gap_losses_are_not_clipped(self):
        a=self.candles();a[:,1:5]=100;a[:,2]=102;a[:,3]=98
        g=dict(stop_bps=40,target_bps=80,max_hold_seconds=7200)
        for side in ('long','short'):
            p=b.outcomes(a,side,g,LIMITS);self.assertEqual(p['reason'][0],0);self.assertLess(p['net'][0],0)
            # A subsequent gap may exceed the trigger cap; never hide that loss.
            x=a.copy();x[:,2:4]=100;x[1,1]=90 if side=='long' else 110
            x[1,2]=max(110,x[1,1]);x[1,3]=min(90,x[1,1])
            p=b.outcomes(x,side,g,LIMITS);self.assertLess(p['net'][0],-2)
    def test_shortlist_uses_training_only(self):
        m=dict(trades=30,net=10,stress_net=8,profit_factor=2,max_drawdown=1)
        rows=[dict(condition_index=i,fingerprint=str(i),train=dict(m,stress_net=8-i),later_folds=[dict(net=10000*i)]) for i in range(10)]
        chosen=b.select_training(rows);self.assertEqual([r['condition_index'] for r in chosen],list(range(5)))
        for r in rows:r['later_folds']=[dict(net=-100000)]
        self.assertEqual(b.select_training(rows),chosen)
    def test_ignored_history_cursor_rejected(self):
        def fetch(path,params):return [[params['endTime']+1,1,1,1,1,1]]
        with self.assertRaisesRegex(ValueError,'cursor_ignored'):history('BTCUSDT-PERP',1_800_000_000,fetch=fetch)
    def test_shadow_future_only_restart_idempotence_and_both_sides_cap(self):
        cs=[[]];gs=[dict(stop_bps=40,target_bps=80,max_hold_seconds=7200)]
        layout=(cs,gs,np.array([0,0]),np.array([0,0]),np.array([1.,-1.]))
        s=shadow.initial(0,cs,gs);self.assertEqual(s['trades'].sum(),0)
        shadow.observe(s,[300000,100,100.1,99.9,100,1],{},LIMITS,layout)
        self.assertEqual(s['qty'].sum(),0);self.assertTrue(s['pending'].all())
        shadow.observe(s,[600000,100,100.1,99.9,100,1],{},LIMITS,layout);self.assertTrue((s['qty']>0).all())
        before=copy.deepcopy(s);shadow.observe(s,[600000,100,110,90,100,1],{},LIMITS,layout)
        for k in before:np.testing.assert_array_equal(s[k],before[k])
        shadow.observe(s,[900000,100,102,98,100,1],{},LIMITS,layout);self.assertEqual(s['trades'].sum(),2);self.assertTrue((s['net']<0).all())
    def test_paper_admits_thirteenth_distinct_trial_without_changing_loss_cap(self):
        e=Engine(Store(':memory:'),Clock());e.markets={MARKET:LIMITS};e.action('alice','start')
        for i in range(13):
            spec=dict(SPEC,name='test'+str(i),conditions=[dict(feature='return_1',op='gt',value=i)])
            self.assertIsNotNone(e.propose('alice',spec,{}))
        self.assertEqual(PAPER_TRIAL_LIMIT,128)
    def test_bulk_cannot_skip_paper_or_cross_user_running_state(self):
        e=Engine(Store(':memory:'),Clock());e.markets={MARKET:LIMITS};e.action('alice','start')
        c,g=b.catalog();spec=b.specification(MARKET,'long',c[0],g[0]);now=e.clock()
        item=dict(passed=True,spec=spec,fingerprint=rules.executable_identity(spec),source='proxy',train={},later_folds=[])
        data=dict(version=1,status='COMPLETED',completed=now,live_enabled=False,run_id='test',shortlist=[item])
        with tempfile.TemporaryDirectory() as td,patch.object(bulk_bridge,'root',return_value=Path(td)):
            Path(td,'latest.json').write_text(json.dumps(data))
            self.assertEqual(bulk_bridge.import_shortlist(e,'missing',now,{MARKET}),0)
            e.action('bob','start');e.action('bob','pause')
            self.assertEqual(bulk_bridge.import_shortlist(e,'bob',now,{MARKET}),0)
            self.assertEqual(bulk_bridge.import_shortlist(e,'alice',now,{MARKET}),1)
            self.assertEqual(bulk_bridge.import_shortlist(e,'alice',now,{MARKET}),0)
            candidate=next(iter(e.accounts['alice']['candidates'].values()))
            self.assertEqual(candidate['state'],'TESTING');self.assertEqual(candidate['trades'],[])

    def test_shadow_disk_restart_preserves_all_state(self):
        cs=[[]];gs=[dict(stop_bps=40,target_bps=80,max_hold_seconds=7200)]
        layout=(cs,gs,np.array([0,0]),np.array([0,0]),np.array([1.,-1.]))
        state=shadow.initial(0,cs,gs)
        shadow.observe(state,[300000,100,100.1,99.9,100,1],{},LIMITS,layout)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'snapshot.npz';np.savez_compressed(path,**state)
            with np.load(path,allow_pickle=False) as z:restored={k:z[k].copy() for k in z.files}
        for k in state:np.testing.assert_array_equal(state[k],restored[k])
        bar=[600000,100,102,98,100,1]
        shadow.observe(state,bar,{},LIMITS,layout);shadow.observe(restored,bar,{},LIMITS,layout)
        for k in state:np.testing.assert_array_equal(state[k],restored[k])
    def test_shadow_gap_never_manufactures_realized_trades(self):
        cs=[[]];gs=[dict(stop_bps=40,target_bps=80,max_hold_seconds=7200)]
        layout=(cs,gs,np.array([0,0]),np.array([0,0]),np.array([1.,-1.]))
        state=shadow.initial(0,cs,gs);state['qty'][:]=1
        with self.assertRaisesRegex(ValueError,'shadow_candle_gap'):
            shadow.observe(state,[600000,100,102,98,100,1],{},LIMITS,layout)
        self.assertEqual(state['trades'].sum(),0);self.assertEqual(state['invalidated'].sum(),2)
