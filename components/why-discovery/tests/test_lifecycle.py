import copy
import threading
import unittest
from unittest.mock import patch
from discovery.agents import Agents
from discovery.engine import Engine
from discovery.store import Store
from discovery import rules
from test_discovery import Clock,MARKET,LIMITS,SPEC

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.store=Store(':memory:');self.addCleanup(self.store.db.close)
        self.e=Engine(self.store,self.clock);self.e.markets={MARKET:LIMITS};self.e.action('alice','start')
        self.cid=self.e.propose('alice',SPEC,{});self.c=self.e.accounts['alice']['candidates'][self.cid]
        self.fresh()
    def fresh(self):
        now=self.clock.t
        self.e.books.books[MARKET]=dict(bids={100.:1.},asks={100.02:1.},received=now,source=now,seq=1)
        self.e.books.marks[MARKET]=dict(rate=0.,next=now+28800,source=now,received=now)
    def failed(self):
        self.c['trades']=[dict(id=str(i),net=-.1,valid=True,reason='STOP',closed=self.clock.t) for i in range(10)]
        self.e.retire('alice',self.c,rules.metrics(self.c['trades']));self.e.commit()
    def model(self,output):
        class Model:
            ready=True;model='fixture-model'
            def __init__(self):self.requests=[];self.inputs=[]
            def ask(self,role,evidence,schema):
                self.inputs.append((role,copy.deepcopy(evidence)))
                return output
        model=Model();return model,Agents(self.e,model,threading.Event())
    def test_failed_screen_retires_only_after_ten_valid_trials(self):
        self.c['trades']=[dict(net=-.1,valid=True,reason='STOP',closed=self.clock.t) for _ in range(9)]
        self.c['trades'].append(dict(net=-1,valid=False,reason='STALE_MARKET_DATA',closed=self.clock.t))
        self.e.tick();self.assertIsNotNone(self.c['entry']);self.clock.t+=.3;self.fresh()
        self.e.maker_trade('alice',self.c,dict(price=100,qty=100,ts=self.clock.t,side='sell',id='final'),self.clock.t)
        self.e.exit_fill('alice',self.c,self.c['position']['qty'],99.9,self.clock.t,'STOP')
        self.assertEqual(self.c['state'],'RETIRED');self.assertEqual(self.c['revision']['status'],'PENDING')
        self.assertEqual(self.c['retirement']['score']['closed'],10)
        self.clock.t+=20;self.fresh();self.e.tick();self.assertIsNone(self.c['entry'])
    def test_old_failed_records_retire_on_restart_without_changing_trials(self):
        self.failed();before=copy.deepcopy(self.c['trades']);self.c['state']='SCREEN_FAILED'
        self.c.pop('revision');self.c.pop('retirement');self.e.dirty=True;self.e.commit()
        restarted=Engine(self.store,self.clock);c=restarted.accounts['alice']['candidates'][self.cid]
        self.assertEqual(c['state'],'RETIRED');self.assertEqual(c['trades'],before)
        self.assertEqual(c['revision']['status'],'PENDING')
    def test_rotation_gives_each_eligible_strategy_an_entry_turn(self):
        for hold in (60,75):self.e.propose('alice',dict(SPEC,max_hold_seconds=7200+hold),{})
        turns=[]
        for _ in range(6):
            self.fresh();self.e.tick()
            active=[c for c in self.e.accounts['alice']['candidates'].values() if c['entry']]
            self.assertEqual(len(active),1);turns.append(active[0]['id'])
            self.clock.t+=16
        self.assertEqual(len(set(turns[:3])),3);self.assertEqual(turns[:3],turns[3:])
        self.assertFalse(any(c['position'] for c in self.e.accounts['alice']['candidates'].values()))
    def test_analysis_excludes_feed_failures_and_child_starts_fresh(self):
        self.failed();self.c['trades'].insert(0,dict(net=-999,valid=False,reason='STALE_MARKET_DATA',closed=self.clock.t))
        before=copy.deepcopy(self.c['trades']);spec_before=copy.deepcopy(self.c['spec'])
        changed=dict(SPEC,max_hold_seconds=7260)
        model,agents=self.model(dict(strategies=[changed],reason='Hypothesis: shorter holding may reduce stop exposure. Needs a fresh test.'))
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(model.inputs[0][0],'strategy_revision')
        self.assertEqual(len(model.inputs[0][1]['trades']),10)
        self.assertTrue(all(t['valid'] for t in model.inputs[0][1]['trades']))
        self.assertEqual(self.c['state'],'RETIRED');self.assertEqual(self.c['trades'],before);self.assertEqual(self.c['spec'],spec_before)
        child=self.e.accounts['alice']['candidates'][self.c['revision']['child']]
        self.assertEqual(child['state'],'TESTING');self.assertEqual(child['trades'],[])
        self.assertEqual(child['lineage']['parent'],self.cid);self.assertEqual(child['lineage']['generation'],2)
        self.assertNotEqual(child['version'],self.c['version']);self.assertFalse(self.e.state('alice')['live_enabled'])
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(len(model.inputs),1)
    def test_name_only_change_cannot_reenter_same_settings(self):
        self.failed();model,agents=self.model(dict(strategies=[dict(SPEC,name='New wording')],reason='Change name'))
        with patch('discovery.agents.time.time',self.clock):agents.revise('alice',self.cid)
        self.assertEqual(len(self.e.accounts['alice']['candidates']),1)
        self.assertEqual(self.c['revision']['error'],'REVISION_SETTINGS_UNCHANGED')
    def test_no_defensible_tweak_keeps_original_retired(self):
        self.failed();model,agents=self.model(dict(strategies=[],reason='Insufficient valid evidence to justify a change.'))
        with patch('discovery.agents.time.time',self.clock):agents.once();agents.once()
        self.assertEqual(self.c['state'],'RETIRED');self.assertEqual(self.c['revision']['status'],'NO_CHANGE')
        self.assertEqual(len(model.inputs),1)
    def test_pause_during_analysis_does_not_create_new_candidate(self):
        self.failed();model,agents=self.model(dict(strategies=[dict(SPEC,max_hold_seconds=7260)],reason='Trial'))
        ask=model.ask
        def pause(*args):self.e.action('alice','pause');return ask(*args)
        model.ask=pause
        with patch('discovery.agents.time.time',self.clock):agents.revise('alice',self.cid)
        self.assertEqual(len(self.e.accounts['alice']['candidates']),1);self.assertEqual(self.c['revision']['status'],'WAITING_SLOT')
    def test_full_capacity_queues_analysis_and_retests_without_another_model_call(self):
        self.failed()
        for hold in range(100,228):self.e.propose('alice',dict(SPEC,max_hold_seconds=7200+hold),{})
        model,agents=self.model(dict(strategies=[dict(SPEC,max_hold_seconds=7260)],reason='Shorter holding hypothesis.'))
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(len(model.inputs),1);self.assertEqual(self.c['revision']['status'],'WAITING_SLOT')
        active=[c for c in self.e.accounts['alice']['candidates'].values() if c['state']=='TESTING']
        self.assertEqual(len(active),128)
        active[0]['state']='APPROVED_PAPER'
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(len(model.inputs),1);self.assertEqual(self.c['revision']['status'],'CREATED')
        self.assertEqual(len([c for c in self.e.accounts['alice']['candidates'].values() if c['state']=='TESTING']),128)

    def test_stale_market_does_not_trigger_failure_analysis(self):
        self.failed();self.clock.t+=6;model,agents=self.model(dict(strategies=[],reason=''))
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(model.inputs,[])

if __name__=='__main__':unittest.main()
