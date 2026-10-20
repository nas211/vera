"""Slow REST and cancellation tests; no exchange connections or private keys."""
import dataclasses
import unittest
from unittest.mock import patch
from tests.execution import test_scalp_live as fixtures
from tests.execution import test_adapter_yellow_exec as adapter
from customer.scalp.strategy import RULES
import yellow_exec


class ExitPriority(unittest.TestCase):
    setUp=fixtures.LiveBoundary.setUp
    intent=fixtures.LiveBoundary.intent

    def seed(self):
        self.r.submit(self.intent('entry'))
        row=self.r.store.orders()[-1]
        self.mock.fill(row['venue_id'],'1','100',trade_id='held')
        self.r.reconcile()
        return next(iter(self.r.model.lots))

    def test_stop_submit_precedes_unrelated_entry_cancellations(self):
        lid=self.seed()
        self.r.submit(dict(self.intent('old-exit'),lot=lid,buy=False,price=100.2))
        for i in range(3):self.r.submit(self.intent('pending-'+str(i)))
        self.r.reconcile()
        self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
        now=self.clock()
        self.r.model.lots[lid].update(stop_at=now-6,stop_mid=99.9)
        fixtures.book(self.r.model,self.clock,99.8,99.9)
        events=[];cancel=self.mock.cancel_order;place=self.mock.place_order
        def slow_cancel(account,market,venue_id):
            row=next(r for r in self.r.store.orders() if r['venue_id']==venue_id)
            events.append(('cancel-exit' if row['payload'].get('lot') else 'cancel-entry',self.clock()))
            self.clock.advance(2)
            return cancel(account,market,venue_id)
        def tracked_place(req):
            events.append(('exit' if req.reduce_only else 'entry',self.clock()))
            return place(req)
        with patch.object(self.mock,'cancel_order',side_effect=slow_cancel),patch.object(self.mock,'place_order',side_effect=tracked_place):
            self.r.frame()
        names=[x[0] for x in events]
        self.assertIn('exit',names)
        self.assertLess(names.index('exit'),names.index('cancel-entry'))
        self.assertEqual([e[1]-now for e in events if e[0]=='exit'],[2])
        iocs=[r for r in self.r.store.orders() if r['payload']['kind']=='ioc']
        self.assertEqual(len(iocs),1)
        self.assertEqual(iocs[0]['payload']['reason'],'STOP_EXIT')

    def test_slow_account_read_cannot_submit_using_expired_book(self):
        original=self.mock.get_account
        def slow_read(account):
            self.clock.advance(16)
            return original(account)
        with patch.object(self.mock,'get_account',side_effect=slow_read):
            with self.assertRaisesRegex(ValueError,'stale_market_data'):
                self.r.submit(self.intent())
        self.assertEqual(self.mock.place_calls,0)

    def test_pause_during_account_read_prevents_submission(self):
        original=self.mock.get_account
        def paused_read(account):
            self.r.ctx=dataclasses.replace(self.ctx,entries_paused=True)
            return original(account)
        with patch.object(self.mock,'get_account',side_effect=paused_read):
            with self.assertRaisesRegex(ValueError,'entries_disabled'):
                self.r.submit(self.intent())
        self.assertEqual(self.mock.place_calls,0)

    def test_entry_batch_yields_when_hold_deadline_is_reached(self):
        lid=self.seed();self.r.model.lots[lid]['opened']=self.clock()-RULES['max_hold_seconds']+1
        sent=[]
        def submit(p):sent.append(p['id']);self.clock.advance(2)
        with patch.object(self.r,'submit',side_effect=submit):
            self.r._submit_intents([self.intent('a'),self.intent('b'),self.intent('c')])
        self.assertEqual(sent,['a'])

    def test_entry_batch_yields_while_stop_close_is_pending(self):
        self.r.model.pending_exits.add('closing')
        with patch.object(self.r,'submit') as submit:
            self.r._submit_intents([self.intent('a')])
        submit.assert_not_called()


class CancellationTimestamp(unittest.TestCase):
    def test_cancel_retries_timestamp_rejection_with_new_signature(self):
        c=adapter.client([adapter.Resp(401,{'error':'invalid_timestamp'}),adapter.Resp(body={'ok':True})])
        original=yellow_exec.auth_headers
        signed=[]
        def headers(*a,**kw):
            h=original(*a,**kw);signed.append(h);return h
        with patch.object(yellow_exec,'auth_headers',side_effect=headers),patch.object(yellow_exec.time,'sleep'):
            self.assertEqual(c._req('DELETE','/perpetual/order',{'market':fixtures.M,'order_id':'known'},idempotent=True),{'ok':True})
        self.assertEqual(len(c.s.calls),2)
        self.assertEqual(len(signed),2)

    def test_timestamp_retries_are_bounded(self):
        c=adapter.client([adapter.Resp(401,{'error':'invalid_timestamp'})]*4)
        with patch.object(yellow_exec.time,'sleep'):
            with self.assertRaises(yellow_exec.YellowError):
                c._req('DELETE','/perpetual/order',{'order_id':'known'},tries=3,idempotent=True)
        self.assertEqual(len(c.s.calls),3)

    def test_order_submission_timestamp_rejection_is_never_retried(self):
        c=adapter.client([adapter.Resp(401,{'error':'invalid_timestamp'}),adapter.Resp()])
        with self.assertRaises(yellow_exec.YellowError):
            c._req('POST','/perpetual/order',{'client_order_id':'once'},idempotent=False)
        self.assertEqual(len(c.s.calls),1)
