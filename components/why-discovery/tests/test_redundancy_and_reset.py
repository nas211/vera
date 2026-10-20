import copy
import queue
import threading
import time
import unittest
from unittest.mock import patch
from discovery.market import FeedMerge, PublicFeed
from discovery.engine import Engine
from discovery.store import Store
from test_discovery import Clock, MARKET, LIMITS, SPEC
from test_feed_recovery import book

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.out=[];self.recovered=[]
        self.m=FeedMerge(self.out.append,self.recovered.append,self.clock)
    def data(self,source,d):self.m.receive(source,('data',d,self.clock.t))
    def increment(self,seq):
        d=book(self.clock.t,seq);d['header']['type']='orderbook.increment';return d
    def test_duplicate_streams_do_not_duplicate_book_or_trade_updates(self):
        self.data(0,book(self.clock.t));self.data(1,book(self.clock.t))
        trade=dict(market=MARKET,header=dict(type='trades.snapshot'),sequence_num=1,trades=[])
        self.data(0,trade);self.data(1,trade)
        trade=copy.deepcopy(trade);trade['header']['type']='trades.increment';trade['sequence_num']=2
        trade['trades']=[dict(id='one',price='100',amount='1',direction='sell',executed_at=self.clock.t)]
        self.data(0,trade);self.data(1,trade)
        self.assertEqual(len([d for kind,d,at in self.out if kind=='data']),3)
        self.assertEqual(len(self.m.trade_seen),1)
    def test_trade_sequence_numbers_are_local_to_each_connection(self):
        def snapshot(seq):return dict(market=MARKET,header=dict(type='trades.snapshot'),sequence_num=seq,trades=[])
        self.data(0,snapshot(75779));self.data(1,snapshot(75803))
        def increment(seq,tid):return dict(market=MARKET,header=dict(type='trades.increment'),sequence_num=seq,
            trades=[dict(id=tid,price='100',amount='1',direction='sell',executed_at=self.clock.t)])
        self.data(0,increment(75780,'one'));self.data(1,increment(75804,'one'))
        self.data(1,increment(75805,'two'));self.data(0,increment(75781,'two'))
        data=[d for kind,d,at in self.out if kind=='data']
        self.assertEqual([d['sequence_num'] for d in data],[0,1,2])
        self.assertEqual([t['id'] for d in data for t in d['trades']],['one','two'])
        self.assertFalse(self.recovered)
    def test_actual_trade_gap_on_both_sources_still_invalidates(self):
        self.data(0,dict(market=MARKET,header=dict(type='trades.snapshot'),sequence_num=10,trades=[]))
        self.data(0,dict(market=MARKET,header=dict(type='trades.increment'),sequence_num=12,trades=[]))
        self.assertTrue(any(kind=='market_gap' for kind,d,at in self.out))
    def test_one_dead_connection_does_not_reset_healthy_stream(self):
        self.data(0,book(self.clock.t));self.data(1,book(self.clock.t))
        self.m.receive(0,('gap','PUBLIC_FEED_UNAVAILABLE',self.clock.t))
        self.clock.t+=1;self.data(1,self.increment(2))
        self.assertTrue(self.m.books.fresh(MARKET,self.clock.t))
        self.assertFalse(any(kind in ('gap','market_gap') for kind,d,at in self.out))
    def test_both_failed_connections_fail_closed(self):
        self.data(0,book(self.clock.t));self.data(1,book(self.clock.t))
        for source in (0,1):self.m.receive(source,('gap','PUBLIC_FEED_UNAVAILABLE',self.clock.t))
        self.assertEqual(len([d for kind,d,at in self.out if kind=='gap']),1)
        self.assertFalse(self.m.books.fresh(MARKET,self.clock.t))
        self.clock.t+=1;self.m.receive(2,('confirmation',book(self.clock.t),self.clock.t))
        self.assertFalse(self.m.books.fresh(MARKET,self.clock.t))
        self.assertFalse(self.m.live)
    def test_out_of_order_arrivals_wait_for_missing_update(self):
        self.data(0,book(self.clock.t));self.data(1,self.increment(3))
        self.assertEqual(self.m.books.seq[(MARKET,'book')],1)
        self.data(0,self.increment(2))
        self.assertEqual(self.m.books.seq[(MARKET,'book')],3)
        self.assertEqual([d['sequence_num'] for kind,d,at in self.out if kind=='data'],[1,2,3])
    def test_missing_update_on_both_streams_is_not_hidden_by_new_snapshot(self):
        self.data(0,book(self.clock.t));self.data(1,book(self.clock.t,3))
        self.assertEqual(self.m.books.seq[(MARKET,'book')],1)
        self.data(1,self.increment(4));self.clock.t+=1.1;self.m.tick()
        self.assertEqual(self.recovered,[MARKET])
        self.assertTrue(any(kind=='market_gap' for kind,d,at in self.out))
        self.assertFalse(self.m.books.fresh(MARKET,self.clock.t))
    def test_source_local_market_failure_is_covered_by_other_stream(self):
        self.data(0,book(self.clock.t));self.data(1,book(self.clock.t))
        self.m.receive(0,('market_gap',dict(market=MARKET,reason='GAP'),self.clock.t))
        self.data(1,self.increment(2))
        self.assertFalse(any(kind=='market_gap' for kind,d,at in self.out))
    def test_older_funding_from_backup_cannot_replace_current_funding(self):
        def funding(at):return dict(market=MARKET,header=dict(type='mark_price',created_at=at),funding_rate=.001,next_funding_time=at+3600)
        self.data(0,funding(self.clock.t));self.data(1,funding(self.clock.t-10))
        self.assertEqual(self.m.books.marks[MARKET]['source'],self.clock.t)

