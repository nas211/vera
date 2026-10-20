import json, logging, unittest, uuid
from .harness import ANON_KEY, FakeYellow, JWT_SECRET, RuntimeStub, ApiServer, db, make_config, make_service, new_user, reset_db, token
from customer import log as clog

K, S = FakeYellow.KEY_OK, FakeYellow.SECRET_OK


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.rt = RuntimeStub()
        cls.svc, cls.fake = make_service("ok_scopes", cls.rt)
        cls.srv = ApiServer(cls.svc)
        cls.cap = clog.CaptureHandler(); cls.cap.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s")); clog.attach(cls.cap)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close(); clog.detach(cls.cap)

    def setUp(self):
        reset_db()
        self.A, self.B = new_user("a@example.test"), new_user("b@example.test")
        self.tA, self.tB = token(self.A.user_id, self.A.email), token(self.B.user_id, self.B.email)
        self.srv.api.subscriptions.choose(self.A, "core", activated_via="owner")
        self.srv.api.subscriptions.choose(self.B, "core", activated_via="owner")
        self.fake.mode = "ok_scopes"; self.srv.api.limiter.hits.clear()

    # ---- public
    def test_health_and_public_config(self):
        st, j, _, h = self.srv.call("GET", "/yellow/api/health")
        self.assertEqual(st, 200)
        for k in ("APP_HEALTHY", "SUPABASE_HEALTHY", "VAULT_HEALTHY", "YELLOW_CONNECTED", "YELLOW_VERIFIED", "EXECUTION_READY"):
            self.assertIn(k, j)
        self.assertTrue(j["APP_HEALTHY"]); self.assertTrue(j["SUPABASE_HEALTHY"]); self.assertTrue(j["VAULT_HEALTHY"])
        self.assertFalse(j["YELLOW_CONNECTED"]); self.assertFalse(j["EXECUTION_READY"]); self.assertFalse(j["ready"])
        self.assertEqual(h.get("Cache-Control"), "no-store"); self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        st, j, text, _ = self.srv.call("GET", "/yellow/api/public-config")
        self.assertEqual(st, 200)
        self.assertEqual(j["supabase_anon_key"], ANON_KEY)
        self.assertNotIn(JWT_SECRET, text); self.assertNotIn("service_role", text.lower()); self.assertNotIn("why_yellow_test_pw", text)
        self.assertEqual(j["sizes_usd"], [20, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000])

    # ---- auth
    def test_unauthenticated_rejected(self):
        for path in ("/yellow/api/me", "/yellow/api/me/yellow/connect", "/yellow/api/me/bot/activate"):
            st, j, _, _ = self.srv.call("GET" if path.endswith("/me") else "POST", path, body={} if not path.endswith("/me") else None)
            self.assertEqual(st, 401, path); self.assertEqual(j["error"], "unauthorized")
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me", tok="garbage.token.here")
        self.assertEqual(st, 401)
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me", tok=token(self.A.user_id, ttl=-10))
        self.assertEqual((st, j["code"]), (401, "token_expired"))
        st, j, _, _ = self.srv.call("GET", "/yellow/api/me", tok=token(self.A.user_id, secret="wrong-secret-wrong-secret-wrong-secret"))
        self.assertEqual(st, 401)

    def test_forged_identity_rejected(self):
        for body in ({"user_id": self.B.user_id}, {"account_id": "x"}, {"tenant": "b"}, {"UserId": self.B.user_id}):
            st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/size", tok=self.tA, body={"position_size_usd": 200, **body})
            self.assertEqual((st, j["error"]), (400, "client_supplied_identity_rejected"), body)
        st, j, _, _ = self.srv.call("GET", f"/yellow/api/me?user_id={self.B.user_id}", tok=self.tA)
        self.assertEqual(st, 400)
        # A's token with B's id anywhere: B untouched
        self.assertEqual(self.svc.get_me(self.B)["config"]["position_size_usd"], 100)

    # ---- flow + isolation
    def test_connect_flow_and_cross_user_isolation(self):
        st, j, text, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual(st, 200, j)
        self.assertEqual(j["account"]["state"], "VERIFIED")
        self.assertNotIn(K, text); self.assertNotIn(S, text)
        aid = j["account"]["id"]
        st, jb, textb, _ = self.srv.call("GET", "/yellow/api/me", tok=self.tB)
        self.assertEqual(st, 200)
        self.assertNotEqual(jb["account"]["id"], aid)
        self.assertEqual(jb["account"]["state"], "DISCONNECTED"); self.assertIsNone(jb["account"]["api_key_masked"])
        self.assertNotIn(aid, textb)
        # B cannot bind A's key, cannot activate A, cannot change A's size
        st, jb, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tB, body={"api_key": K, "api_secret": S})
        self.assertEqual((st, jb["error"]), (409, "credential_already_bound"))
        st, jb, _, _ = self.srv.call("POST", "/yellow/api/me/bot/activate", tok=self.tB, body={})
        self.assertEqual(st, 409)
        self.srv.api.limiter.hits.clear()
        st, jb, _, _ = self.srv.call("POST", "/yellow/api/me/bot/size", tok=self.tB, body={"position_size_usd": 2000})
        self.assertEqual(st, 200)
        self.assertEqual(self.svc.get_me(self.A)["config"]["position_size_usd"], 100)
        self.assertEqual(self.svc.get_me(self.A)["account"]["state"], "VERIFIED")
        # A activates, pauses, changes size, disconnects
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/activate", tok=self.tA, body={}); self.assertEqual(j["account"]["state"], "ACTIVE")
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/size", tok=self.tA, body={"position_size_usd": 500}); self.assertEqual(j["config"]["position_size_usd"], 500)
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/size", tok=self.tA, body={"position_size_usd": 999}); self.assertEqual((st, j["error"]), (422, "size_not_allowed"))
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/pause", tok=self.tA, body={}); self.assertEqual(j["account"]["state"], "PAUSED")
        self.rt.open[aid] = 1
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/disconnect", tok=self.tA, body={}); self.assertEqual((st, j["error"]), (409, "open_positions"))
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/disconnect", tok=self.tA, body={"confirm_unmanaged": True}); self.assertEqual(j["account"]["state"], "DISCONNECTED")
        self.rt.open.clear()

    def test_invalid_credentials_and_states_over_http(self):
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": "x", "api_secret": S})
        self.assertEqual((st, j["error"]), (422, "invalid_api_key_format"))
        self.fake.mode = "invalid"
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual((st, j["account"]["state"], j["account"]["verification_state"]), (200, "DISCONNECTED", "FAILED"))
        self.fake.mode = "unreachable"
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual((st, j["account"]["state"]), (200, "PENDING_YELLOW_VERIFICATION"))
        self.fake.mode = "forbidden"
        self.srv.api.limiter.hits.clear()
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual((st, j["account"]["verification_state"]), (200, "FORBIDDEN_PERMISSION"))
        self.fake.mode = "ok_no_scopes"
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual((st, j["account"]["state"], j["account"]["verification_reason"]), (200, "PENDING_YELLOW_VERIFICATION", "permission_metadata_unavailable"))

    def test_rate_limit_and_body_limits(self):
        for i in range(5):
            self.srv.call("POST", "/yellow/api/me/yellow/verify", tok=self.tA, body={})
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/yellow/verify", tok=self.tA, body={})
        self.assertEqual((st, j["error"]), (429, "rate_limited"))
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/size", tok=self.tA, raw=b"x" * 9000)
        self.assertEqual(st, 413)
        st, j, _, _ = self.srv.call("POST", "/yellow/api/me/bot/size", tok=self.tA, raw=b"not json")
        self.assertEqual((st, j["error"]), (400, "bad_request"))
        st, j, _, _ = self.srv.call("GET", "/yellow/api/nope", tok=self.tA)
        self.assertEqual(st, 404)
        st, j, _, _ = self.srv.call("GET", "/other/path")
        self.assertEqual(st, 404)

    # ---- logs
    def test_secrets_never_reach_logs(self):
        self.cap.lines.clear()
        lg = clog.get_logger("test")
        self.fake.mode = "raise"    # verifier raises with the secret inside the message
        st, j, text, _ = self.srv.call("POST", "/yellow/api/me/yellow/connect", tok=self.tA, body={"api_key": K, "api_secret": S})
        self.assertEqual(st, 200); self.assertNotIn(S, text)
        lg.info("debug dump api_key=%s api_secret=%s Authorization: Bearer %s", K, S, self.tA)
        lg.error("exception %s", RuntimeError("boom " + S + " " + K))
        try:
            raise ValueError("secret leak " + S)
        except ValueError:
            lg.exception("caught")
        lines = "\n".join(self.cap.lines)
        self.assertTrue(any("verifier raised" in l for l in self.cap.lines))
        self.assertNotIn(S, lines); self.assertNotIn(K, lines); self.assertNotIn(self.tA, lines)
        self.assertIn("[REDACTED]", lines)
        self.assertIn("api_key=[REDACTED]", lines)

    def test_not_configured_mode(self):
        srv = ApiServer(None, cfg=make_config(supabase_url="", supabase_anon_key="", supabase_db_url=None), ver=None)
        try:
            st, j, _, _ = srv.call("GET", "/yellow/api/public-config"); self.assertEqual((st, j["configured"]), (200, False))
            st, j, _, _ = srv.call("GET", "/yellow/api/me", tok=self.tA); self.assertEqual((st, j["error"]), (503, "supabase_not_configured"))
            st, j, _, _ = srv.call("GET", "/yellow/api/health"); self.assertEqual((st, j["SUPABASE_HEALTHY"], j["EXECUTION_READY"]), (200, False, False))
        finally:
            srv.close()

