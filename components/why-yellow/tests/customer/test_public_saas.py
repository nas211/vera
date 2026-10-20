import shutil
import time
import unittest
from decimal import Decimal

from customer.tenants import CanonicalSignal, CloseSignal
from customer.router import ExecutionRouter
from customer.config import Config
from customer.subscriptions import SubscriptionService
from weex.service import WeexAccountService, WeexCredentialResolver
from .harness import ApiServer, db, make_service, new_user, reset_db, token


class TestPaperAccount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.svc, _ = make_service()
        cls.srv = ApiServer(cls.svc)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        shutil.rmtree(cls.srv.cfg.state_dir, ignore_errors=True)

    def setUp(self):
        reset_db()
        self.user = new_user("paper@example.test")
        self.jwt = token(self.user.user_id, self.user.email)

    def call(self, method, path, body=None):
        return self.srv.call(method, "/yellow/api" + path, tok=self.jwt, body=body)[:2]

    def test_free_account_equity_margin_pnl_and_archived_reset(self):
        status, account = self.call("GET", "/me/paper")
        self.assertEqual(status, 200)
        self.assertEqual((account["paper_starting_balance"], account["paper_equity"]), (25000.0, 25000.0))
        self.assertEqual(account["settings"], {"strategy": "WHY Alpha", "allocation_pct": 25.0,
                                                "position_size_pct": 5.0, "leverage": 10,
                                                "risk_preset": "balanced"})
        self.call("POST", "/me/paper/start")
        signal = CanonicalSignal("paper-s1", "BTCUSDT-PERP", "long", "why_pullback", Decimal("100"), time.time(), {})
        self.assertEqual(self.srv.api.paper.dispatch(signal)[self.user.user_id], "OPENED")
        _, opened = self.call("GET", "/me/paper")
        self.assertEqual(opened["paper_used_margin"], 312.5)
        self.assertEqual(len(opened["paper_open_positions"]), 1)
        self.assertLess(opened["paper_available_balance"], 24687.5)  # entry fee also reduces equity
        self.srv.api.paper.on_mark("BTCUSDT-PERP", Decimal("110"))
        self.srv.api.paper.dispatch_close(CloseSignal("BTCUSDT-PERP", "long", "TARGET", time.time()))
        _, closed = self.call("GET", "/me/paper")
        self.assertEqual(closed["paper_used_margin"], 0.0)
        self.assertEqual(len(closed["paper_trade_history"]), 1)
        self.assertGreater(closed["paper_equity"], 25000.0)
        old_session = closed["paper_session_id"]
        _, reset = self.call("POST", "/me/paper/reset")
        self.assertNotEqual(reset["paper_session_id"], old_session)
        self.assertEqual(reset["paper_equity"], 25000.0)
        self.assertEqual(reset["paper_trade_history"], [])
        self.assertEqual(reset["paper_open_positions"], [])
        self.assertEqual(reset["paper_realized_pnl"], 0)
        self.assertFalse(reset["running"])
        with db().service() as c:
            archived = c.execute("select archived_reason from public.why_paper_sessions where id=%s", (old_session,)).fetchone()
        self.assertEqual(archived["archived_reason"], "user_reset")

    def test_settings_persist_and_are_validated_server_side(self):
        status, account = self.call("POST", "/me/paper/settings", {
            "strategy": "why-1", "allocation_pct": 50, "position_size_pct": 10,
            "leverage": 15, "risk_preset": "aggressive",
        })
        self.assertEqual(status, 200)
        self.assertEqual(account["settings"]["allocation_pct"], 50.0)
        self.assertEqual(account["settings"]["leverage"], 15)
        self.call("POST", "/me/paper/start")
        signal = CanonicalSignal("paper-selected-strategy", "ETHUSDT-PERP", "short", "WY-REVERT", Decimal("2500"), time.time(), {})
        self.assertEqual(self.srv.api.paper.dispatch(signal)[self.user.user_id], "OPENED")
        self.srv.api.paper.on_mark("ETHUSDT-PERP", Decimal("2490"))
        self.srv.api.paper.dispatch_close(CloseSignal("ETHUSDT-PERP", "short", "TARGET", time.time()))
        _, traded = self.call("GET", "/me/paper")
        self.assertEqual(traded["paper_trade_history"][0]["reason"], "TARGET")
        status, body = self.call("POST", "/me/paper/settings", {
            "strategy": "why-1", "allocation_pct": 100, "position_size_pct": 20,
            "leverage": 50, "risk_preset": "custom",
        })
        self.assertEqual((status, body["error"]), (422, "invalid_risk_settings"))

    def test_target_close_is_paper_only_while_timeout_reaches_live(self):
        class Sink:
            def __init__(self): self.reasons = []
            def dispatch_close(self, signal): self.reasons.append(signal.reason); return "CLOSED"
        yellow, paper = Sink(), Sink()
        router = ExecutionRouter(yellow, paper=paper)
        target = CloseSignal("BTCUSDT-PERP", "long", "TARGET", time.time())
        out = router.dispatch_close(target)
        self.assertEqual(out, {"yellow": "VENUE_MANAGED", "paper": "CLOSED"})
        self.assertEqual(yellow.reasons, [])
        self.assertEqual(paper.reasons, ["TARGET"])
        router.dispatch_close(CloseSignal("BTCUSDT-PERP", "long", "TIMEOUT", time.time()))
        self.assertEqual(yellow.reasons, ["TIMEOUT"])


