"""v2: P&L from tenant ledgers, revenue share, partner-scoped logins, password reset, alerts."""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.customer import harness as H                     # noqa: E402
from tests.affiliate.test_affiliate import _user, SECRET     # noqa: E402
from affiliate.alerts import Alerts                          # noqa: E402
from affiliate.config import Config                          # noqa: E402
from affiliate.pnl import PnlCache, read_ledger              # noqa: E402
from affiliate.reports import ReportError, Reports           # noqa: E402
from affiliate.service import App                            # noqa: E402
from execution.ledger import SCHEMA                          # noqa: E402


def make_ledger(path: str, trades):
    """trades: list of (direction, qty, entry, exit_or_None, fee_entry, fee_exit)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for i, (d, qty, ent, ex, fe, fx) in enumerate(trades):
        pid, ec, xc = f"p{i}", f"why-e{i}", f"why-x{i}"
        con.execute("insert into positions(position_id, account_id, market, direction, strategy, entry_coid, qty, entry_price, state, origin, opened_ts, updated_ts, state_ts) "
                    "values (?, 'acct', 'BTCUSDT-PERP', ?, 's', ?, ?, ?, ?, 'signal', 1, 1, ?)", (pid, d, ec, str(qty), str(ent), "CLOSED" if ex else "PROTECTED", 100 + i))
        con.execute("insert into orders(client_order_id, intent_key, signal_id, account_id, market, side, direction, order_type, purpose, strategy, qty, leverage, reduce_only, tif, state, filled_qty, avg_price, fees, position_id, created_ts, updated_ts, state_ts) "
                    "values (?, ?, 's', 'acct', 'BTCUSDT-PERP', 'buy', ?, 'market', 'ENTRY', 's', ?, 10, 0, 'IOC', 'FILLED', ?, ?, ?, ?, 1, 1, 1)", (ec, ec, d, str(qty), str(qty), str(ent), str(fe), pid))
        con.execute("insert into fills(trade_id, client_order_id, account_id, market, qty, price, fee, fee_currency, ts) values (?, ?, 'acct', 'BTCUSDT-PERP', ?, ?, ?, 'USDT', 1)", (f"t-e{i}", ec, str(qty), str(ent), str(fe)))
        if ex:
            con.execute("insert into orders(client_order_id, intent_key, signal_id, account_id, market, side, direction, order_type, purpose, strategy, qty, leverage, reduce_only, tif, state, filled_qty, avg_price, fees, position_id, created_ts, updated_ts, state_ts) "
                        "values (?, ?, 's', 'acct', 'BTCUSDT-PERP', 'sell', ?, 'limit', 'TARGET', 's', ?, 10, 1, 'GTC', 'FILLED', ?, ?, ?, ?, 1, 1, 1)", (xc, xc, d, str(qty), str(qty), str(ex), str(fx), pid))
            con.execute("insert into fills(trade_id, client_order_id, account_id, market, qty, price, fee, fee_currency, ts) values (?, ?, 'acct', 'BTCUSDT-PERP', ?, ?, ?, 'USDT', 2)", (f"t-x{i}", xc, str(qty), str(ex), str(fx)))
    con.commit(); con.close()


class PnlTest(unittest.TestCase):
    def test_closed_zero_quantity_entry_link_and_exchange_history(self):
        from affiliate.pnl import recent_closed
        from customer.result_history import match_history
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ledger.sqlite')
            make_ledger(path, [('long', '1.42', '14.172', '14.197', '0.008049696', '0.008063896')])
            with sqlite3.connect(path) as c:
                c.execute("update orders set position_id=null where purpose='ENTRY'")
                c.execute("update positions set qty='0'")
            row = recent_closed(path)[0]
            self.assertEqual(row['qty'], '1.42')
            self.assertAlmostEqual(row['pnl'], 0.019386408)
            self.assertAlmostEqual(float(read_ledger(path)['realized_pnl']), row['pnl'])
            h = dict(id='venue-position', market='BTCUSDT-PERP', direction='long', entry_price='14.172', max_held='1.42',
                     opened_at='1970-01-01T00:00:01Z', closed_at='1970-01-01T00:01:40Z',
                     realized_pnl='0.0355', total_trading_fee='0.016113592', net_funding_fee='-0.00145013752824497683',
                     exit_price='14.197', close_reason='normal')
            matched = match_history([row], [h])
            self.assertEqual(list(matched), ['p0'])
            self.assertEqual(match_history([row], [dict(h, max_held='2')]), {})
            self.assertEqual(match_history([row, dict(row, position_id='other')], [h]), {})
            self.assertEqual(match_history([row], [h, dict(h, id='other')]), {})
            with open(path + '.history.json', 'w') as f:
                json.dump(matched, f)
            self.assertAlmostEqual(recent_closed(path)[0]['pnl'], 0.017936270471755)
            self.assertAlmostEqual(float(read_ledger(path)['realized_pnl']), 0.017936270471755)

    def test_missing_exit_is_not_invented_and_admin_history_is_supported(self):
        from affiliate.pnl import recent_closed
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ledger.sqlite')
            make_ledger(path, [('short', '0.22', '90.307', None, '0.007947016', '0')])
            with sqlite3.connect(path) as c:
                c.execute("update positions set state='CLOSED', qty='0'")
                c.execute("update orders set position_id=null")
            self.assertIsNone(recent_closed(path)[0]['pnl'])
            self.assertEqual(read_ledger(path)['closed'], 1)
            with open(path + '.history.json', 'w') as f:
                json.dump({'p0': dict(realized_pnl='0', total_trading_fee='0.007947016',
                          net_funding_fee='0.0031826860345980461', exit_price='0', close_reason='admin_correct')}, f)
            row = recent_closed(path)[0]
            self.assertAlmostEqual(row['pnl'], -0.004764329965402)
            self.assertIsNone(row['exit_price'])
            self.assertEqual(row['reason'], 'admin_correct')

    def test_read_ledger_math(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ledger.sqlite")
            make_ledger(p, [("long", "0.01", "100000", "101000", "0.5", "0.5"),      # +10 - 1 = +9
                            ("short", "0.01", "100000", "101000", "0.5", "0.5"),     # -10 - 1 = -11
                            ("long", "0.02", "50000", None, "0.4", "0")])          # open
            r = read_ledger(p)
            self.assertEqual((r["closed"], r["wins"], r["losses"], r["open_positions"]), (2, 1, 1, 1))
            self.assertEqual(float(r["realized_pnl"]), -2.0)
            self.assertEqual(float(r["fees"]), 2.4)
            self.assertGreater(float(r["volume"]), 0)
            self.assertEqual(read_ledger(os.path.join(d, "nope.sqlite"))["closed"], 0)


class V2ReportsTest(unittest.TestCase):
    def setUp(self):
        H.reset_db()
        self.tmp = tempfile.TemporaryDirectory()
        self.rep = Reports(H.db(), "https://whytradingbot.com", pnl=PnlCache(self.tmp.name, ttl_s=0), default_rev_share=25.0)

    def tearDown(self):
        self.tmp.cleanup()

    def _connected_user(self, email, partner):
        ident = _user(email, partner, confirmed=True)
        svc = H.make_service("ok_scopes")[0]
        svc.get_me(ident)
        with H.db().service() as c:
            acct = c.execute("select id from public.yellow_accounts where user_id = %s", (ident.user_id,)).fetchone()["id"]
        return ident, str(acct)

    def test_pnl_revenue_and_partner_scope(self):
        _, a1 = self._connected_user("y1@x.test", "yellow")
        _, a2 = self._connected_user("d1@x.test", None)
        make_ledger(os.path.join(self.tmp.name, a1, "ledger.sqlite"), [("long", "0.01", "100000", "102000", "1", "1")])   # +20 - 2 = 18, fees 2
        make_ledger(os.path.join(self.tmp.name, a2, "ledger.sqlite"), [("long", "0.01", "100000", "99000", "2", "2")])    # -10 - 4 = -14, fees 4
        o = self.rep.overview(30)
        self.assertEqual(o["totals"]["closed_trades"], 2)
        self.assertAlmostEqual(o["totals"]["realized_pnl"], 4.0)
        self.assertAlmostEqual(o["totals"]["fees"], 6.0)
        self.assertAlmostEqual(o["totals"]["revenue"], 1.5)               # 25 % of 6
        by = {p["code"]: p for p in o["partners"]}
        self.assertAlmostEqual(by["yellow"]["revenue"], 0.5)
        self.assertAlmostEqual(by["direct"]["fees"], 4.0)
        # partner scope
        os_ = self.rep.overview(30, partner="yellow")
        self.assertEqual(os_["totals"]["signups"], 1)
        self.assertAlmostEqual(os_["totals"]["fees"], 2.0)
        self.assertEqual([p["code"] for p in os_["partners"]], ["yellow"])
        items = self.rep.signups(partner="yellow", mask_emails=True)["items"]
        self.assertEqual(items[0]["email"], "y1***@x.test")
        self.assertAlmostEqual(items[0]["realized_pnl"], 18.0)
        # rev share change
        self.rep.set_rev_share("admin", "yellow", 40)
        self.assertAlmostEqual({p["code"]: p for p in self.rep.partners()}["yellow"]["revenue"], 0.8)
        with self.assertRaises(ReportError):
            self.rep.set_rev_share("admin", "yellow", 120)
        self.assertIn("realized_pnl", self.rep.signups_csv(partner="yellow"))

    def test_partner_login_and_reset_tokens(self):
        with self.assertRaises(ReportError):
            self.rep.add_team_user("cli", "p@yellow.test", None, "partner")             # needs a partner code
        with self.assertRaises(ReportError):
            self.rep.add_team_user("cli", "p@yellow.test", None, "partner", partner_code="nope")
        out = self.rep.add_team_user("cli", "p@yellow.test", "Y", "partner", partner_code="yellow")
        u = self.rep.authenticate("p@yellow.test", out["password"])
        self.assertEqual((u["role"], u["partner_code"]), ("partner", "yellow"))
        rs = self.rep.create_reset("P@yellow.test")
        self.assertEqual(rs["email"], "p@yellow.test")
        self.assertIsNone(self.rep.create_reset("ghost@x.test"))
        self.assertEqual(self.rep.check_reset(rs["token"]), "p@yellow.test")
        self.assertIsNone(self.rep.check_reset("bogus"))
        with self.assertRaises(ReportError):
            self.rep.consume_reset(rs["token"], "short")
        self.assertEqual(self.rep.consume_reset(rs["token"], "a-much-longer-password"), "p@yellow.test")
        self.assertIsNone(self.rep.check_reset(rs["token"]))                         # single use
        self.assertIsNone(self.rep.authenticate("p@yellow.test", out["password"]))
        self.assertIsNotNone(self.rep.authenticate("p@yellow.test", "a-much-longer-password"))
        # change own password
        uid = u["id"]
        with self.assertRaises(ReportError):
            self.rep.change_password(uid, "wrong", "another-long-password")
        self.rep.change_password(uid, "a-much-longer-password", "another-long-password")
        self.assertIsNotNone(self.rep.authenticate("p@yellow.test", "another-long-password"))


class FakeTg:
    enabled = True
    def __init__(self): self.msgs = []
    def send(self, text): self.msgs.append(text); return 1


class FakeMail:
    enabled = True
    def __init__(self): self.sent = []
    def send(self, to, subject, html, text=None): self.sent.append((to, subject, text or html)); return True


class AlertsTest(unittest.TestCase):
    def setUp(self):
        H.reset_db()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(db_url=H.DSN, session_secret=SECRET, state_dir=self.tmp.name, alert_emails=["team@why.test"])
        self.tg, self.mail = FakeTg(), FakeMail()
        self.alerts = Alerts(H.db(), self.cfg, telegram=self.tg, mailer=self.mail)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_sets_watermark_then_reports_new_activity(self):
        _user("old@x.test", "yellow")
        self.assertEqual(self.alerts.poll(), [])                       # first run: no flood
        self.assertEqual(self.tg.msgs, [])
        ident = _user("new@x.test", "yellow")
        svc = H.make_service("ok_scopes")[0]
        svc.get_me(ident)
        svc.connect(ident, "K" * 40, "S" * 40)                          # credential_submitted (+ verified with FakeYellow ok_scopes)
        msgs = self.alerts.poll()
        self.assertTrue(any("New signup" in m and "new@x.test" in m and "yellow" in m for m in msgs))
        self.assertTrue(any("connected a Yellow API key" in m for m in msgs))
        self.assertEqual(len(self.tg.msgs), 1)
        self.assertEqual(self.mail.sent[0][0], ["team@why.test"])
        self.assertEqual(self.alerts.poll(), [])                       # nothing twice
        # watermark survives a restart
        again = Alerts(H.db(), self.cfg, telegram=self.tg, mailer=self.mail)
        self.assertEqual(again.poll(), [])


class V2AppTest(unittest.TestCase):
    def setUp(self):
        H.reset_db()
        self.mail = FakeMail()
        self.cfg = Config(db_url=H.DSN, session_secret=SECRET, cookie_secure=False, base_url="https://whytradingbot.com/affiliate")
        self.app = App(self.cfg, H.db(), mailer=self.mail, alerts=None)
        self.rep = self.app.reports
        self.apw = self.rep.add_team_user("cli", "admin@why.test", "A", "admin")["password"]
        self.ppw = self.rep.add_team_user("cli", "p@yellow.test", "P", "partner", partner_code="yellow")["password"]

    def _login(self, email, pw):
        r = self.app.handle("POST", "/login", {}, urlencode({"email": email, "password": pw}).encode(), "9.9.9.9")
        return next(v.split(";")[0] for k, v in r.headers if k == "Set-Cookie")

    def test_partner_scope_enforced(self):
        _user("y@x.test", "yellow"); _user("d@x.test", None)
        ck = self._login("p@yellow.test", self.ppw)
        page = self.app.handle("GET", "/", {"Cookie": ck}, b"")
        self.assertIn(b'"role": "partner"', page.body)
        ov = json.loads(self.app.handle("GET", "/api/overview", {"Cookie": ck}, b"").body)
        self.assertEqual(ov["scope"], "yellow"); self.assertEqual(ov["totals"]["signups"], 1)
        sg = json.loads(self.app.handle("GET", "/api/signups?partner=direct", {"Cookie": ck}, b"").body)    # cannot escape the scope
        self.assertEqual(sg["total"], 1); self.assertEqual(sg["items"][0]["email"], "y***@x.test")
        self.assertEqual(self.app.handle("GET", "/api/team", {"Cookie": ck}, b"").status, 403)
        self.assertEqual(self.app.handle("POST", "/api/partners", {"Cookie": ck, "X-Requested-With": "why-affiliate"}, b'{"code":"x","name":"x"}').status, 403)
        self.assertNotIn(b"d@x.test", self.app.handle("GET", "/api/export.csv", {"Cookie": ck}, b"").body)

    def test_forgot_reset_flow(self):
        r = self.app.handle("GET", "/forgot", {}, b"")
        self.assertEqual(r.status, 200)
        r = self.app.handle("POST", "/forgot", {"X-Forwarded-Prefix": "/affiliate"}, urlencode({"email": "admin@why.test"}).encode(), "1.1.1.1")
        self.assertEqual(r.status, 200); self.assertIn(b"on its way", r.body)
        self.assertEqual(len(self.mail.sent), 1)
        link = [w for w in self.mail.sent[0][2].split() if "reset?token=" in w][0]
        token = link.split("token=")[1]
        self.assertTrue(link.startswith("https://whytradingbot.com/affiliate/reset?token="))
        # unknown email: same answer, no mail
        r = self.app.handle("POST", "/forgot", {}, urlencode({"email": "nobody@why.test"}).encode(), "1.1.1.1")
        self.assertIn(b"on its way", r.body); self.assertEqual(len(self.mail.sent), 1)
        self.assertEqual(self.app.handle("GET", "/reset?token=bad", {}, b"").status, 400)
        r = self.app.handle("GET", f"/reset?token={token}", {}, b"")
        self.assertEqual(r.status, 200); self.assertIn(b"admin@why.test", r.body)
        r = self.app.handle("POST", "/reset", {}, urlencode({"token": token, "password": "brand-new-password-1", "password2": "nope"}).encode())
        self.assertEqual(r.status, 400)
        r = self.app.handle("POST", "/reset", {}, urlencode({"token": token, "password": "brand-new-password-1", "password2": "brand-new-password-1"}).encode())
        self.assertEqual(r.status, 200); self.assertIn(b"Password updated", r.body)
        self.assertIsNone(self.rep.authenticate("admin@why.test", self.apw))
        self.assertIsNotNone(self.rep.authenticate("admin@why.test", "brand-new-password-1"))
        self.assertEqual(self.app.handle("POST", "/reset", {}, urlencode({"token": token, "password": "brand-new-password-2", "password2": "brand-new-password-2"}).encode()).status, 400)

    def test_change_password_and_revshare_routes(self):
        ck = self._login("admin@why.test", self.apw)
        h = {"Cookie": ck, "X-Requested-With": "why-affiliate"}
        self.assertEqual(self.app.handle("POST", "/api/me/password", h, json.dumps({"current": "x", "new": "long-enough-password"}).encode()).status, 403)
        self.assertEqual(self.app.handle("POST", "/api/me/password", h, json.dumps({"current": self.apw, "new": "long-enough-password"}).encode()).status, 200)
        self.assertEqual(self.app.handle("POST", "/api/partners/yellow/revshare", h, json.dumps({"pct": 30}).encode()).status, 200)
        self.assertEqual(json.loads(self.app.handle("GET", "/api/partners", {"Cookie": ck}, b"").body)["items"][0]["rev_share_pct"], 30.0)
        r = self.app.handle("POST", "/api/team", h, json.dumps({"email": "p2@yellow.test", "role": "partner", "partner_code": "yellow"}).encode())
        self.assertEqual(r.status, 200); self.assertEqual(json.loads(r.body)["partner_code"], "yellow")


if __name__ == "__main__":
    unittest.main()
