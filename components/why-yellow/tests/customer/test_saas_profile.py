"""SaaS profile layer (2026-09-30): plan subscription + read-only trade results endpoints.
Additive to the customer API; the Yellow connect/verify/activate flow is untouched."""
import os, shutil, sqlite3, time, unittest
from .harness import FakeYellow, RuntimeStub, ApiServer, db, make_service, new_user, reset_db, token
from execution.ledger import SCHEMA

K, S = FakeYellow.KEY_OK, FakeYellow.SECRET_OK


class TestSaasProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.rt = RuntimeStub()
        cls.svc, cls.fake = make_service("ok_scopes", cls.rt)
        cls.srv = ApiServer(cls.svc)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        shutil.rmtree(cls.srv.cfg.state_dir, ignore_errors=True)

    def setUp(self):
        reset_db()
        self.A, self.B = new_user("a@example.test"), new_user("b@example.test")
        self.tA, self.tB = token(self.A.user_id, self.A.email), token(self.B.user_id, self.B.email)
        self.srv.api.limiter.hits.clear()

    # ---- subscription
    def test_subscription_defaults_free_and_owner_can_activate_paid(self):
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me/subscription", tok=self.tA)
        self.assertEqual(st, 200); self.assertEqual(j["subscription"]["plan"], "free")
        self.assertEqual([p["id"] for p in j["plans"]], ["free", "core", "pro", "prime"])
        self.assertEqual(j["plans"][2]["monthly_usd"], 129); self.assertEqual(j["plans"][2]["annual_usd"], 1290)
        j = self.srv.api.subscriptions.choose(self.A, "pro", "annual")
        s = j["subscription"]
        self.assertEqual((s["plan"], s["billing"], s["status"], s["activated_via"], s["exchange_limit"]), ("pro", "annual", "active", "owner", 3))
        # switching plan keeps one row per user
        j = self.srv.api.subscriptions.choose(self.A, "prime")
        self.assertEqual((j["subscription"]["plan"], j["subscription"]["billing"], j["subscription"]["exchange_limit"]), ("prime", "monthly", None))
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from public.why_subscriptions").fetchone()["n"], 1)
        j = self.srv.api.subscriptions.cancel(self.A)
        self.assertEqual(j["subscription"]["status"], "cancelled")

    def test_public_cannot_self_activate_and_accounts_are_isolated(self):
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/subscription", tok=self.tA, body={"plan": "enterprise"})
        self.assertEqual((st, j["error"]), (404, "not_found"))
        self.srv.api.subscriptions.choose(self.A, "core")
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me/subscription", tok=self.tB)
        self.assertEqual(st, 200); self.assertEqual(j["subscription"]["plan"], "free")
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me/subscription")
        self.assertEqual(st, 401)

    def test_subscription_independent_of_yellow_state(self):
        st, me, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual(st, 200); self.assertEqual(me["account"]["state"], "VERIFIED")
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me/subscription", tok=self.tA)
        self.assertEqual(j["subscription"]["plan"], "free")                     # free LIVE; Yellow flow never mutates billing
        self.assertTrue(j["subscription"]["live_allowed"])

    # ---- trades
    def test_trades_empty_then_from_ledger(self):
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me/trades", tok=self.tA)
        self.assertEqual(st, 200)
        self.assertEqual(j["trades"], []); self.assertEqual(j["summary"]["closed_trades"], 0); self.assertEqual(j["mode"], "live")
        aid = j["account_id"]
        # a closed long in this tenant's ledger: entry 100 -> exit 110 on qty 2, fees 0.5 => pnl 19.5
        d = os.path.join(self.srv.cfg.state_dir, "tenants", aid); os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(os.path.join(d, "ledger.sqlite"))
        con.executescript(SCHEMA)
        now = time.time()
        con.execute("insert into positions(position_id, account_id, market, direction, strategy, qty, entry_price, state, origin, reason, opened_ts, updated_ts, state_ts) "
                    "values ('p1', ?, 'BTCUSDT', 'long', 'why_pullback', '2', '100', 'CLOSED', 'entry', 'target', ?, ?, ?)", (f"yellow:{aid}", now - 600, now, now))
        con.execute("insert into positions(position_id, account_id, market, direction, strategy, qty, entry_price, state, origin, reason, opened_ts, updated_ts, state_ts) "
                    "values ('p2', ?, 'ETHUSDT', 'short', 'why_pullback', '1', '50', 'OPEN', 'entry', null, ?, ?, ?)", (f"yellow:{aid}", now - 60, now, now))
        base = "insert into orders(client_order_id, intent_key, signal_id, account_id, market, side, direction, order_type, purpose, strategy, qty, price, leverage, reduce_only, tif, state, filled_qty, avg_price, fees, position_id, created_ts, updated_ts, state_ts) values "
        con.execute(base + "('c1','i1','s1',?, 'BTCUSDT','buy','long','market','ENTRY','why_pullback','2','100',10,0,'IOC','FILLED','2','100','0.2','p1',?,?,?)", (f"yellow:{aid}", now, now, now))
        con.execute(base + "('c2','i2','s1',?, 'BTCUSDT','sell','long','market','TARGET','why_pullback','2','110',10,1,'IOC','FILLED','2','110','0.3','p1',?,?,?)", (f"yellow:{aid}", now, now, now))
        con.commit(); con.close()
        st, j, text, _ = self.srv.call("GET", "/yellow/api/me/trades?limit=5", tok=self.tA)
        self.assertEqual(st, 200)
        self.assertEqual(len(j["trades"]), 1)
        t = j["trades"][0]
        self.assertEqual((t["market"], t["direction"], t["reason"]), ("BTCUSDT", "long", "target"))
        self.assertAlmostEqual(t["pnl"], 19.5, places=6); self.assertAlmostEqual(t["fees"], 0.5, places=6)
        self.assertEqual(j["summary"]["closed_trades"], 1); self.assertEqual(j["summary"]["wins"], 1); self.assertEqual(j["summary"]["open_positions"], 1)
        self.assertAlmostEqual(j["summary"]["realized_pnl"], 19.5, places=6)
        self.assertNotIn(S, text); self.assertNotIn(K, text)
        # B has its own (empty) ledger view
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me/trades", tok=self.tB)
        self.assertEqual(j["trades"], [])


if __name__ == "__main__":
    unittest.main()
