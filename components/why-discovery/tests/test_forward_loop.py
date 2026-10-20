import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from discovery import rules
from discovery.agents import Agents,context
from discovery.engine import Engine,INACTIVE_TEST_SECONDS
from discovery.store import Store
from test_discovery import Clock,MARKET,LIMITS,SPEC

class ForwardLoopTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.db=Store(':memory:');self.addCleanup(self.db.db.close)
        self.e=Engine(self.db,self.clock);self.e.markets={MARKET:LIMITS};self.e.action('alice','start')
        self.cid=self.e.propose('alice',SPEC,{});self.c=self.e.accounts['alice']['candidates'][self.cid];self.fresh()
    def fresh(self):
        t=self.clock.t
        self.e.books.books[MARKET]=dict(bids={100.:1.},asks={100.02:1.},received=t,source=t,seq=1)
        self.e.books.marks[MARKET]=dict(rate=0.,next=t+28800,source=t,received=t)
    def approve(self):
        self.c['trades']=[dict(id='screen'+str(i),net=.1,reason='TARGET',valid=True,closed=self.clock.t) for i in range(10)]
        self.c['state']='APPROVED_PAPER'
    def fill_and_close(self,price=100.1,valid=True,reason='TARGET'):
        self.clock.t+=11;self.fresh();self.e.tick();self.assertIsNotNone(self.c['entry'])
        self.clock.t+=.3;self.fresh();self.e.maker_trade('alice',self.c,dict(price=100,qty=100,ts=self.clock.t,side='sell',id=str(self.clock.t)),self.clock.t)
        p=self.c['position'];self.assertEqual(p['phase'],'FORWARD_PAPER');p['valid']=valid
        if not valid:p['invalid_reason']='EMPTY_ORDER_BOOK'
        self.e.exit_fill('alice',self.c,p['qty'],price,self.clock.t,reason)
    def test_approved_version_continues_and_screen_stays_frozen(self):
        self.approve();before=copy.deepcopy(self.c['trades']);score=rules.metrics(before)
        for _ in range(11):self.fill_and_close()
        self.assertEqual(self.c['state'],'APPROVED_PAPER');self.assertEqual(self.c['trades'][:10],before)
        self.assertEqual(rules.metrics(self.c['trades']),score);self.assertEqual(self.c['forward']['evaluated'],10)
        self.assertEqual(len(self.c['forward']['batches']),1)
        state=self.e.state('alice');self.assertEqual(state['candidates'][0]['forward_score']['closed'],11)
        self.assertFalse(state['live_enabled']);self.assertTrue(all(t.get('mode','paper')=='paper' for t in self.c['trades']))
    def test_failed_forward_batch_retires_and_revision_uses_failed_forward_trials(self):
        self.approve()
        for _ in range(10):self.fill_and_close(price=99.9,reason='STOP')
        self.assertEqual(self.c['state'],'RETIRED');self.assertEqual(self.c['retirement']['phase'],'FORWARD_PAPER')
        class Model:
            model='fixture';ready=True;requests=[]
            def ask(inner,role,evidence,schema):
                inner.evidence=evidence;return dict(strategies=[],reason='No supported change')
        m=Model();agents=Agents(self.e,m,threading.Event())
        with patch('discovery.agents.time.time',self.clock):agents.revise('alice',self.cid)
        self.assertEqual(len(m.evidence['trades']),10);self.assertTrue(all(t['net']<0 for t in m.evidence['trades']))
        self.assertEqual(m.evidence['phase'],'FORWARD_PAPER')
    def test_invalid_and_owner_closes_do_not_advance_forward_batch(self):
        self.approve()
        screen=self.e.state('alice')['candidates'][0]['score']
        for _ in range(10):self.fill_and_close(valid=False)
        self.fill_and_close(reason='OWNER_CLOSE')
        self.assertEqual(self.c['state'],'APPROVED_PAPER');self.assertEqual(self.c['forward']['evaluated'],0)
        self.assertEqual(self.e.state('alice')['candidates'][0]['forward_score']['closed'],0)
        self.assertEqual(self.e.state('alice')['candidates'][0]['score'],screen)
    def test_expired_capacity_automatically_admits_a_revised_version(self):
        self.c['trades']=[dict(id=str(i),net=-.1,reason='STOP',valid=True,closed=self.clock.t) for i in range(10)]
        original=copy.deepcopy(self.c['trades']);self.e.retire('alice',self.c,rules.metrics(original))
        for hold in range(100,228):self.e.propose('alice',dict(SPEC,max_hold_seconds=7200+hold),{})
        class Model:
            model='fixture';ready=True;requests=[]
            def ask(inner,*args):return dict(strategies=[dict(SPEC,max_hold_seconds=7260)],reason='Changed hold; requires independent paper trials')
        agents=Agents(self.e,Model(),threading.Event())
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(self.c['revision']['status'],'WAITING_SLOT')
        self.clock.t+=max(INACTIVE_TEST_SECONDS,2*(SPEC['max_hold_seconds']+227))+1;self.fresh()
        with patch('discovery.agents.time.time',self.clock):agents.once()
        self.assertEqual(self.c['revision']['status'],'CREATED');self.assertEqual(self.c['trades'],original)
        child=self.e.accounts['alice']['candidates'][self.c['revision']['child']]
        self.assertEqual(child['state'],'TESTING');self.assertEqual(child['trades'],[])
    def test_legacy_losing_approval_is_retired_on_restart(self):
        self.approve();self.c['trades']=[dict(net=1,reason='TARGET',closed=self.clock.t) for _ in range(6)]+[dict(net=-2,reason='STOP',closed=self.clock.t) for _ in range(4)]
        self.e.dirty=True;self.e.commit();e=Engine(self.db,self.clock)
        self.assertEqual(e.accounts['alice']['candidates'][self.cid]['state'],'RETIRED')
    def test_pause_and_kill_block_forward_entries(self):
        self.approve();self.e.action('alice','pause');self.e.tick();self.assertIsNone(self.c['entry'])
        self.e.accounts['alice']['running']=True;self.e.accounts['alice']['kill']=True;self.e.tick();self.assertIsNone(self.c['entry'])
    def test_expiry_releases_slots_even_when_entry_attempts_repeat(self):
        self.e.tick();self.assertIsNotNone(self.c['entry']);self.clock.t+=max(INACTIVE_TEST_SECONDS,2*SPEC['max_hold_seconds'])+1;self.fresh()
        self.c['last_entry']=self.clock.t;self.e.expire_inactive('alice',self.clock.t)
        self.assertEqual(self.c['state'],'EXPIRED');self.assertIsNone(self.c['entry'])
        self.assertIsNotNone(self.e.propose('alice',dict(SPEC,max_hold_seconds=7260),{}))
    def test_expiry_preserves_positions_and_paused_or_stale_accounts(self):
        self.clock.t+=INACTIVE_TEST_SECONDS+1
        self.e.expire_inactive('alice',self.clock.t);self.assertEqual(self.c['state'],'TESTING')
        self.fresh();self.e.accounts['alice']['running']=False
        self.e.expire_inactive('alice',self.clock.t);self.assertEqual(self.c['state'],'TESTING')
        self.e.accounts['alice']['running']=True;self.c['position']={'qty':1}
        self.e.expire_inactive('alice',self.clock.t);self.assertEqual(self.c['state'],'TESTING')
    def test_expiry_respects_recent_trial_and_long_hold(self):
        self.clock.t+=INACTIVE_TEST_SECONDS+1;self.fresh()
        self.c['trades']=[dict(closed=self.clock.t-60,valid=True,reason='STOP')]
        self.e.expire_inactive('alice',self.clock.t);self.assertEqual(self.c['state'],'TESTING')
        self.c['trades']=[];self.c['spec']['max_hold_seconds']=7200
        self.e.expire_inactive('alice',self.clock.t);self.assertEqual(self.c['state'],'TESTING')
    def test_context_failure_is_precise_and_retry_is_bounded(self):
        self.approve();self.c['state']='REVIEW_PENDING'
        class Model:model='fixture';requests=[]
        agents=Agents(self.e,Model(),threading.Event());agents.context_path='/nonexistent/why-context.json'
        with patch('discovery.agents.time.time',self.clock):agents.review('alice',self.cid)
        self.assertEqual(self.c['review']['reason'],'CONTEXT_FILE_MISSING')
        self.assertEqual(self.c['review']['next_retry_at'],self.clock.t+300)
        self.assertEqual(self.c['state'],'REVIEW_DELAYED')
    def test_valid_context_approves_swing_then_paper_entry_runs(self):
        self.approve();self.c['state']='REVIEW_PENDING'
        class Model:
            model='fixture';requests=[]
            def ask(inner,role,*args):
                return dict(verdict='confirm',reason='Fixture confirms',sentiment=0) if role=='news' else dict(side='long',action='open',reason='Fixture aligned')
        agents=Agents(self.e,Model(),threading.Event())
        data=dict(as_of=self.clock.t,news=[],calendar=[],markets={MARKET:dict(funding_shock=False,oi_shock=False,sentiment=0)})
        with patch('discovery.agents.context',return_value=data),patch('discovery.agents.time.time',self.clock):agents.review('alice',self.cid)
        self.assertEqual(self.c['state'],'APPROVED_PAPER');self.fill_and_close()
    def test_positive_win_count_with_zero_net_does_not_pass(self):
        ts=[dict(net=1,reason='TARGET') for _ in range(6)]+[dict(net=-1.5,reason='STOP') for _ in range(4)]
        self.assertFalse(rules.metrics(ts)['qualified'])

if __name__=='__main__':unittest.main()