class ResultResetTests(unittest.TestCase):
    def test_clear_feed_results_from_page_preserves_ledger_and_risk_totals(self):
        clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS}
        cid=e.propose('alice',SPEC,{});c=e.accounts['alice']['candidates'][cid]
        c['trades']=[dict(id='stale',net=-8,valid=False,reason='MAX_HOLD',invalid_reason='STALE_MARKET_DATA',closed=clock.t),
                     dict(id='empty',net=2,valid=False,reason='EMPTY_ORDER_BOOK',invalid_reason='EMPTY_ORDER_BOOK',closed=clock.t),
                     dict(id='good',net=-1,valid=True,reason='STOP',invalid_reason='',closed=clock.t),
                     dict(id='owner',net=-.5,valid=False,reason='OWNER_CLOSE',invalid_reason='OWNER_CLOSE',closed=clock.t)]
        before=copy.deepcopy(c['trades']);e.dirty=True;e.commit();state=e.state('alice')
        self.assertEqual(state['realized'],-1.5);self.assertEqual(state['equity'],24998.5)
        self.assertEqual(state['feed_results_archived'],2)
        self.assertEqual([t['id'] for t in state['candidates'][0]['trades']],['good','owner'])
        self.assertEqual(state['candidates'][0]['score']['closed'],1)
        self.assertEqual(c['trades'],before)
        self.assertEqual(e.store.load()['alice']['candidates'][cid]['trades'],before)
        self.assertEqual(e.daily(e.accounts['alice'],clock.t),-7.5)
        self.assertEqual(e.state('bob')['feed_results_archived'],0)

class QuietFailoverTests(unittest.TestCase):
    def test_quiet_books_keep_refreshing_while_first_stream_is_down(self):
        out=queue.Queue();feed=PublicFeed(out);both_started=threading.Event()
        class Stream:
            def __init__(self,sink,pinned,refresh_enabled,family=None):
                self.sink=sink;self.reconnect=threading.Event()
                assert not refresh_enabled
            def recover(self,m):raise AssertionError('Unexpected market recovery')
            def run(self):
                self.sink.put(('data',book(time.time()),time.time()),timeout=1)
                if self.sink.source==1:both_started.set()
                else:
                    both_started.wait(1)
                    self.sink.put(('gap','PUBLIC_FEED_UNAVAILABLE',time.time()),timeout=1)
        class Confirmations:
            def __init__(self,stop,family=None):self.results=queue.Queue()
            def start(self):pass
            def request(self,m):self.results.put((book(time.time()),time.time()))
        e=Engine(Store(':memory:'));stale=[];gaps=[];started=time.monotonic()
        with patch('discovery.market.StreamFeed',Stream),patch('discovery.market.SnapshotFeed',Confirmations):
            thread=threading.Thread(target=feed.run,daemon=True);thread.start()
            try:
                while time.monotonic()-started<6.2:
                    try:
                        kind,d,at=out.get(timeout=.1)
                        if kind in ('gap','market_gap'):gaps.append(kind)
                        e.receive(kind,d,at)
                    except queue.Empty:pass
                    if MARKET in e.books.books and not e.books.fresh(MARKET,time.time()):stale.append(time.time())
            finally:feed.stopping.set();thread.join(2)
        self.assertEqual(gaps,[]);self.assertEqual(stale,[])
        self.assertGreaterEqual(e.confirmed_snapshots,2)

