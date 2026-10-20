import copy
import io
import dataclasses
import json
import math
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from discovery import gate, rules
from discovery.agents import Agents, context, news_rules
from discovery.engine import Engine, FEED_GRACE_SECONDS
from discovery.market import Books, BookRefresh, universe
from discovery.model import Model
from discovery.server import handler
from discovery.store import Store

MARKET='BTCUSD-PERP'
LIMITS=dict(tick=.01,step=.01,min_qty=.01,min_notional=1.)
SPEC=dict(name='Generated test',thesis='Test observed imbalance',market=MARKET,mode='swing',side='long',conditions=[dict(feature='book_imbalance',op='gt',value=-1)],match='all',stop_bps=40,target_bps=60,max_hold_seconds=7200)

class Clock:
    t=1800000000.
    def __call__(self):return self.t

class GateTests(unittest.TestCase):
    def setUp(self):
        self.i=gate.Intent('intent','chain',MARKET,'long',1.,100.,99.)
        self.a=gate.Account()
        self.kw=dict(markets={MARKET},mid=100.,age=0.,min_qty=.01,step=.01,min_notional=1.)
    def decision(self,ip=None,ap=None,kp=None):return gate.decide(dataclasses.replace(self.i,**(ip or {})),dataclasses.replace(self.a,**(ap or {})),**dict(self.kw,**(kp or {})))
    def test_approved_bound_to_exact_intent(self):
        d=self.decision();self.assertTrue(d.approved);self.assertTrue(gate.accepts(self.i,d))
        self.assertFalse(gate.accepts(dataclasses.replace(self.i,qty=2),d))
        self.assertFalse(gate.accepts(dataclasses.replace(self.i,id='new'),d))
        self.assertFalse(gate.accepts(self.i,self.decision(ip=dict(mode='live'))))
    def test_every_gate_rejects_its_failure(self):
        cases=[('paper_only',dict(mode='live'),{},{}),('identity',dict(chain_id=''),{},{}),('symbol',dict(market='NO'),{},{}),
            ('side',dict(side='flat'),{},{}),('positive',dict(qty=0),{},{}),('leverage',dict(leverage=11),{},{}),
            ('leverage',dict(leverage=True),{},{}),('fresh',{},{},dict(age=5.1)),('fresh',{},{},dict(age=-1)),
            ('price_sanity',dict(price=104),{},{}),('lot',dict(qty=.015),{},{}),('lot',{},{},dict(step=0)),
            ('lot',dict(qty=.001),{},{}),('minimum',dict(qty=.01),{},dict(min_notional=2)),
            ('stop',dict(stop=0),{},{}),('stop',dict(stop=101),{},{}),('planned_loss',dict(qty=2),{},{}),
            ('kill',{},dict(kill=True),{}),('kill',{},dict(global_kill=True),{}),('kill',{},dict(daily_net=-20),{}),
            ('coin_exposure',{},dict(coin=450),{}),('side_exposure',{},dict(side=950),{}),
            ('total_exposure',{},dict(total=1950),{}),('total_exposure',{},dict(equity=40),{}),
            ('open_orders',{},dict(open_orders=8),{}),('order_rate',{},dict(recent_orders=20),{}),
            ('reduce_only_size',dict(reduce_only=True),dict(position_qty=.5),{})]
        for reason,ip,ap,kp in cases:
            with self.subTest(reason=reason,ip=ip,ap=ap,kp=kp):
                d=self.decision(ip,ap,kp);self.assertFalse(d.approved);self.assertFalse(dict(d.checks)[reason])
    def test_nonfinite_and_types(self):
        for v in (float('nan'),float('inf'),True,'1'):
            d=self.decision(ip=dict(qty=v));self.assertFalse(d.approved);self.assertEqual(d.reject_reason,'non_finite')
        self.assertFalse(self.decision(kp=dict(mid=0)).approved)
    def test_short_geometry(self):
        self.assertTrue(self.decision(ip=dict(side='short',stop=101)).approved)
        self.assertFalse(self.decision(ip=dict(side='short',stop=99)).approved)
    def test_reduce_only_survives_kill_and_dust(self):
        d=self.decision(ip=dict(qty=.01,reduce_only=True),ap=dict(position_qty=.01,kill=True,global_kill=True,daily_net=-21,total=10000,coin=10000,side=10000,open_orders=50,recent_orders=100),kp=dict(min_qty=1,min_notional=1000))
        self.assertTrue(d.approved);self.assertTrue(d.trip_kill)

