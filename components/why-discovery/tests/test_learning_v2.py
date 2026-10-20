import copy,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from discovery import learning,rules
from discovery.engine import Engine
from discovery.store import Store
from discovery.agents import Agents,context
from discovery.context_feed import parse_news,parse_calendar,market_context,Feed,NEWS,FUNDING,CALENDAR
from test_discovery import Clock,MARKET,LIMITS,SPEC

class LearningTests(unittest.TestCase):
    def test_validation_requires_fifty_and_elapsed_window_and_payoff(self):
        def trades(n,span=86401):return [dict(net=1 if i%5 else -.5,reason='TARGET',closed=1800000000+i*span/max(n-1,1),opened=1800000000+i*span/max(n-1,1)-10,phase='FORWARD_PAPER',valid=True) for i in range(n)]
        self.assertFalse(learning.validation(trades(49))['qualified'])
        self.assertFalse(learning.validation(trades(50,100))['qualified'])
        self.assertTrue(learning.validation(trades(50))['qualified'])
        t=trades(50);t[0]['net']=-11;self.assertFalse(learning.validation(t)['qualified'])
        for i in range(50):t[i]['net']=.1 if i%5 else -.39
        self.assertEqual(learning.validation(t)['status'],'NOT_QUALIFIED')
    def test_stats_excludes_invalid_and_reports_payoff_drawdown(self):
        ts=[dict(net=n,reason='TARGET') for n in [2,-1,-2,4]]+[dict(net=-999,reason='STOP',valid=False)]
        s=learning.stats(ts);self.assertEqual(s['net'],3);self.assertEqual(s['max_drawdown'],3);self.assertEqual(s['average_win'],3);self.assertEqual(s['average_loss'],-1.5);self.assertEqual(s['expectancy'],.75)
    def test_executable_identity_ignores_rename_and_condition_order(self):
        a=copy.deepcopy(SPEC);a['conditions'].append(dict(feature='spread_bps',op='lt',value=10))
        b=dict(a,name='Renamed',thesis='Renamed thesis',conditions=list(reversed(a['conditions'])))
        self.assertEqual(rules.executable_identity(a),rules.executable_identity(b))

class EngineLearningTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.store=Store(':memory:');self.addCleanup(self.store.db.close);self.e=Engine(self.store,self.clock)
        self.e.markets={MARKET:LIMITS};self.e.action('alice','start');self.cid=self.e.propose('alice',SPEC,{});self.c=self.e.accounts['alice']['candidates'][self.cid];self.fresh()
    def fresh(self,price=100):
        self.e.books.books[MARKET]=dict(bids={price:1.},asks={price+.02:1.},source=self.clock.t,received=self.clock.t,seq=1)
        self.e.books.marks[MARKET]=dict(rate=0,next=self.clock.t+28800,source=self.clock.t)
    def test_archive_preserves_pnl_daily_risk_and_history_after_restart(self):
        self.c['trades']=[dict(id='valid',net=-1,closed=self.clock.t,reason='STOP',valid=True),dict(id='invalid',net=-2,closed=self.clock.t,reason='STALE_MARKET_DATA',invalid_reason='STALE_MARKET_DATA',valid=False)]
        self.c['state']='EXPIRED';original=copy.deepcopy(self.c);risk=self.e.account_risk(self.e.accounts['alice'],MARKET,'long',self.clock.t);view=self.e.state('alice')
        with patch('discovery.engine.ACTIVE_VERSION_LIMIT',1):self.assertIsNotNone(self.e.propose('alice',dict(SPEC,max_hold_seconds=7260),{}))
        self.assertNotIn(self.cid,self.e.accounts['alice']['candidates'])
        self.assertEqual(self.e.archives('alice')['items'][0]['candidate'],original)
        self.assertEqual(self.e.archives('bob')['items'],[])
        self.assertEqual(self.e.state('alice')['realized'],view['realized'])
        self.assertEqual(self.e.account_risk(self.e.accounts['alice'],MARKET,'long',self.clock.t).equity,risk.equity)
        self.assertEqual(self.e.daily(self.e.accounts['alice'],self.clock.t),-3)
        restarted=Engine(self.store,self.clock);self.assertEqual(restarted.state('alice')['realized'],view['realized'])
        self.assertIsNone(self.e.propose('alice',dict(SPEC,name='Cosmetic rename'),{}))
        self.assertEqual(len(self.e.research_memory('alice',{MARKET})['versions']),1)
    def test_pending_revision_is_not_archived(self):
        self.e.retire('alice',self.c,rules.metrics([]))
        with patch('discovery.engine.ACTIVE_VERSION_LIMIT',1):self.assertIsNone(self.e.propose('alice',dict(SPEC,max_hold_seconds=7260),{}))
        self.assertIn(self.cid,self.e.accounts['alice']['candidates'])
    def test_memory_is_separate_per_account(self):
        self.c['trades']=[dict(net=-1,reason='STOP')];self.e.action('bob','start')
        self.assertEqual(self.e.research_memory('bob',{MARKET})['versions'],[])
        self.assertEqual(self.e.research_memory('alice',{MARKET})['versions'][0]['screen']['net'],-1)
    def test_new_generation_receives_account_memory(self):
        self.c['trades']=[dict(net=-.1,reason='STOP',closed=self.clock.t)]
        class Model:
            model='fixture';requests=[];ready=True
            def ask(inner,role,evidence,schema):inner.input=evidence;return dict(strategies=[],reason='No supported setup')
        model=Model();a=Agents(self.e,model,threading.Event())
        with patch('discovery.agents.time.time',self.clock):a.once()
        self.assertEqual(model.input['empirical_memory']['versions'][0]['screen']['net'],-.1)
    def test_daily_lock_survives_same_day_and_releases_next_day(self):
        a=self.e.accounts['alice'];a['kill']=True;a['risk_lock']=dict(day=self.e.day(self.clock.t),reason='DAILY_LOSS_LIMIT')
        self.e.tick();self.assertTrue(a['kill']);self.assertIsNone(self.c['entry'])
        self.clock.t+=86400;self.fresh();self.e.tick();self.assertFalse(a['kill'])
    def test_unknown_lock_does_not_reset(self):
        a=self.e.accounts['alice'];a['kill']=True;self.clock.t+=86400;self.fresh();self.e.tick();self.assertTrue(a['kill'])
    def test_diagnostics_capture_entry_and_excursions_without_unbounded_paths(self):
        self.e.tick();self.clock.t+=.3;self.fresh();self.e.maker_trade('alice',self.c,dict(price=100,qty=100,ts=self.clock.t,side='sell',id='entry'),self.clock.t)
        for i in range(200):
            self.clock.t+=5;self.fresh(100.1 if i%2 else 99.9);self.e.observe_position(self.c,self.clock.t)
        d=self.c['position']['diagnostics'];self.assertAlmostEqual(d['mfe_bps'],10);self.assertAlmostEqual(d['mae_bps'],-10);self.assertLessEqual(len(d['path']),60);self.assertIn('features',d)
        q=self.c['position']['qty'];self.e.exit_fill('alice',self.c,q,100.05,self.clock.t,'MAX_HOLD')
        self.assertGreater(self.c['trades'][0]['diagnostics']['hold_seconds'],900)

