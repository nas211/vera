"""Manual close is scoped to a JWT owner, one position, and one outstanding order."""
import unittest
from types import SimpleNamespace
from .test_tenant_fanout import Env, ctx
from customer.accounts import AccountError

class TestManualClose(unittest.TestCase):
    def setUp(self):
        self.e=Env()
        for aid in ('acctA','acctB'): self.e.accounts.set(ctx(aid,100))
        self.e.ready(); self.e.reg.dispatch(self.e.sig()); self.e.fill_entries()
        self.a=self.e.reg.tenants['acctA']; self.b=self.e.reg.tenants['acctB']
        self.pid=self.a.open_positions()[0]['position_id']
        self.ident=SimpleNamespace(user_id='user-acctA')
    def tearDown(self): self.e.cleanup()
    def test_close_only_owned_position_and_deduplicate(self):
        before=self.e.mocks['acctB'].place_calls
        result=self.e.reg.close_for_user(self.ident,self.pid)
        self.assertEqual(result,'CLOSE_SUBMITTED')
        n=self.e.mocks['acctA'].place_calls
        self.assertEqual(self.e.reg.close_for_user(self.ident,self.pid),'CLOSE_PENDING')
        self.assertEqual(self.e.mocks['acctA'].place_calls,n)
        self.assertEqual(self.e.mocks['acctB'].place_calls,before)
        orders=self.a.ledger.orders_where(list(__import__('execution.types',fromlist=['OrderState']).OrderState),account_id=self.a.ctx.namespace)
        close=[x for x in orders if x['purpose']=='CLOSE']
        self.assertEqual(len(close),1); self.assertTrue(close[0]['reduce_only'])
        self.assertEqual(close[0]['order_type'],'market');self.assertEqual(close[0]['position_id'],self.pid)
    def test_cross_owner_denied(self):
        before=self.e.mocks['acctA'].place_calls
        with self.assertRaises(AccountError) as cm:
            self.e.reg.close_for_user(SimpleNamespace(user_id='user-acctB'),self.pid)
        self.assertEqual(cm.exception.code,'position_not_found')
        self.assertEqual(self.e.mocks['acctA'].place_calls,before)
    def test_paused_accounts_can_close(self):
        self.a.apply_context(ctx('acctA',100,state='PAUSED',paused=True))
        self.assertEqual(self.e.reg.close_for_user(self.ident,self.pid),'CLOSE_SUBMITTED')
    def test_runtime_includes_exact_position_id(self):
        self.assertEqual(self.a.runtime_info()['positions'][0]['position_id'],self.pid)

class TestCancelCredentialProxy(unittest.TestCase):
    def test_private_cancel_resolves_credentials(self):
        from customer.context import LazyYellowClient
        calls=[]
        lazy=LazyYellowClient(None,ctx('acctA',100))
        lazy.ensure=lambda:SimpleNamespace(_req=lambda *a,**kw:calls.append((a,kw)))
        lazy._req('DELETE','/perpetual/order',{'market':'BTCUSDT-PERP'},idempotent=True)
        self.assertEqual(calls[0][0][0],'DELETE')
    def test_degraded_order_snapshot_is_not_empty_success(self):
        from execution.yellow_transport import YellowTransport
        from execution.transport import VenueUnavailable
        transport=YellowTransport(SimpleNamespace(open_orders_page=lambda *args:{'orders':[],'stale':True}))
        with self.assertRaises(VenueUnavailable):transport.get_open_orders('acctA')