class RuleTests(unittest.TestCase):
    def test_freeze_and_schema(self):
        s=rules.validate(SPEC,{MARKET});self.assertEqual(rules.identity(s),rules.identity(copy.deepcopy(s)))
        changed=copy.deepcopy(s);changed['conditions'][0]['value']=.3
        self.assertNotEqual(rules.identity(s),rules.identity(changed));self.assertTrue(rules.triggered(s,dict(book_imbalance=.1)))
        self.assertFalse(rules.triggered(s,{}));self.assertFalse(rules.triggered(s,dict(book_imbalance=float('nan'))))
    def test_unsafe_proposals(self):
        for patch_value in [dict(code='print(1)'),dict(market='OTHER'),dict(mode='scalp'),dict(target_bps=59),dict(stop_bps=39,target_bps=60),dict(max_hold_seconds=7199),dict(max_hold_seconds=21601),dict(stop_bps=float('nan')),dict(conditions=[dict(feature='__import__',op='gt',value=1)])]:
            with self.subTest(patch_value=patch_value):
                with self.assertRaises(ValueError):rules.validate(dict(SPEC,**patch_value),{MARKET})
    def test_exact_ten_six_positive_not_break_even(self):
        t=[dict(net=1.,valid=True,reason='TARGET') for _ in range(6)]
        self.assertFalse(rules.metrics(t)['qualified'])
        t+=[dict(net=0.,valid=True,reason='TIME') for _ in range(4)]
        self.assertTrue(rules.metrics(t)['qualified']);self.assertEqual(rules.metrics(t)['wins'],6)
        t[0]['net']=0;self.assertFalse(rules.metrics(t)['qualified'])
        self.assertEqual(rules.metrics(t+[dict(net=100,reason='TARGET')])['wins'],5)
    def test_owner_and_gap_excluded_and_positive_pnl_required(self):
        t=[dict(net=1,reason='TARGET') for _ in range(6)]+[dict(net=-10,reason='STOP') for _ in range(4)]
        self.assertFalse(rules.metrics(t)['qualified']);self.assertLess(rules.metrics(t)['net'],0)
        self.assertEqual(rules.metrics([dict(net=10,reason='OWNER_CLOSE'),dict(net=10,reason='TARGET',valid=False)]+t)['excluded'],2)

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.store=Store(':memory:');self.e=Engine(self.store,self.clock);self.e.markets={MARKET:LIMITS};self.seq=0
        self.e.action('alice','start');self.cid=self.e.propose('alice',SPEC,{'fixture':True});self.c=self.e.accounts['alice']['candidates'][self.cid]
        self.book()
    def book(self,bid=100,ask=100.02,depth=1):
        self.seq+=1;t=self.clock.t
        self.e.books.books[MARKET]=dict(bids={bid:float(depth)},asks={ask:float(depth)},received=t,source=t,seq=self.seq)
        self.e.books.marks[MARKET]=dict(rate=0.,next=t+28800,source=t,received=t)
    def entry(self):
        self.e.tick();self.assertIsNotNone(self.c['entry']);self.clock.t+=.3;self.book()
        self.e.maker_trade('alice',self.c,dict(price=100,qty=100,ts=self.clock.t,side='sell',id=uuid_value(self.clock.t)),self.clock.t)
        self.e.tick();self.assertIsNotNone(self.c['position'])
    def test_queue_latency_partial_fills(self):
        self.e.tick();self.assertIsNotNone(self.c['entry']);o=self.c['entry']
        self.e.maker_trade('alice',self.c,dict(price=100,qty=100,ts=self.clock.t,side='sell',id='early'),self.clock.t)
        self.assertIsNone(self.c['position']);self.clock.t+=.3
        self.e.maker_trade('alice',self.c,dict(price=100,qty=1,ts=self.clock.t,side='sell',id='queue'),self.clock.t)
        self.assertIsNone(self.c['position'])
        self.e.maker_trade('alice',self.c,dict(price=100,qty=.25,ts=self.clock.t,side='sell',id='partial'),self.clock.t)
        self.assertAlmostEqual(self.c['position']['qty'],.25);self.assertGreater(o['remaining'],0)
    def test_ten_real_round_trips_stop_screening(self):
        for j in range(10):
            self.book();self.entry();p=self.c['position'];self.assertIsNotNone(p['target'])
            self.clock.t+=.3;self.book()
            self.e.maker_trade('alice',self.c,dict(price=p['target']['price'],qty=100,ts=self.clock.t,side='buy',id=str(j)),self.clock.t)
            self.assertIsNone(self.c['position']);self.clock.t+=11
        self.e.tick();self.assertEqual(self.c['state'],'REVIEW_PENDING');self.assertEqual(len(self.c['trades']),10);self.assertIsNone(self.c['entry'])
        self.assertGreater(sum(t['fees'] for t in self.c['trades']),0);self.assertTrue(rules.metrics(self.c['trades'])['qualified'])
        self.assertTrue(all(t['mode']=='paper' for t in self.c['trades']))
    def test_stop_cancel_before_ioc_partial_retry(self):
        self.entry();self.clock.t+=.1;self.book(99.5,99.52,.5);self.e.tick()
        p=self.c['position'];self.assertEqual(p['exit_reason'],'STOP');self.assertIsNone(p['target']);self.assertIsNone(p['exit_order'])
        self.clock.t+=.26;self.book(99.5,99.52,.5);self.e.tick();self.assertIsNotNone(p['exit_order'])
        self.clock.t+=.26;self.e.tick();remaining=p['qty'];self.assertGreater(remaining,0)
        # Same book cannot be consumed twice by repeated IOC attempts.
        self.clock.t+=.3;self.e.tick();self.clock.t+=.3;self.e.tick();self.assertEqual(p['qty'],remaining)
        self.clock.t+=.3;self.book(99.6,99.62,100);self.e.tick();self.clock.t+=.3;self.e.tick()
        self.assertIsNone(self.c['position']);t=self.c['trades'][0];self.assertLess(t['net'],0)
        events=self.store.events('alice');stages=[x['stage'] for x in reversed(events)]
        self.assertLess(stages.index('exit_trigger'),stages.index('cancel_confirmed'));self.assertLess(stages.index('cancel_confirmed'),stages.index('exit_send'))
    def test_pause_keeps_exit_management(self):
        self.entry();self.e.action('alice','pause');self.assertIsNotNone(self.c['position']);self.assertFalse(self.e.accounts['alice']['running'])
    def test_owner_close_excluded(self):
        self.entry();self.e.action('alice','close',self.cid)
        self.clock.t+=.3;self.book(depth=100);self.e.tick();self.clock.t+=.3;self.e.tick()
        self.assertEqual(self.c['trades'][0]['reason'],'OWNER_CLOSE');self.assertEqual(rules.metrics(self.c['trades'])['closed'],0)
    def test_restart_preserves_position_and_invalidates_screen(self):
        self.entry();self.e.commit();restored=Engine(self.store,self.clock);c=restored.accounts['alice']['candidates'][self.cid]
        self.assertIsNotNone(c['position']);self.assertFalse(c['position']['valid']);self.assertEqual(c['position']['exit_reason'],'RESTART_GAP');self.assertIsNone(c['entry'])
    def test_stale_data_never_enters(self):
        self.clock.t+=6;self.e.tick();self.assertIsNone(self.c['entry'])
    def test_gap_excludes_and_keeps_position(self):
        self.entry();self.e.gap('GAP');p=self.c['position']
        # Short outages hold the position valid; the grace expiry invalidates it.
        self.assertTrue(p['valid']);self.assertEqual(p['feed_gap']['reason'],'GAP');self.assertEqual(p['exit_reason'],'')
        self.clock.t+=FEED_GRACE_SECONDS;self.e.tick()
        self.assertFalse(p['valid']);self.assertEqual(p['exit_reason'],'GAP');self.assertIsNotNone(self.c['position']);self.assertIsNone(p['target'])
    def test_funding_boundary_blocks_entries(self):
        self.e.books.marks[MARKET]['next']=self.clock.t+60;self.e.tick();self.assertIsNone(self.c['entry'])
    def test_empty_market_does_not_reset_other_books_or_crash_position_state(self):
        self.entry();self.e.books.books['OTHER']=copy.deepcopy(self.e.books.books[MARKET])
        empty=dict(market=MARKET,header=dict(type='orderbook.snapshot',created_at=self.clock.t),sequence_num=10,bids=[],asks=[])
        self.assertTrue(self.e.receive('data',empty,self.clock.t))
        self.assertTrue(self.e.books.fresh('OTHER',self.clock.t))
        self.assertTrue(self.e.accounts['alice']['running'])
        self.assertIsNotNone(self.c['position']);self.assertTrue(self.c['position']['valid'])
        self.assertEqual(self.c['position']['feed_gap']['reason'],'EMPTY_ORDER_BOOK')
        self.clock.t+=FEED_GRACE_SECONDS;self.e.tick()
        self.assertFalse(self.c['position']['valid']);self.assertEqual(self.c['position']['exit_reason'],'EMPTY_ORDER_BOOK')
        self.assertIsNone(self.e.state('alice')['candidates'][0]['unrealized'])
    def test_cross_account_state_and_close(self):
        self.entry();self.assertEqual(self.e.state('bob')['candidates'],[])
        with self.assertRaises(ValueError):self.e.action('bob','close',self.cid)
        self.assertIsNotNone(self.c['position']);self.assertEqual(self.store.events('bob'),[])
    def test_no_mutation_of_version(self):
        self.assertIsNone(self.e.propose('alice',SPEC,{}));self.assertEqual(len(self.e.accounts['alice']['candidates']),1)
        self.e.propose('alice',dict(SPEC,target_bps=61),{});self.assertEqual(len(self.e.accounts['alice']['candidates']),2)

