import copy
import json
import queue
import types
import unittest
from unittest.mock import patch

from discovery.market import Books, StreamFeed as PublicFeed, SnapshotFeed
from discovery.engine import Engine
from discovery.store import Store
from test_discovery import Clock, MARKET, LIMITS, SPEC
from test_feed_recovery import book


class ConfirmationTests(unittest.TestCase):
    def test_captured_refresh_race_keeps_primary_sequence_intact(self):
        now=1800000000.;b=Books();b.receive(book(now,7009043),now)
        # Captured TAO race: the snapshot was two sequence numbers ahead while
        # unsubscribe/subscribe had removed update 7009044 from the old stream.
        snapshot=book(now+2,7009045)
        self.assertFalse(b.confirm(snapshot,now+2))
        self.assertEqual(b.seq[(MARKET,'book')],7009043)
        for seq in (7009044,7009045):
            d=book(now+1,seq);d['header']['type']='orderbook.increment'
            self.assertEqual(b.receive(d,now+2),[])
        self.assertTrue(b.confirm(snapshot,now+2))
        self.assertTrue(b.fresh(MARKET,now+6))
        self.assertEqual(b.seq[(MARKET,'book')],7009045)

    def test_confirmation_cannot_change_depth_or_hide_gap_or_seed_book(self):
        now=1800000000.;b=Books()
        self.assertFalse(b.confirm(book(now),now));b.receive(book(now),now)
        for seq in (0,2,3):self.assertFalse(b.confirm(book(now+1,seq),now+1))
        changed=book(now+1);changed['bids']=[['99','2']]
        self.assertFalse(b.confirm(changed,now+1))
        self.assertEqual(b.books[MARKET]['source'],now)
        with self.assertRaisesRegex(ValueError,'SEQUENCE_GAP'):
            gap=book(now+1,3);gap['header']['type']='orderbook.increment';b.receive(gap,now+1)

    def test_old_or_future_confirmation_cannot_extend_freshness(self):
        now=1800000000.;b=Books();b.receive(book(now),now)
        self.assertFalse(b.confirm(book(now-1),now+1))
        self.assertFalse(b.confirm(book(now+1),now+7))
        self.assertFalse(b.confirm(book(now+3),now+1))
        self.assertFalse(b.fresh(MARKET,now+6))

    def test_market_recovery_leaves_other_positions_and_entries_intact(self):
        clock=Clock();e=Engine(Store(':memory:'),clock);other='ETHUSD-PERP'
        e.markets={MARKET:LIMITS,other:LIMITS};e.action('alice','start')
        ids=[e.propose('alice',dict(SPEC,market=m),{}) for m in (MARKET,other)]
        for m in (MARKET,other):
            d=book(clock.t);d['market']=m;e.books.receive(d,clock.t)
            e.books.marks[m]=dict(source=clock.t,rate=0.,next=clock.t+28800)
        e.tick();clock.t+=.3
        for cid in ids:
            c=e.accounts['alice']['candidates'][cid]
            e.maker_trade('alice',c,dict(side='sell',price=100,qty=100,ts=clock.t,id=cid),clock.t)
        e.tick();unaffected=copy.deepcopy(e.accounts['alice']['candidates'][ids[1]])
        e.receive('market_gap',dict(market=MARKET,reason='PUBLIC_MARKET_STALLED'),clock.t)
        self.assertEqual(e.accounts['alice']['candidates'][ids[0]]['position']['feed_gap']['reason'],'PUBLIC_MARKET_STALLED')
        self.assertEqual(e.accounts['alice']['candidates'][ids[1]],unaffected)
        self.assertTrue(e.books.fresh(other,clock.t))
        self.assertEqual(e.feed_status()['market_recoveries'],1)

    def test_engine_does_not_fill_from_confirmation(self):
        clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS}
        e.action('alice','start');cid=e.propose('alice',SPEC,{})
        e.books.receive(book(clock.t),clock.t)
        e.books.marks[MARKET]=dict(source=clock.t,rate=0.,next=clock.t+28800)
        e.tick();clock.t+=3
        e.receive('confirmation',book(clock.t),clock.t)
        self.assertIsNone(e.accounts['alice']['candidates'][cid]['position'])
        self.assertEqual(e.feed_status()['confirmed_snapshots'],1)


