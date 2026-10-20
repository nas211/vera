"""Affiliate CRM tests on the local shim DB (same harness as tests/customer). No network."""
from __future__ import annotations
import json
import os
import sys
import unittest
import uuid
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.customer import harness as H                     # noqa: E402
from affiliate.auth import Sessions, check_password, hash_password   # noqa: E402
from affiliate.config import Config                          # noqa: E402
from affiliate.reports import ReportError, Reports           # noqa: E402
from affiliate.service import App, COOKIE                    # noqa: E402
from customer.auth import Identity                           # noqa: E402

SECRET = "x" * 48


def _user(email, partner=None, confirmed=False):
    uid = str(uuid.uuid4())
    meta = {"partner": partner} if partner else {}
    with H.db().service() as c:
        c.execute("insert into auth.users(id, email, raw_user_meta_data, email_confirmed_at) values (%s, %s, %s::jsonb, case when %s then now() end)",
                  (uid, email, json.dumps(meta), confirmed))
    H._CREATED_USERS.add(uid)
    return Identity(uid, email, "authenticated", partner=partner)


class ReportsTest(unittest.TestCase):
    def setUp(self):
        H.reset_db()
        self.rep = Reports(H.db(), "https://whytradingbot.com")

    def test_partner_seeded_and_link(self):
        codes = {p["code"]: p for p in self.rep.partners()}
        self.assertIn("yellow", codes)
        self.assertEqual(codes["yellow"]["link"], "https://whytradingbot.com/r/yellow")

    def test_attribution_from_metadata_and_account_row(self):
        _user("a@x.test", "yellow", confirmed=True)
        _user("b@x.test", "yellow")
        _user("c@x.test", None)
        _user("d@x.test", "nobody")
        o = self.rep.overview(30)
        self.assertEqual(o["totals"]["signups"], 4)
        self.assertEqual(o["totals"]["confirmed"], 1)
        by = {p["code"]: p for p in o["partners"]}
        self.assertEqual(by["yellow"]["signups"], 2)
        self.assertEqual(by["direct"]["signups"], 1)
        self.assertEqual(by["nobody"]["signups"], 1)
        self.assertFalse(by["nobody"]["registered"])
        self.assertEqual(sum(d["total"] for d in o["daily"]), 4)
        s = self.rep.signups(partner="yellow")
        self.assertEqual(s["total"], 2)
        self.assertEqual({i["email"] for i in s["items"]}, {"a@x.test", "b@x.test"})
        self.assertEqual(self.rep.signups(q="c@x")["total"], 1)
        csv = self.rep.signups_csv(partner="yellow")
        self.assertIn("a@x.test,yellow", csv)

    def test_first_touch_written_on_account_row(self):
        """The customer backend copies the JWT partner into yellow_accounts.partner_code on first /me,
        and keeps attributing the user even if they later wipe their own metadata."""
        ident = _user("e@x.test", "yellow")
        svc = H.make_service("ok_scopes")
        svc = svc[0] if isinstance(svc, tuple) else svc
        svc.get_me(ident)
        with H.db().service() as c:
            self.assertEqual(c.execute("select partner_code from public.yellow_accounts where user_id = %s", (ident.user_id,)).fetchone()["partner_code"], "yellow")
            c.execute("update auth.users set raw_user_meta_data = '{}'::jsonb where id = %s", (ident.user_id,))
        by = {p["code"]: p for p in self.rep.partners()}
        self.assertEqual(by["yellow"]["signups"], 1)
        # a second /me never overwrites the first touch
        svc.get_me(Identity(ident.user_id, ident.email, "authenticated", partner="other"))
        with H.db().service() as c:
            self.assertEqual(c.execute("select partner_code from public.yellow_accounts where user_id = %s", (ident.user_id,)).fetchone()["partner_code"], "yellow")

    def test_account_created_without_partner_column(self):
        """Before the affiliate migration is applied the customer backend must still create accounts."""
        ident = _user("f@x.test", "yellow")
        svc = H.make_service("ok_scopes")[0]
        with H.db().service() as c:
            c.execute("alter table public.yellow_accounts drop column partner_code")
        try:
            me = svc.get_me(ident)
            self.assertEqual(me["account"]["state"], "DISCONNECTED")
        finally:
            with H.db().service() as c:
                c.execute("alter table public.yellow_accounts add column partner_code text")

    def test_jwt_partner_claim(self):
        import time as _t
        import jwt as _jwt
        v = H.verifier()
        uid = str(uuid.uuid4())
        now = int(_t.time())
        base = {"sub": uid, "email": "j@x.test", "role": "authenticated", "aud": "authenticated", "iss": f"{H.SUPA_URL}/auth/v1", "iat": now, "exp": now + 600}
        tok = _jwt.encode({**base, "user_metadata": {"partner": " Yellow "}}, H.JWT_SECRET, algorithm="HS256")
        self.assertEqual(v.verify(tok).partner, "yellow")
        tok = _jwt.encode({**base, "user_metadata": {"partner": ""}}, H.JWT_SECRET, algorithm="HS256")
        self.assertIsNone(v.verify(tok).partner)
        tok = _jwt.encode(base, H.JWT_SECRET, algorithm="HS256")
        self.assertIsNone(v.verify(tok).partner)

    def test_size_hidden_until_connected_and_delete_signup(self):
        ident = _user("g@x.test", "yellow")
        H.make_service("ok_scopes")[0].get_me(ident)          # creates the account row with the default size
        it = self.rep.signups(q="g@x")["items"][0]
        self.assertIsNone(it["position_size_usd"])
        self.assertEqual(it["yellow_state"], "DISCONNECTED")
        self.rep.delete_signup("cli", "G@x.test")
        self.assertEqual(self.rep.signups(q="g@x")["total"], 0)
        with self.assertRaises(ReportError):
            self.rep.delete_signup("cli", "g@x.test")

    def test_test_users_excluded(self):
        uid = str(uuid.uuid4())
        with H.db().service() as c:
            c.execute("insert into auth.users(id, email, raw_user_meta_data) values (%s, 'tst@x.test', '{\"why_test_user\": true}'::jsonb)", (uid,))
        H._CREATED_USERS.add(uid)
        self.assertEqual(self.rep.overview()["totals"]["signups"], 0)

    def test_partner_admin(self):
        out = self.rep.add_partner("admin@why", "bybit", "Bybit", "deal")
        self.assertEqual(out["link"], "https://whytradingbot.com/r/bybit")
        with self.assertRaises(ReportError):
            self.rep.add_partner("admin@why", "bybit", "Bybit")
        with self.assertRaises(ReportError):
            self.rep.add_partner("admin@why", "Bad Code!", "x")
        with self.assertRaises(ReportError):
            self.rep.add_partner("admin@why", "direct", "x")
        self.rep.update_partner("admin@why", "bybit", name="Bybit EU", active=False)
        by = {p["code"]: p for p in self.rep.partners()}
        self.assertEqual(by["bybit"]["name"], "Bybit EU")
        self.assertFalse(by["bybit"]["active"])
        self.assertTrue(any(a["action"] == "partner_updated" for a in self.rep.audit()))

    def test_team_users_and_passwords(self):
        out = self.rep.add_team_user("cli", "Team@Why.com", "T", "admin")
        self.assertEqual(out["email"], "team@why.com")
        self.assertGreaterEqual(len(out["password"]), 12)
        self.assertIsNone(self.rep.authenticate("team@why.com", "wrong"))
        u = self.rep.authenticate("team@why.com", out["password"])
        self.assertEqual(u["role"], "admin")
        with self.assertRaises(ReportError):
            self.rep.add_team_user("cli", "team@why.com", None, "member")
        new = self.rep.set_team_password("cli", "team@why.com")
        self.assertIsNone(self.rep.authenticate("team@why.com", out["password"]))
        self.assertIsNotNone(self.rep.authenticate("team@why.com", new["password"]))
        self.rep.set_team_active("cli", "team@why.com", False)
        self.assertIsNone(self.rep.authenticate("team@why.com", new["password"]))
        h = hash_password("abc")
        self.assertTrue(check_password("abc", h)); self.assertFalse(check_password("abd", h)); self.assertFalse(check_password("abc", "garbage"))