def uuid_value(t):return str(t)

class FeedTests(unittest.TestCase):
    def book(self,seq=1,typ='orderbook.snapshot'):
        return dict(market=MARKET,header=dict(type=typ,created_at=1800000000.),sequence_num=seq,bids=[['100','1']],asks=[['101','1']])
    def test_sequence_gap_and_snapshot_not_a_fill(self):
        b=Books();self.assertEqual(b.receive(self.book(),1800000000),[])
        with self.assertRaises(ValueError):b.receive(self.book(3,'orderbook.increment'),1800000000)
        self.assertEqual(b.receive(self.book(1),1800000000),[])
    def test_duplicate_trades_not_filled_twice(self):
        b=Books();d=dict(market=MARKET,header=dict(type='trades.snapshot'),sequence_num=1,trades=[]);b.receive(d,1800000000)
        d.update(header=dict(type='trades.increment'),sequence_num=2,trades=[dict(id='same',price='100',amount='1',executed_at=1800000000,direction='sell')])
        self.assertEqual(len(b.receive(d,1800000000)),1);d['sequence_num']=3;self.assertEqual(b.receive(d,1800000000),[])
    def test_candle_lookahead_excluded(self):
        b=Books();b.seed(MARKET,[[1800000000000,1,2,1,2,5]],1800000001);self.assertEqual(b.bars[MARKET],[])
    def test_dynamic_whitelist_and_limits(self):
        row=dict(symbol=MARKET,status='active',filters=[dict(filter_type='PRICE_FILTER',config=dict(tick_size='.01')),dict(filter_type='LOT_SIZE',config=dict(step_size='.01',min_qty='.01')),dict(filter_type='MIN_NOTIONAL',config=dict(min_notional='1'))])
        self.assertIn(MARKET,universe(dict(symbols=[row])));row['status']='halted'
        with self.assertRaises(ValueError):universe(dict(symbols=[row]))
    def test_empty_book_keeps_contiguous_stream_but_blocks_trading(self):
        b=Books();now=1800000000.;b.receive(self.book(),now)
        empty=self.book(2,'orderbook.increment');empty.update(bids=[['100','0']],asks=[['101','0']])
        b.receive(empty,now)
        self.assertFalse(b.fresh(MARKET,now));self.assertIsNone(b.features(MARKET,now))
        b.receive(self.book(3,'orderbook.increment'),now)
        self.assertTrue(b.fresh(MARKET,now));self.assertEqual(b.seq[(MARKET,'book')],3)
    def test_crossed_book_still_rejected(self):
        b=Books();d=self.book();d['bids']=[['102','1']]
        with self.assertRaisesRegex(ValueError,'INVALID_BOOK'):b.receive(d,1800000000.)
    def test_quiet_book_snapshot_confirms_freshness_without_fills(self):
        b=Books();now=1800000000.;b.receive(self.book(),now)
        snapshot=self.book();snapshot['header']['created_at']=now+4
        self.assertEqual(b.receive(snapshot,now+4),[])
        self.assertTrue(b.fresh(MARKET,now+6))
        self.assertEqual(b.seq[(MARKET,'book')],1)
        b.receive(self.book(),now+8)
        self.assertEqual(b.books[MARKET]['source'],now+4)
        self.assertFalse(b.fresh(MARKET,now+10))
    def test_refresh_snapshot_cannot_hide_sequence_gap_or_changed_same_sequence(self):
        b=Books();now=1800000000.;b.receive(self.book(),now)
        with self.assertRaisesRegex(ValueError,'SEQUENCE_GAP'):b.receive(self.book(3),now)
        snapshot=self.book();snapshot['bids']=[['99','1']]
        with self.assertRaisesRegex(ValueError,'SNAPSHOT_SEQUENCE_MISMATCH'):b.receive(snapshot,now)
    def test_duplicate_traffic_does_not_postpone_quiet_book_refresh(self):
        now=1800000000.;r=BookRefresh();r.select({MARKET},now);r.observe(self.book(),now)
        # New transport headers on duplicate sequence numbers cannot keep an
        # old book alive or suppress the request for a verified snapshot.
        for offset in (1.,2.,3.):
            duplicate=self.book(1,'orderbook.increment');duplicate['header']['created_at']=now+offset
            r.observe(duplicate,now+offset)
        self.assertEqual(r.due(now+3),[MARKET])
        self.assertFalse(r.books.fresh(MARKET,now+6))
        snapshot=self.book();snapshot['header']['created_at']=now+3.5
        r.observe(snapshot,now+3.5)
        self.assertEqual(r.due(now+4),[])
        self.assertTrue(r.books.fresh(MARKET,now+6))
    def test_snapshot_timeout_retries_and_old_snapshot_does_not_refresh(self):
        now=1800000000.;r=BookRefresh();r.select({MARKET},now)
        self.assertEqual(r.due(now+1.9),[])
        self.assertEqual(r.due(now+2),[MARKET])
        self.assertEqual(r.due(now+2.5),[])
        self.assertEqual(r.due(now+3),[MARKET])
        r.observe(self.book(),now+3)
        self.assertEqual(r.due(now+4),[MARKET])
        self.assertFalse(r.books.fresh(MARKET,now+6))
    def test_refresh_reselection_drops_previous_sequence(self):
        now=1800000000.;r=BookRefresh();r.select({MARKET},now);r.observe(self.book(),now)
        r.select(set(),now+1);self.assertEqual(r.due(now+3),[])
        r.select({MARKET},now+4);r.observe(self.book(100),now+4)
        self.assertEqual(r.books.seq[(MARKET,'book')],100)
    def test_fresh_empty_book_requests_immediate_snapshot_but_never_trades(self):
        now=1800000000.;r=BookRefresh();r.select({MARKET},now);r.observe(self.book(),now)
        empty=self.book(2,'orderbook.increment');empty['asks']=[['101','0']]
        r.observe(empty,now+.1)
        self.assertEqual(r.books.issue(MARKET,now+.1),'EMPTY_ORDER_BOOK')
        self.assertFalse(r.books.fresh(MARKET,now+.1))
        self.assertEqual(r.due(now+.1),[MARKET]);self.assertEqual(r.due(now+.2),[])
        self.assertEqual(r.books.issue(MARKET,now+6),'STALE_MARKET_DATA')
        restored=self.book(3);restored['header']['created_at']=now+1
        r.observe(restored,now+1)
        self.assertTrue(r.books.fresh(MARKET,now+1));self.assertEqual(r.due(now+1),[])

