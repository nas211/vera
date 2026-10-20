"""Regression coverage for missing confirmations and stalled heartbeat reads."""
import json
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from customer.feed import YellowMarketFeed
from customer.readiness import Readiness
from execution.market_data import FreshnessMonitor
from tests.customer import test_live_entry_filter as fixtures

class RefreshRecoveryTests(unittest.TestCase):
    def fixture(self, direction='long'):
        f=fixtures.FilterTests();f.setUp();f.seed(direction);f.gate.offer(f.sig(direction));return f

    def test_rest_and_websocket_confirm_same_long_and_short_once(self):
        for direction in ('long','short'):
            for transport in ('rest','websocket'):
                with self.subTest(direction=direction,transport=transport):
                    f=self.fixture(direction);old=list(f.gate.bars[(f.market,'5m')])
                    f.recover(direction)
                    if transport=='rest':
                        newest=f.gate.bars[(f.market,'5m')][-1]
                        f.gate.bars[(f.market,'5m')]=old
                        self.assertTrue(f.gate.seed(f.market,'5m',old+[newest]))
                    self.assertEqual(len(f.gate.ready(f.fresh)),1)
                    self.assertEqual(f.gate.ready(f.fresh),[])

    def test_identical_history_refresh_keeps_pending(self):
        f=self.fixture()
        for interval in ('5m','1h'):
            self.assertTrue(f.gate.seed(f.market,interval,list(f.gate.bars[(f.market,interval)])))
        self.assertTrue(f.gate.pending)

    def test_revised_evidence_invalidates_pending(self):
        f=self.fixture();rows=[list(r) for r in f.gate.bars[(f.market,'5m')]]
        rows[-1][4]+=1
        self.assertTrue(f.gate.seed(f.market,'5m',rows))
        self.assertFalse(f.gate.pending)
        self.assertEqual(f.gate.last_decision[f.market],'candle_history_replaced')

    def test_stale_gap_invalid_and_nonoverlapping_histories_cancel(self):
        for bad in ('stale','gap','invalid','nonoverlap'):
            with self.subTest(bad=bad):
                f=self.fixture();rows=[list(r) for r in f.gate.bars[(f.market,'5m')]]
                if bad=='stale':f.clock.advance(300)
                if bad=='gap':del rows[-2]
                if bad=='invalid':rows[-1][4]=float('nan')
                if bad=='nonoverlap':
                    f.clock.advance(300*301);rows=fixtures.history(f.clock(),'5m')
                f.gate.seed(f.market,'5m',rows)
                self.assertFalse(f.gate.pending)

    def test_disconnect_is_fail_closed_and_reports_cancellation(self):
        f=self.fixture();f.gate.disconnect()
        self.assertFalse(f.gate.pending);self.assertFalse(f.gate.bars)
        self.assertEqual(f.gate.status()['decisions'][f.market],'feed_disconnected')
        self.assertEqual(f.gate.status()['decision_counts']['feed_disconnected'],1)