class PrimaryConnectionTests(unittest.TestCase):
    def test_sequence_failure_resubscribes_only_affected_market(self):
        clock=Clock();out=queue.Queue();feed=PublicFeed(out);sent=[];other='ETHUSD-PERP'
        initial=book(clock.t);second=book(clock.t);second['market']=other
        gap=book(clock.t,3);gap['header']['type']='orderbook.increment'
        recovered=book(clock.t,100);other_update=book(clock.t,2)
        other_update['market']=other;other_update['header']['type']='orderbook.increment'
        messages=[dict(id=1,connect={})]+[dict(push=dict(pub=dict(data=d))) for d in (initial,second,gap,recovered,other_update)]
        class Stop:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,seconds):clock.t+=seconds
        stop=Stop();feed.stopping=stop
        class Socket:
            def send(self,raw):sent.append(json.loads(raw))
            def settimeout(self,value):pass
            def close(self):pass
            def recv(self):
                msg=messages.pop(0)
                if not messages:stop.stopped=True
                return json.dumps(msg)
        snapshots=types.SimpleNamespace(start=lambda:None,request=lambda m:None,results=queue.Queue())
        module=types.SimpleNamespace(create_connection=lambda *a,**k:Socket(),WebSocketTimeoutException=TimeoutError)
        with patch.dict('sys.modules',websocket=module),patch('discovery.market.SnapshotFeed',return_value=snapshots), \
             patch('discovery.market.get',return_value={}),patch('discovery.market.universe',return_value={MARKET:LIMITS,other:LIMITS}), \
             patch('discovery.market.time.time',clock):feed.run()
        events=list(out.queue)
        self.assertFalse(any(kind=='gap' for kind,d,at in events))
        self.assertEqual([d['market'] for kind,d,at in events if kind=='market_gap'],[MARKET])
        unsubs=[d['unsubscribe']['channel'] for d in sent if 'unsubscribe' in d]
        self.assertEqual(len(unsubs),3);self.assertTrue(all(channel.endswith(MARKET) for channel in unsubs))
        self.assertTrue(any(kind=='data' and d==other_update for kind,d,at in events))

    def test_snapshot_socket_forwards_only_requested_snapshot(self):
        class Stop:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,seconds):pass
        stop=Stop();worker=SnapshotFeed(stop);worker.request(MARKET);sent=[]
        snapshot=book(1800000000.)
        publication=dict(push=dict(pub=dict(data=dict(header=dict(type='trades.increment'),market=MARKET))))
        messages=[dict(id=1,connect={}),publication,dict(id=2,subscribe=dict(data=snapshot))]
        class Socket:
            def send(self,raw):sent.append(json.loads(raw))
            def settimeout(self,value):pass
            def close(self):pass
            def recv(self):
                msg=messages.pop(0)
                if not messages:stop.stopped=True
                return json.dumps(msg)
        module=types.SimpleNamespace(create_connection=lambda *a,**k:Socket(),WebSocketTimeoutException=TimeoutError)
        with patch.dict('sys.modules',websocket=module):worker.run()
        self.assertEqual(worker.results.qsize(),1);self.assertEqual(worker.results.get()[0],snapshot)
        self.assertEqual(worker.pending,set())
        self.assertEqual(sent[-1]['unsubscribe']['channel'],'public.orderbook.increment.'+MARKET)

    def test_quiet_refresh_never_unsubscribes_primary_connection(self):
        clock=Clock();start=clock.t;out=queue.Queue();feed=PublicFeed(out);sent=[]
        class Stop:
            def is_set(self):return clock.t-start>=12
            def wait(self,seconds):clock.t+=seconds
        class Socket:
            calls=0
            def send(self,raw):sent.append(json.loads(raw))
            def settimeout(self,value):pass
            def close(self):pass
            def recv(self):
                self.calls+=1
                if self.calls==1:return json.dumps(dict(id=1,connect={}))
                if self.calls==2:return json.dumps(dict(subscribe=dict(data=book(clock.t))))
                clock.t+=.25;raise TimeoutError()
        class Confirmations:
            def __init__(self):self.results=queue.Queue();self.requested=[]
            def start(self):pass
            def request(self,m):
                self.requested.append(m);self.results.put((book(clock.t),clock.t))
        snapshots=Confirmations();feed.stopping=Stop()
        module=types.SimpleNamespace(create_connection=lambda *a,**k:Socket(),WebSocketTimeoutException=TimeoutError)
        with patch.dict('sys.modules',websocket=module),patch('discovery.market.SnapshotFeed',return_value=snapshots), \
             patch('discovery.market.get',return_value={}),patch('discovery.market.universe',return_value={MARKET:LIMITS}), \
             patch('discovery.market.time.time',clock):feed.run()
        self.assertGreaterEqual(len(snapshots.requested),3)
        self.assertFalse(any('unsubscribe' in d for d in sent))
        self.assertEqual(sum('subscribe' in d for d in sent),3)
        self.assertFalse(any(kind in ('gap','market_gap') for kind,d,at in list(out.queue)))

    def test_confirmation_queue_deduplicates_pending_requests(self):
        feed=SnapshotFeed(types.SimpleNamespace())
        for _ in range(100):feed.request(MARKET)
        self.assertEqual(feed.requests.qsize(),1)
        self.assertEqual(feed.requests.get(),MARKET);feed.release(MARKET);feed.request(MARKET)
        self.assertEqual(feed.requests.qsize(),1)


if __name__=='__main__':unittest.main()
