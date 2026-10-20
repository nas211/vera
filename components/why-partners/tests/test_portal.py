"""why-partners tests: auth rules, read-only book normalisation, HTTP surface.
Run: python3 -m unittest discover -s tests -v   (uses a temp DB + temp fixtures)"""
import json, os, sys, tempfile, threading, time, unittest, urllib.request, urllib.error, http.cookies

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="whyp-")
os.environ["PARTNERS_DB"] = os.path.join(TMP, "t.db")
os.environ["PARTNERS_COOKIE_SECURE"] = "0"
os.environ["PARTNERS_PORT"] = "18099"
os.environ["PARTNERS_CACHE_SEC"] = "0"
os.environ["PARTNERS_LOGIN_MAX_FAILS"] = "3"
# synthetic engine files so tests never touch the live book
os.environ["PARTNERS_SINCE_ISO"] = "2026-01-01T00:00:00Z"
TR = os.path.join(TMP, "trades.jsonl"); ST = os.path.join(TMP, "state.json")
os.environ["PARTNERS_TRADES"] = TR; os.environ["PARTNERS_STATE"] = ST
os.environ["AV2_PAPER_NOTIONAL_USD"] = "1000"
rows = [
 {"coin":"BTC","direction":"long","entry_px":100.0,"exit_px":102.0,"opened_ts":1767225600,"opened_ts_iso":"2026-01-01T00:00:00Z","ts":1767229200,"ts_iso":"2026-01-01T01:00:00Z","notional_usd":1000,"tp_usd":25,"sl_usd":20,"pnl_usd":19.5,"reason":"tp","spec_hash":"h1","strat":"C:(secret sexpr):long"},
 {"coin":"ETH","direction":"short","entry_px":50.0,"exit_px":51.0,"opened_ts":1767232800,"opened_ts_iso":"2026-01-01T02:00:00Z","ts":1767236400,"ts_iso":"2026-01-01T03:00:00Z","notional_usd":1000,"tp_usd":30,"sl_usd":25,"pnl_usd":-20.4,"reason":"stop","spec_hash":"h2","strat":"C:(secret2):short"},
]
with open(TR, "w") as fh:
    for r in rows: fh.write(json.dumps(r) + "\n")
json.dump({"open": {"SOL|h3": {"coin":"SOL","direction":"long","entry_px":10.0,"tp_px":10.25,"sl_px":9.8,"notional_usd":1000,"opened_ts":1767240000,"spec_hash":"h3","strat":"C:(secret3):long","spec_key":"k"}}}, open(ST, "w"))

sys.path.insert(0, ROOT)
import auth, book, server  # noqa: E402
book.prices = lambda syms: {s: {"px": 10.5, "pct24h": 1.0, "ts": time.time()} for s in syms}  # no network in tests


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        auth.init_db(); auth.create_partner("Alice@Example.com", "correct horse battery", "Alice")

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError): auth.create_partner("bob", "short", "")

    def test_login_case_insensitive_and_session_roundtrip(self):
        tok, why = auth.login("alice@example.com", "correct horse battery", "1.1.1.1")
        self.assertTrue(tok); self.assertEqual(why, "ok")
        self.assertEqual(auth.session_user(tok)["display_name"], "Alice")
        auth.logout(tok); self.assertIsNone(auth.session_user(tok))

    def test_generic_failure_and_lockout(self):
        auth.create_partner("erin", "erin has a long password", "")
        for _ in range(3):
            tok, why = auth.login("erin", "wrong", "2.2.2.2"); self.assertIsNone(tok)
        tok, why = auth.login("erin", "erin has a long password", "2.2.2.2")
        self.assertIsNone(tok); self.assertEqual(why, "locked")
        tok, why = auth.login("ghost", "x", "3.3.3.3"); self.assertIsNone(tok)

    def test_disable_kills_sessions_and_reset_revokes(self):
        auth.create_partner("carol", "another long password", "")
        tok, _ = auth.login("carol", "another long password", "4.4.4.4"); self.assertTrue(auth.session_user(tok))
        auth.set_enabled("carol", False); self.assertIsNone(auth.session_user(tok))
        self.assertIsNone(auth.login("carol", "another long password", "4.4.4.4")[0])
        auth.set_enabled("carol", True); tok, _ = auth.login("carol", "another long password", "4.4.4.4")
        auth.reset_password("carol", "brand new long password"); self.assertIsNone(auth.session_user(tok))
        self.assertTrue(auth.login("carol", "brand new long password", "4.4.4.4")[0])