class SocketReadTests(unittest.TestCase):
    def fixture(self):
        self.now=1000.
        fresh=FreshnessMonitor(clock=lambda:self.now)
        return YellowMarketFeed(['BTC'],fresh,Mock(),clock=lambda:self.now)

    def receive(self,feed,messages,maxsize=100):
        sent=[];it=iter(messages)
        ws=SimpleNamespace(recv=lambda:next(it,''),send=sent.append)
        inbox=queue.Queue(maxsize=maxsize);ended=threading.Event();errors=[]
        feed._receive(ws,inbox,ended,errors,{2:'public.mark_price.BTC'})
        return inbox,sent,errors

    def test_heartbeat_processed_without_draining_business_queue(self):
        feed=self.fixture()
        publication=json.dumps({'push':{'channel':'public.mark_price.BTC','pub':{'data':{'mark_price':'100'}}}})
        inbox,sent,errors=self.receive(feed,[publication+'\n{}',publication])
        self.assertEqual(sent,['{}']);self.assertEqual(inbox.qsize(),2)
        feed.registry.on_mark.assert_not_called()
        self.assertEqual(inbox.get()[1],1000.)

    def test_subscription_errors_fail_closed(self):
        feed=self.fixture()
        _,_,errors=self.receive(feed,[json.dumps({'id':2,'error':{'code':103}})])
        self.assertIn('protocol_error',str(errors[0]))

    def test_subscription_ack_is_verified(self):
        feed=self.fixture();self.receive(feed,[json.dumps({'id':2,'subscribe':{}})])
        self.assertEqual(feed.subscriptions,{'public.mark_price.BTC'})

    def test_overflow_is_an_error_not_silent_data_loss(self):
        feed=self.fixture()
        pub=json.dumps({'push':{'channel':'test'}})
        _,_,errors=self.receive(feed,[pub,pub],1)
        self.assertIsInstance(errors[0],queue.Full)

    def test_received_timestamp_not_processing_time_controls_mark_age(self):
        feed=self.fixture();self.now+=40
        feed._handle({'push':{'channel':'public.mark_price.BTC','pub':{'data':{'mark_price':'100'}}}},1000.)
        self.assertFalse(feed.fresh.mark_fresh('BTC'))
        feed.registry.on_mark.assert_called_once_with('BTC',100.,1000.)

    def test_invalid_prices_never_reach_execution(self):
        feed=self.fixture()
        for px in ('nan','inf','-1','0'):
            feed._handle({'push':{'channel':'public.mark_price.BTC','pub':{'data':{'mark_price':px}}}})
        feed.registry.on_mark.assert_not_called()

    def test_connected_but_unsubscribed_or_stalled_is_not_ready(self):
        feed=self.fixture();feed.connected=True;feed.last_msg=self.now
        self.assertFalse(feed.status()['ready'])
        feed.subscriptions={'a','b','c'};feed.last_processed_at=self.now
        feed.fresh.seed('BTC',[0,300000,600000]);feed.fresh.on_mark('BTC',100)
        self.assertTrue(feed.status()['ready'])
        self.now+=31
        self.assertFalse(feed.status()['ready'])

    def test_readiness_does_not_claim_connected_feed_is_usable(self):
        cfg=SimpleNamespace(supabase_configured=False)
        feed=self.fixture();feed.connected=True
        health=Readiness(cfg,feed=feed,yellow_probe=lambda:True).snapshot()
        self.assertFalse(health['YELLOW_CONNECTED'])
        self.assertFalse(health['EXECUTION_READY'])

class AuthenticationReadinessTests(unittest.TestCase):
    def test_provider_402_is_distinct_from_database_health(self):
        cfg=SimpleNamespace(supabase_configured=True,supabase_url='https://example.invalid',supabase_anon_key='public')
        readiness=Readiness(cfg)
        with patch('httpx.get',return_value=SimpleNamespace(status_code=402)):
            self.assertEqual(readiness._default_auth_probe(),(False,'provider_quota_or_billing_restriction'))

    def test_auth_outage_does_not_claim_existing_execution_has_stopped(self):
        from contextlib import contextmanager
        @contextmanager
        def service():
            yield SimpleNamespace(execute=lambda *_:SimpleNamespace(fetchone=lambda:{'n':1}))
        cfg=SimpleNamespace(supabase_configured=True)
        db=SimpleNamespace(ping=lambda:(True,'ok'),service=service)
        feed=SimpleNamespace(status=lambda:{'connected':True,'ready':True})
        r=Readiness(cfg,db=db,feed=feed,runner_alive=lambda:True,yellow_probe=lambda:True,
                    auth_probe=lambda:(False,'provider_quota_or_billing_restriction'))
        with patch('customer.readiness.Vault.health',return_value=(True,'ok')), patch.dict('os.environ',{'YELLOW_ARMED':'1'}):
            h=r.snapshot()
        self.assertTrue(h['EXECUTION_READY']);self.assertTrue(h['SUPABASE_HEALTHY'])
        self.assertFalse(h['CUSTOMER_ACCESS_READY']);self.assertFalse(h['ready'])

class FlatMarkDispatchTests(unittest.TestCase):
    def tenant(self,venue_stops=True,positions=()):
        from customer.tenants import TenantEngine
        t=object.__new__(TenantEngine)
        t.transport=SimpleNamespace(capabilities=SimpleNamespace(venue_stop_orders=venue_stops))
        t.open_positions=Mock(return_value=list(positions))
        t.eng=SimpleNamespace(fresh=FreshnessMonitor(clock=lambda:1000.),on_mark=Mock(return_value=['normal-path']))
        return t
    def test_flat_venue_account_updates_marks_without_engine_wait(self):
        t=self.tenant()
        self.assertEqual(t.on_mark('BTC',100,1000.),[])
        self.assertTrue(t.eng.fresh.mark_fresh('BTC'))
        t.eng.on_mark.assert_not_called()
    def test_software_venue_and_existing_positions_keep_engine_stop_path(self):
        for venue_stops,positions in [(False,()),(True,({'state':'PROTECTED'},)),(True,({'state':'PROTECTION_PENDING'},))]:
            t=self.tenant(venue_stops,positions)
            self.assertEqual(t.on_mark('BTC',100,1000.),['normal-path'])
            t.eng.on_mark.assert_called_once_with('BTC',100,1000.)