class FakeWeex:
    def __init__(self, key, secret, passphrase, **kwargs): self.values = key, secret, passphrase
    def exchange_info(self): return {"symbols": [{"symbol": "BTCUSDT", "contractType": "PERPETUAL"}]}
    def api_trading_symbols(self): return ["BTCUSDT"]
    def balances(self): return [{"asset": "USDT", "balance": "500", "availableBalance": "450"}]
    def positions(self): return []
    def set_leverage(self, symbol, leverage): return {"symbol": symbol, "leverage": leverage}


class TestWeexAccounts(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.user = new_user("weex@example.test")
        self.subscriptions = SubscriptionService(db())
        self.subscriptions.choose(self.user, "pro", activated_via="owner")
        self.service = WeexAccountService(db(), self.subscriptions,
            Config(weex_enabled=True, weex_broker_id="WHY123"), client_factory=FakeWeex)

    def test_connection_uses_vault_and_has_independent_start_stop(self):
        key, secret, phrase = "weex-key-12345", "weex-secret-12345", "weexpassphrase12345"
        account = self.service.connect(self.user, key, secret, phrase)
        self.assertEqual(account["state"], "VERIFIED")
        self.assertNotIn(secret, repr(account)); self.assertNotIn(phrase, repr(account))
        with db().service() as c:
            public_text = " ".join(r["row"] for table in ("weex_accounts", "weex_credentials")
                for r in c.execute(f"select t::text row from public.{table} t").fetchall())
        self.assertNotIn(secret, public_text); self.assertNotIn(phrase, public_text)
        ctx = self.service.list_execution_contexts()[0]
        credentials = WeexCredentialResolver(db()).resolve(ctx)
        self.assertNotIn(secret, repr(credentials)); self.assertNotIn(phrase, repr(credentials))
        self.assertEqual(self.service.set_running(self.user, True)["state"], "ACTIVE")
        self.assertEqual(self.service.set_running(self.user, False)["state"], "PAUSED")

    def test_free_user_can_connect_one_live_exchange(self):
        free = new_user("free-weex@example.test")
        self.subscriptions.ensure(free)
        connected = self.service.connect(free, "weex-key-54321", "weex-secret-54321", "weexpassphrase54321")
        self.assertEqual(connected["state"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()

class TestManualPaperClose(TestPaperAccount):
    def test_market_close_owned_position_once(self):
        self.call('GET','/me/paper');self.call('POST','/me/paper/start')
        other=new_user('other-close@example.test')
        self.srv.api.paper.get(other);self.srv.api.paper.set_running(other,True)
        sig=CanonicalSignal('manual-close','ETHUSDT-PERP','long','WY-REVERT',Decimal('2500'),time.time(),{})
        self.srv.api.paper.dispatch(sig);self.srv.api.paper.on_mark(sig.market,Decimal('2510'))
        _,a=self.call('GET','/me/paper');pid=a['paper_open_positions'][0]['id']
        data={'venue':'paper','position_id':pid,'confirm':True}
        status,body=self.srv.call('POST','/yellow/api/me/positions/close',tok=token(other.user_id,other.email),body=data)[:2]
        self.assertEqual((status,body['error']),(404,'position_not_found'))
        self.assertEqual(self.call('POST','/me/positions/close',{**data,'confirm':False})[0],422)
        self.assertEqual(self.call('POST','/me/positions/close',data),(200,{'result':'CLOSED'}))
        self.assertEqual(self.call('POST','/me/positions/close',data),(200,{'result':'ALREADY_CLOSED'}))
        _,a=self.call('GET','/me/paper')
        self.assertEqual(len(a['paper_open_positions']),0)
        self.assertEqual(len(a['paper_trade_history']),1)
        self.assertEqual(a['paper_trade_history'][0]['reason'],'USER_MARKET_CLOSE')
        self.assertEqual(a['paper_used_margin'],0)
        self.assertEqual(len(self.srv.api.paper.get(other)['paper_open_positions']),1)
    def test_stale_mark_rejected_without_close(self):
        self.call('GET','/me/paper');self.call('POST','/me/paper/start')
        sig=CanonicalSignal('stale-close','ETHUSDT-PERP','short','WY-REVERT',Decimal('2500'),time.time(),{})
        self.srv.api.paper.dispatch(sig);self.srv.api.paper.on_mark(sig.market,Decimal('2500'),time.time()-60)
        _,a=self.call('GET','/me/paper');pid=a['paper_open_positions'][0]['id']
        status,body=self.call('POST','/me/positions/close',{'venue':'paper','position_id':pid,'confirm':True})
        self.assertEqual((status,body['error']),(503,'market_price_unavailable'))
        self.assertEqual(len(self.call('GET','/me/paper')[1]['paper_open_positions']),1)