class BookTest(unittest.TestCase):
    def test_normalised_rows_carry_no_ip(self):
        s = book.snapshot(True)
        blob = json.dumps(s)
        for bad in ("secret", "spec_hash", "strat", "spec_key", "/opt/", "family"):
            self.assertNotIn(bad, blob)
        self.assertEqual(s["summary"]["closed_trades"], 2)
        self.assertEqual(s["summary"]["realized_usd"], round(19.5 - 20.4, 2))
        self.assertEqual(s["summary"]["open_positions"], 1)
        o = s["open"][0]; self.assertEqual(o["mark_px"], 10.5); self.assertAlmostEqual(o["unrealized_usd"], 50.0)
        self.assertEqual(s["summary"]["unrealized_usd"], 50.0)
        t = {r["symbol"]: r for r in s["trades"]}
        self.assertEqual(t["BTCUSDT"]["exit_reason"], "TARGET"); self.assertEqual(t["ETHUSDT"]["exit_reason"], "STOP")
        self.assertEqual(t["BTCUSDT"]["qty"], 10.0)
        self.assertEqual(t["ETHUSDT"]["move_pct"], -2.0)      # short that went against
        self.assertEqual(s["equity"][0]["nav_usd"], book.CAPITAL_USD)
        self.assertEqual(s["summary"]["max_drawdown_usd"], -20.4)

    def test_state_files_opened_read_only(self):
        # book must never open the engine files writable
        opened = []
        real = os.open
        def spy(path, flags, *a, **k):
            opened.append((path, flags)); return real(path, flags, *a, **k)
        os.open = spy
        try: book.snapshot(True)
        finally: os.open = real
        for path, flags in opened:
            if path in (TR, ST): self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT), 0)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        auth.init_db(); auth.create_partner("dave", "dave has a long password", "Dave")
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 18099), server.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:18099"

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()

    def req(self, path, method="GET", body=None, cookie=None, headers=None):
        h = dict(headers or {})
        if body is not None: h["Content-Type"] = "application/json"; body = json.dumps(body).encode()
        if cookie: h["Cookie"] = "why_partner=" + cookie
        r = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            resp = urllib.request.urlopen(r); return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

    def login(self):
        code, hdr, _ = self.req("/partners/api/login", "POST", {"username": "dave", "password": "dave has a long password"})
        self.assertEqual(code, 200); c = http.cookies.SimpleCookie(); c.load(hdr["Set-Cookie"]); return c["why_partner"].value

    def test_unauth_401_and_redirect(self):
        for ep in ("summary", "open", "trades", "equity", "prices", "session"):
            self.assertEqual(self.req("/partners/api/" + ep)[0], 401, ep)
        opener = urllib.request.build_opener(type("NR", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda *a, **k: None}))
        try: opener.open(self.base + "/partners"); self.fail("expected 302")
        except urllib.error.HTTPError as e: self.assertEqual(e.code, 302); self.assertTrue(e.headers["Location"].endswith("/partners/login"))

    def test_login_flow_headers_logout(self):
        code, hdr, _ = self.req("/partners/api/login", "POST", {"username": "dave", "password": "nope"})
        self.assertEqual(code, 401)
        tok = self.login()
        code, hdr, body = self.req("/partners/api/summary", cookie=tok)
        self.assertEqual(code, 200); self.assertIn("no-store", hdr["Cache-Control"]); self.assertEqual(hdr["X-Frame-Options"], "DENY")
        self.assertIn("summary", json.loads(body))
        code, _, body = self.req("/partners/api/session", cookie=tok); self.assertEqual(json.loads(body)["user"], "Dave")
        self.assertEqual(self.req("/partners/api/logout", "POST", cookie=tok)[0], 200)
        self.assertEqual(self.req("/partners/api/summary", cookie=tok)[0], 401)

    def test_cross_origin_post_refused_and_traversal_404(self):
        code, _, _ = self.req("/partners/api/login", "POST", {"username": "dave", "password": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(self.req("/partners/static/../server.py")[0], 404)
        self.assertEqual(self.req("/partners/static/app.js")[0], 200)
        self.assertEqual(self.req("/partners/server.py")[0], 404)
        self.assertEqual(self.req("/partners/static/../../etc/passwd")[0], 404)


if __name__ == "__main__":
    unittest.main()
