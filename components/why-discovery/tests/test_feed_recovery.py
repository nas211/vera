import json
import queue
import threading
import types
import unittest
from unittest.mock import patch

from discovery.market import Books, BookRefresh, CandleFeed, StreamFeed as PublicFeed
from discovery.engine import Engine
from discovery.store import Store
from test_discovery import Clock, MARKET, LIMITS, SPEC


def book(now, seq=1):
    return dict(market=MARKET, header=dict(type='orderbook.snapshot', created_at=now),
                sequence_num=seq, bids=[['100','1']], asks=[['101','1']])


class RecoveryTests(unittest.TestCase):
    def test_unanswered_snapshot_reconnects_despite_duplicate_traffic(self):
        now=1800000000.;refresh=BookRefresh();refresh.select({MARKET},now)
        refresh.observe(book(now),now)
        self.assertFalse(refresh.stalled(now+2))
        self.assertEqual(refresh.due(now+2),[MARKET])
        for offset in (3,4,5):
            duplicate=book(now+offset);duplicate['header']['type']='orderbook.increment'
            refresh.observe(duplicate,now+offset)
        self.assertTrue(refresh.stalled(now+5.1))
        self.assertFalse(refresh.books.fresh(MARKET,now+5.1))

    def test_confirmed_snapshot_prevents_reconnect_and_has_no_fills(self):
        now=1800000000.;r=BookRefresh();r.select({MARKET},now);r.observe(book(now),now)
        r.due(now+2);r.observe(book(now+3),now+3)
        self.assertFalse(r.stalled(now+5.1));self.assertTrue(r.books.fresh(MARKET,now+5.1))
        self.assertEqual(r.books.receive(book(now+4),now+4),[])

    def test_missing_initial_snapshot_and_dropped_market(self):
        now=1800000000.;r=BookRefresh();r.select({MARKET},now);r.due(now+2)
        self.assertTrue(r.stalled(now+6))
        r.select(set(),now+6);self.assertFalse(r.stalled(now+7))

    def test_transport_closes_stalled_socket_before_retry(self):
        clock=Clock();out=queue.Queue();feed=PublicFeed(out)
        class Stop:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,seconds):
                clock.t+=seconds
                if seconds==5:
                    assert ws.closed
                    self.stopped=True
        class Socket:
            closed=False
            handshake=False
            def send(self,raw):pass
            def settimeout(self,value):pass
            def recv(self):
                if not self.handshake:
                    self.handshake=True
                    return json.dumps(dict(id=1,connect={}))
                clock.t+=.5
                raise TimeoutError()
            def close(self):self.closed=True
        ws=Socket();feed.stopping=Stop()
        snapshots=types.SimpleNamespace(start=lambda:None,request=lambda m:None,results=queue.Queue())
        module=types.SimpleNamespace(create_connection=lambda *a,**k:ws,WebSocketTimeoutException=TimeoutError)
        with patch.dict('sys.modules',websocket=module), patch('discovery.market.get',return_value={}), \
             patch('discovery.market.universe',return_value={MARKET:LIMITS}), patch('discovery.market.time.time',clock), \
             patch('discovery.market.SnapshotFeed',return_value=snapshots):
            feed.run()
        events=list(out.queue)
        self.assertEqual(events[-1][0:2],('gap','PUBLIC_FEED_STALLED'))
        self.assertTrue(ws.closed)

    def test_queue_delay_retains_actual_arrival_time_and_blocks_entries(self):
        clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS}
        e.action('alice','start');cid=e.propose('alice',SPEC,{})
        arrived=clock.t;clock.t+=6
        # A later source timestamp must not hide how long this message sat queued.
        e.receive('data',book(clock.t),arrived)
        self.assertEqual(e.books.books[MARKET]['received'],arrived)
        self.assertFalse(e.books.fresh(MARKET,clock.t))
        self.assertIsNone(e.accounts['alice']['candidates'][cid]['entry'])
        self.assertEqual(e.feed_status()['last_event_age'],6)

    def test_transport_gap_is_visible_in_health(self):
        e=Engine(Store(':memory:'));e.receive('gap','PUBLIC_FEED_STALLED',e.clock())
        self.assertEqual(e.feed_status()['last_gap']['error'],'PUBLIC_FEED_STALLED')


class CandleTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.out=queue.Queue();self.selected={MARKET}
        self.feed=CandleFeed(self.out,lambda:self.selected,threading.Event(),self.clock)
    def rows(self):
        end=int(self.clock.t//300)*300
        return [[(end-300*i)*1000,100,102,99,101,10] for i in range(25,0,-1)]
    def test_refreshes_on_candle_boundary_without_any_model_call(self):
        with patch('discovery.market.get',side_effect=lambda *a,**k:self.rows()) as get:
            self.feed.once();self.assertEqual(get.call_count,1)
            first=self.out.get();b=Books();b.seed(*first[1],first[2])
            self.clock.t+=299;self.feed.once();self.assertEqual(get.call_count,1)
            self.clock.t+=2;self.feed.once();self.assertEqual(get.call_count,2)
            second=self.out.get();b.seed(*second[1],second[2])
            b.receive(book(self.clock.t),self.clock.t)
            self.assertIn('return_20',b.features(MARKET,self.clock.t))
            self.assertEqual(b.bars[MARKET][-1][0],self.clock.t//300*300-300)
    def test_stale_response_retries_and_does_not_create_fresh_features(self):
        stale=self.rows();self.clock.t+=900
        with patch('discovery.market.get',side_effect=[stale,self.rows()]) as get:
            self.feed.once();event=self.out.get();b=Books();b.seed(*event[1],event[2])
            b.receive(book(self.clock.t),self.clock.t)
            self.assertNotIn('return_20',b.features(MARKET,self.clock.t))
            self.clock.t+=1;self.feed.once();self.assertEqual(get.call_count,1)
            self.clock.t+=14;self.feed.once();self.assertEqual(get.call_count,2)
    def test_provider_failure_does_not_starve_other_markets_or_fabricate_bars(self):
        self.selected.add('ETHUSD-PERP')
        with patch('discovery.market.get',side_effect=[OSError('unavailable'),self.rows()]) as get:
            with self.assertRaises(OSError):self.feed.once()
            self.assertTrue(self.out.empty());self.clock.t+=1;self.feed.once()
            self.assertEqual(get.call_args.args[1]['symbol'],'ETHUSD-PERP')
            self.assertEqual(self.out.get()[1][0],'ETHUSD-PERP')
    def test_no_active_markets_means_no_requests(self):
        self.selected.clear()
        with patch('discovery.market.get') as get:self.feed.once();get.assert_not_called()


if __name__=='__main__':unittest.main()