class TestManualCloseApi(TestApi):
    def test_auth_validation_and_owner_selection(self):
        from types import SimpleNamespace
        calls=[]
        self.srv.api.registry=SimpleNamespace(close_for_user=lambda ident,venue,pid:calls.append((ident.user_id,venue,pid)) or 'CLOSE_SUBMITTED')
        path='/yellow/api/me/positions/close';payload={'venue':'yellow','position_id':'why-test','confirm':True}
        try:
            self.assertEqual(self.srv.call('POST',path,body=payload)[0],401)
            self.srv.call('POST','/yellow/api/me/yellow/connect',tok=self.tA,body={'api_key':K,'api_secret':S})
            self.assertEqual(self.srv.call('POST',path,tok=self.tA,body={**payload,'user_id':self.B.user_id})[0],400)
            self.assertEqual(self.srv.call('POST',path,tok=self.tA,body={**payload,'confirm':False})[0],422)
            self.assertEqual(calls,[])
            self.assertEqual(self.srv.call('POST',path,tok=self.tA,body=payload)[:2],(200,{'result':'CLOSE_SUBMITTED'}))
            self.assertEqual(calls,[(self.A.user_id,'yellow','why-test')])
            self.assertEqual(self.srv.call('POST',path,tok=self.tB,body=payload)[0],409)
            self.assertEqual(len(calls),1)
        finally:self.srv.api.registry=None