class SessionTest(unittest.TestCase):
    def test_roundtrip_expiry_tamper(self):
        now = [1000.0]
        s = Sessions(SECRET, 60, lambda: now[0])
        tok = s.issue({"id": "1", "email": "a@b", "role": "admin", "name": "A"})
        self.assertEqual(s.read(tok)["email"], "a@b")
        self.assertIsNone(s.read(tok[:-2] + "zz"))
        self.assertIsNone(Sessions("y" * 48, 60, lambda: now[0]).read(tok))
        now[0] += 61
        self.assertIsNone(s.read(tok))
        with self.assertRaises(ValueError):
            Sessions("short", 60)


class AppTest(unittest.TestCase):
    def setUp(self):
        H.reset_db()
        self.cfg = Config(db_url=H.DSN, session_secret=SECRET, cookie_secure=False)
        self.app = App(self.cfg, H.db())
        self.rep = self.app.reports
        self.pw = self.rep.add_team_user("cli", "admin@why.test", "Admin", "admin")["password"]
        self.mpw = self.rep.add_team_user("cli", "member@why.test", "M", "member")["password"]

    def _login(self, email, pw, prefix=""):
        h = {"X-Forwarded-Prefix": prefix} if prefix else {}
        r = self.app.handle("POST", "/login", h, urlencode({"email": email, "password": pw}).encode(), "1.2.3.4")
        return r

    def _cookie_of(self, r):
        for k, v in r.headers:
            if k == "Set-Cookie":
                return v.split(";")[0]
        return ""

    def test_login_flow_and_prefix(self):
        r = self.app.handle("GET", "/", {}, b"")
        self.assertEqual(r.status, 302)
        self.assertEqual(self.app.handle("GET", "/api/overview", {}, b"").status, 401)
        bad = self._login("admin@why.test", "nope")
        self.assertEqual(bad.status, 401)
        ok = self._login("admin@why.test", self.pw, prefix="/affiliate")
        self.assertEqual(ok.status, 302)
        loc = dict(ok.headers)["Location"]
        self.assertEqual(loc, "/affiliate/")
        ck = self._cookie_of(ok)
        self.assertTrue(ck.startswith(COOKIE + "="))
        self.assertIn("Path=/affiliate/", dict(ok.headers)["Set-Cookie"])
        page = self.app.handle("GET", "/", {"Cookie": ck}, b"")
        self.assertEqual(page.status, 200)
        self.assertIn(b"WHY Affiliate CRM", page.body)
        self.assertIn(b'"role": "admin"', page.body)
        ov = self.app.handle("GET", "/api/overview?days=14", {"Cookie": ck}, b"")
        self.assertEqual(ov.status, 200)
        self.assertEqual(json.loads(ov.body)["range_days"], 14)
        out = self.app.handle("POST", "/logout", {"Cookie": ck}, b"")
        self.assertIn("Max-Age=0", dict(out.headers)["Set-Cookie"])

    def test_login_rate_limit(self):
        for _ in range(self.cfg.login_rate_per_min):
            self._login("admin@why.test", "nope")
        self.assertEqual(self._login("admin@why.test", self.pw).status, 429)

    def test_mutations_need_admin_and_csrf(self):
        ck_m = self._cookie_of(self._login("member@why.test", self.mpw))
        ck_a = self._cookie_of(self._login("admin@why.test", self.pw))
        body = json.dumps({"code": "bybit", "name": "Bybit"}).encode()
        self.assertEqual(self.app.handle("POST", "/api/partners", {"Cookie": ck_a}, body).status, 403)                                      # no CSRF header
        self.assertEqual(self.app.handle("POST", "/api/partners", {"Cookie": ck_m, "X-Requested-With": "why-affiliate"}, body).status, 403)  # member
        r = self.app.handle("POST", "/api/partners", {"Cookie": ck_a, "X-Requested-With": "why-affiliate"}, body)
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.body)["link"], "https://whytradingbot.com/r/bybit")
        # member can still read
        self.assertEqual(self.app.handle("GET", "/api/partners", {"Cookie": ck_m}, b"").status, 200)
        # admin cannot deactivate self
        r = self.app.handle("POST", "/api/team/admin%40why.test", {"Cookie": ck_a, "X-Requested-With": "why-affiliate"}, json.dumps({"active": False}).encode())
        self.assertEqual(r.status, 422)
        # team add returns password once
        r = self.app.handle("POST", "/api/team", {"Cookie": ck_a, "X-Requested-With": "why-affiliate"}, json.dumps({"email": "new@why.test", "role": "member"}).encode())
        self.assertEqual(r.status, 200)
        self.assertIn("password", json.loads(r.body))

    def test_csv_and_signups(self):
        _user("z@x.test", "yellow", confirmed=True)
        ck = self._cookie_of(self._login("admin@why.test", self.pw))
        r = self.app.handle("GET", "/api/export.csv?partner=yellow", {"Cookie": ck}, b"")
        self.assertEqual(r.status, 200)
        self.assertIn(b"z@x.test,yellow", r.body)
        r = self.app.handle("GET", "/api/signups?partner=yellow&days=7", {"Cookie": ck}, b"")
        self.assertEqual(json.loads(r.body)["total"], 1)
        self.assertEqual(self.app.handle("GET", "/api/signups?partner=BAD!", {"Cookie": ck}, b"").status, 422)

    def test_unconfigured(self):
        app = App(Config(db_url=None, session_secret=""), None)
        r = app.handle("GET", "/", {}, b"")
        self.assertEqual(r.status, 503)
        self.assertEqual(app.handle("GET", "/health", {}, b"").status, 200)


if __name__ == "__main__":
    unittest.main()