class AgentTests(unittest.TestCase):
    def test_owner_model_is_shared_without_legacy_fallback(self):
        with patch.dict(os.environ,dict(WHY_DISCOVERY_MODEL_KEY='unit-test-only',WHY_LAB_LLM_MODEL='different-legacy-model'),clear=True):
            for role in ('strategy','news','direction'):
                model=Model();self.assertEqual(model.model,'gpt-5.6-luna')
                class Response:
                    def __enter__(self):return self
                    def __exit__(self,*a):pass
                    def read(self,*a):return b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{\\"ok\\":true}"}]}]}'
                with patch.object(model.opener,'open',return_value=Response()) as send:
                    self.assertEqual(model.ask(role,{},dict(properties=dict(ok=dict(type='boolean')))),dict(ok=True))
                    payload=json.loads(send.call_args.args[0].data)
                    self.assertEqual(payload['model'],'gpt-5.6-luna');self.assertFalse(payload['store'])
                    self.assertIn('span 5, 25 and 100 minutes',payload['input'][0]['content'])
                    self.assertIn('positive aggregate net P&L',payload['input'][0]['content'])
    def test_missing_model_does_not_fabricate_strategies(self):
        with patch.dict(os.environ,{},clear=True):
            m=Model();self.assertFalse(m.ready)
            with self.assertRaisesRegex(RuntimeError,'AI_NOT_CONFIGURED'):m.ask('strategy',{}, {})
    def test_no_context_delays(self):
        with self.assertRaisesRegex(ValueError,'NEWS_CALENDAR_NOT_CONFIGURED'):context('',MARKET,time.time())
    def test_calendar_rules(self):
        now=time.time();data=dict(markets={MARKET:dict(funding_shock=False,oi_shock=False,sentiment=0)},calendar=[dict(impact='high',time=now+600)])
        self.assertEqual(news_rules(data,MARKET,now)['verdict'],'delay');data['calendar']=[];self.assertEqual(news_rules(data,MARKET,now)['verdict'],'confirm')
        data['markets'][MARKET]['funding_shock']=True;self.assertEqual(news_rules(data,MARKET,now)['verdict'],'delay')
    def test_qualified_strategy_delays_without_news_and_never_live(self):
        e=Engine(Store(':memory:'));e.markets={MARKET:LIMITS};cid=e.propose('alice',SPEC,{})
        c=e.accounts['alice']['candidates'][cid];c['trades']=[dict(net=1,reason='TARGET') for _ in range(10)]
        with patch.dict(os.environ,{},clear=True):
            a=Agents(e,Model(),threading.Event());a.review('alice',cid)
        self.assertEqual(c['state'],'REVIEW_DELAYED');self.assertEqual(c['review']['reason'],'NEWS_CALENDAR_NOT_CONFIGURED')
    def test_model_incomplete_response_refused(self):
        with patch.dict(os.environ,dict(WHY_DISCOVERY_MODEL='configured-model',WHY_DISCOVERY_MODEL_KEY='test-only'),clear=True):
            m=Model()
            class Response:
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def read(self,*a):return b'{"status":"incomplete"}'
            with patch.object(m.opener,'open',return_value=Response()):
                with self.assertRaisesRegex(ValueError,'AI_INCOMPLETE'):m.ask('strategy',{},dict(properties={}))

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.e=Engine(Store(':memory:'));self.e.markets={MARKET:LIMITS};self.e.propose('alice',SPEC,{})
        class Verifier:
            def verify(self,token):
                if token not in ('alice','bob'):raise ValueError('invalid')
                return type('Identity',(),dict(user_id=token))()
        self.Handler=handler(self.e,Verifier())
    def request(self,path,token=None,body=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        # Exercise the real request handler without opening a network listener.
        raw=json.dumps(body).encode() if body is not None else b''
        headers['Content-Length']=str(len(raw))
        request=object.__new__(self.Handler)
        request.path=path;request.command='POST' if body is not None else 'GET';request.headers=headers;request.rfile=io.BytesIO(raw)
        result=[];request.reply=lambda status,value:result.append((status,value))
        request.process();return result[0]
    def test_auth_and_tenant_isolation(self):
        self.assertEqual(self.request('/state')[0],401);self.assertEqual(self.request('/state','invalid')[0],401)
        self.assertEqual(len(self.request('/state','alice')[1]['candidates']),1);self.assertEqual(self.request('/state','bob')[1]['candidates'],[])
        self.assertEqual(self.request('/start','bob',dict(user_id='alice'))[0],400)
    def test_paper_only_routes(self):
        self.assertEqual(self.request('/live','alice',{})[0],404)
        status,data=self.request('/start','alice',{});self.assertEqual(status,200);self.assertTrue(data['running']);self.assertFalse(data['live_enabled'])
    def test_archive_auth_and_account_isolation(self):
        from unittest.mock import patch
        c=next(iter(self.e.accounts['alice']['candidates'].values()));c['state']='EXPIRED'
        with patch('discovery.engine.ACTIVE_VERSION_LIMIT',1):self.e.archive_completed('alice')
        self.e.commit()
        self.assertEqual(self.request('/archive')[0],401)
        self.assertEqual(len(self.request('/archive','alice')[1]['items']),1)
        self.assertEqual(self.request('/archive','bob')[1]['items'],[])
        self.assertEqual(self.request('/archive?offset=1','alice')[1]['items'],[])
        self.assertEqual(self.request('/archive?offset=-1','alice')[0],400)
    def test_health_exposes_feed_freshness_without_account_data(self):
        status,data=self.request('/health');self.assertEqual(status,200)
        self.assertEqual(data['feed']['fresh_markets'],0)
        self.assertNotIn('candidates',data['feed']);self.assertNotIn('accounts',data['feed'])

if __name__=='__main__':unittest.main()