class ContextTests(unittest.TestCase):
    def test_cached_payload_is_revalidated_at_current_time(self):
        now=[100.];calls=[]
        f=Feed('.',fetcher=lambda url:calls.append(url) or b'payload',clock=lambda:now[0])
        def parse(raw):
            if now[0]>101:raise ValueError('STALE')
            return raw.decode()
        self.assertEqual(f.cached('test','https://example.test',300,parse)['data'],'payload')
        now[0]=102
        with self.assertRaisesRegex(ValueError,'STALE'):f.cached('test','https://example.test',300,parse)
        self.assertEqual(len(calls),1)
    def test_calendar_week_rollover_uses_provider_timezone(self):
        import datetime as dt
        raw=json.dumps([dict(title='CPI',country='USD',date='2026-10-15T08:30:00-04:00',impact='High')]).encode()
        saturday=dt.datetime.fromisoformat('2026-10-18T01:00:00+00:00').timestamp()
        parse_calendar(raw,saturday)
        with self.assertRaisesRegex(ValueError,'CALENDAR_WRONG_WEEK'):parse_calendar(raw,saturday+4*3600)
    def test_calendar_rejects_timezone_free_or_old_week(self):
        now=1792009000.
        def raw(date):return json.dumps([dict(title='CPI',country='USD',date=date,impact='High')]).encode()
        self.assertTrue(parse_calendar(raw('2026-10-15T08:30:00-04:00'),now))
        with self.assertRaises(ValueError):parse_calendar(raw('2026-10-15T08:30:00'),now)
        with self.assertRaises(ValueError):parse_calendar(raw('2025-10-15T08:30:00-04:00'),now)
    def test_news_rejects_stale_and_entity_payloads(self):
        with self.assertRaises(ValueError):parse_news(b'<!DOCTYPE rss><rss/>','source',1792009000.)
        with self.assertRaises(ValueError):parse_news(b'<rss><channel><item><title>test</title><pubDate>Wed, 14 Oct 2020 12:00:00 GMT</pubDate></item></channel></rss>','source',1792009000.)
    def test_market_requires_actual_fresh_baseline(self):
        now=1792009000.;mark=dict(symbol='BTCUSDT',time=now*1000,lastFundingRate='0.0001');news=[dict(title='Bitcoin recovery',sentiment=1)]
        oi=[dict(symbol='BTCUSDT',sumOpenInterest='100',timestamp=(now-600)*1000),dict(symbol='BTCUSDT',sumOpenInterest='110',timestamp=(now-300)*1000)]
        m=market_context('BTC',mark,oi,news,now);self.assertTrue(m['oi_shock']);self.assertFalse(m['funding_shock'])
        with self.assertRaises(ValueError):market_context('BTC',mark,oi[:1],news,now)
        with self.assertRaises(ValueError):market_context('BTC',mark,oi,news,now+901)
    def test_context_handles_generated_metadata_and_stale_market_evidence(self):
        now=1792009000.;data=dict(as_of=now,news=[],calendar=[],markets={MARKET:dict(funding_shock=False,oi_shock=False,sentiment=0,evidence=dict(funding_at=now,oi_at=now-300))},sources={},coverage='fixture')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'context.json';path.write_text(json.dumps(data));self.assertEqual(context(str(path),MARKET,now)['as_of'],now)
            data['markets'][MARKET]['evidence']['oi_at']=now-1000;path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'MARKET_CONTEXT_STALE'):context(str(path),MARKET,now)
    def test_failed_feed_never_refreshes_previous_context_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'context.json';p.write_text('{"as_of":1}')
            def fail(url):raise OSError('offline')
            result=Feed(td,fetcher=fail,clock=lambda:1792009000.).once()
            self.assertFalse(result['ready']);self.assertEqual(json.loads(p.read_text())['as_of'],1)
if __name__=='__main__':unittest.main()