if __name__=='__main__':unittest.main()

class StallWatchdogTests(unittest.TestCase):
    def test_health_does_not_call_silent_source_connected(self):
        clock=Clock();out=[];merge=FeedMerge(out.append,lambda m:None,clock)
        merge.receive(0,('data',book(clock.t),clock.t))
        clock.t+=6;merge.tick()
        status=[d for kind,d,at in out if kind=='transport_status'][-1]
        self.assertFalse(status['streams']['0']['connected'])
        self.assertEqual(status['streams']['0']['last_data_age'],6)

    def test_silent_continuous_transport_reconnects_without_local_refresh(self):
        import json,types
        from discovery.market import StreamFeed
        clock=Clock();start=clock.t;out=queue.Queue();feed=StreamFeed(out,refresh_enabled=False)
        class Stop:
            stopped=False
            def is_set(self):return self.stopped or clock.t-start>12
            def wait(self,seconds):
                clock.t+=seconds
                if seconds==5:self.stopped=True
        class Socket:
            handshake=False;closed=False
            def send(self,raw):pass
            def settimeout(self,value):pass
            def recv(self):
                if not self.handshake:
                    self.handshake=True;return json.dumps(dict(id=1,connect={}))
                clock.t+=.5;raise TimeoutError()
            def close(self):self.closed=True
        ws=Socket();feed.stopping=Stop()
        module=types.SimpleNamespace(create_connection=lambda *a,**k:ws,WebSocketTimeoutException=TimeoutError)
        with patch.dict('sys.modules',websocket=module),patch('discovery.market.get',return_value={}),patch('discovery.market.universe',return_value={MARKET:LIMITS}),patch('discovery.market.time.time',clock):
            feed.run()
        self.assertTrue(ws.closed)
        self.assertIn(('gap','PUBLIC_FEED_STALLED'),[(kind,d) for kind,d,at in out.queue])

    def test_missing_merged_confirmations_recover_stale_market(self):
        clock=Clock();out=queue.Queue();feed=PublicFeed(out);recovered=[];seeded=threading.Event()
        class Stream:
            def __init__(self,sink,pinned,refresh_enabled,family=None):self.sink=sink;self.reconnect=threading.Event()
            def run(self):
                self.sink.put(('data',book(clock.t),clock.t),timeout=1);seeded.set()
            def recover(self,m):
                recovered.append((self.sink.source,m))
                if len(recovered)>=2:feed.stopping.set()
        class MissingSnapshots:
            def __init__(self,stop,family=None):self.results=queue.Queue()
            def start(self):pass
            def request(self,m):pass
        with patch('discovery.market.StreamFeed',Stream),patch('discovery.market.SnapshotFeed',MissingSnapshots),patch('discovery.market.time.time',clock):
            thread=threading.Thread(target=feed.run,daemon=True);thread.start()
            try:
                self.assertTrue(seeded.wait(1));time.sleep(.15)
                clock.t+=3;time.sleep(.15);clock.t+=3
                thread.join(1)
                self.assertEqual(set(recovered),{(0,MARKET),(1,MARKET)})
                self.assertTrue(any(kind=='market_gap' and d['reason']=='PUBLIC_MARKET_STALLED' for kind,d,at in out.queue))
            finally:feed.stopping.set();thread.join(1)
