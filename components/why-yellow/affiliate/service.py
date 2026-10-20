"""why-affiliate.service: team dashboard HTTP server (loopback :8109, behind nginx at
affiliate.whytradingbot.com and, until that DNS lands, whytradingbot.com/affiliate/).

Routes (all relative so the app works under a prefix or at a subdomain root):
  GET  /                     dashboard (login page when signed out)
  GET  /login  POST /login   team sign-in (form), POST /logout
  GET  /api/overview?days=   totals, daily series, partner table
  GET  /api/signups?partner=&q=&days=&limit=&offset=
  GET  /api/export.csv?partner=&q=&days=
  GET  /api/partners   POST /api/partners {code,name,notes}   POST /api/partners/<code> {name?,notes?,active?}
  GET  /api/team       POST /api/team {email,name,role}        POST /api/team/<email> {active?|reset_password}
  GET  /api/audit
  GET  /health
Mutations need role=admin and the X-Requested-With header (CSRF); the cookie is SameSite=Strict."""
from __future__ import annotations
import http.cookies
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from customer.db import Database                      # noqa: E402  (same pooled psycopg wrapper)
from customer.log import get_logger, safe_exc          # noqa: E402

from .alerts import Alerts                             # noqa: E402
from .auth import RateLimiter, Sessions                # noqa: E402
from .config import Config                             # noqa: E402
from .notify import Resend                             # noqa: E402
from .pnl import PnlCache                              # noqa: E402
from .reports import ReportError, Reports              # noqa: E402

log = get_logger("affiliate")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "why_aff_session"
MAX_BODY = 16 * 1024


def _read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class Response:
    def __init__(self, status: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8", headers: Optional[list] = None):
        self.status, self.body, self.ctype, self.headers = status, body, ctype, headers or []


def _json(status: int, payload, headers=None) -> Response:
    return Response(status, json.dumps(payload, default=str).encode(), headers=headers)


def _html(status: int, text: str, headers=None) -> Response:
    return Response(status, text.encode("utf-8"), "text/html; charset=utf-8", headers)


class App:
    def __init__(self, cfg: Config, db: Optional[Database], clock: Callable[[], float] = time.time, mailer: Optional[Resend] = None,
                 alerts: Optional[Alerts] = None):
        self.cfg, self.db, self.clock = cfg, db, clock
        self.pnl = PnlCache(cfg.tenants_dir)
        self.reports = Reports(db, cfg.public_base, pnl=self.pnl, default_rev_share=cfg.rev_share_pct) if db else None
        self.sessions = Sessions(cfg.session_secret, cfg.session_ttl_s, clock) if len(cfg.session_secret) >= 32 else None
        self.limiter = RateLimiter(cfg.login_rate_per_min, clock)
        self.mailer = mailer or Resend(cfg.resend_api_key, cfg.mail_from)
        self.alerts = alerts if alerts is not None else (Alerts(db, cfg, mailer=self.mailer, clock=clock) if db else None)
        self.started = clock()

    # ---- helpers
    def _cookie(self, value: str, prefix: str, max_age: int) -> str:
        c = http.cookies.SimpleCookie()
        c[COOKIE] = value
        c[COOKIE]["path"] = (prefix or "") + "/"
        c[COOKIE]["httponly"] = True
        c[COOKIE]["samesite"] = "Strict"
        c[COOKIE]["max-age"] = max_age
        if self.cfg.cookie_secure:
            c[COOKIE]["secure"] = True
        return c.output(header="").strip()

    def _who(self, headers) -> Optional[dict]:
        if not self.sessions:
            return None
        raw = headers.get("Cookie") or ""
        try:
            c = http.cookies.SimpleCookie()
            c.load(raw)
            tok = c[COOKIE].value if COOKIE in c else None
        except Exception:
            tok = None
        return self.sessions.read(tok)

    @staticmethod
    def _prefix(headers) -> str:
        p = (headers.get("X-Forwarded-Prefix") or "").strip().rstrip("/")
        return p if p.startswith("/") else ""

    @staticmethod
    def _body_json(body: bytes) -> dict:
        if not body:
            return {}
        try:
            d = json.loads(body.decode("utf-8"))
        except Exception:
            raise ReportError("bad_json", 400)
        if not isinstance(d, dict):
            raise ReportError("bad_json", 400)
        return d

    @staticmethod
    def _form(body: bytes) -> dict:
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}

    _schema_checked = 0.0
    _schema_ok = False

    def schema_ok(self) -> bool:
        """True once every affiliate migration is applied (re-checked every 30 s so a deploy needs no restart)."""
        if not self.db:
            return False
        if self.clock() - self._schema_checked < 30.0:
            return self._schema_ok
        ok = False
        try:
            with self.db.service() as c:
                n = c.execute("select count(*) as n from information_schema.columns where table_schema = 'public' and "
                              "((table_name = 'affiliate_partners' and column_name = 'rev_share_pct') or (table_name = 'affiliate_team_users' and column_name = 'partner_code') "
                              "or (table_name = 'affiliate_password_resets' and column_name = 'token_hash') or (table_name = 'yellow_accounts' and column_name = 'partner_code'))").fetchone()["n"]
            ok = int(n) == 4
        except Exception as e:
            log.warning("schema check: %s", safe_exc(e))
        self._schema_ok, self._schema_checked = ok, self.clock()
        return ok

    def health(self) -> dict:
        ok_db = False
        if self.db:
            try:
                with self.db.service() as c:
                    c.execute("select 1").fetchone()
                ok_db = True
            except Exception as e:
                log.warning("db health: %s", safe_exc(e))
        return {"APP_HEALTHY": True, "DB_HEALTHY": ok_db, "CONFIGURED": self.cfg.ready, "SCHEMA_OK": self.schema_ok(), "MAIL_CONFIGURED": self.mailer.enabled,
                "ALERTS": self.alerts.channels() if self.alerts else None, "uptime_s": int(self.clock() - self.started)}

    def _page(self, name: str, prefix: str, **vars_) -> str:
        t = _read(name).replace("{{PREFIX}}", prefix)
        for k, v in vars_.items():
            t = t.replace("{{" + k + "}}", v)
        return t.replace("{{ERROR}}", "").replace("{{INFO}}", "")

    # ---- routing
    def handle(self, method: str, path: str, headers, body: bytes, client_ip: str = "") -> Response:
        url = urlsplit(path)
        route = url.path.rstrip("/") or "/"
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        prefix = self._prefix(headers)
        try:
            if route == "/health" and method == "GET":
                return _json(200, self.health())
            if not self.cfg.ready or not self.reports:
                return _html(503, self._page("login.html", prefix, ERROR="The dashboard is not configured on this server (database or session secret missing)."))
            if not self.schema_ok():
                msg = "Database update pending: run  bash /opt/why-yellow/deploy/affiliate_deploy.sh  on the server (applies the affiliate migrations), then reload."
                return _json(503, {"error": "migration_pending"}) if route.startswith("/api") else _html(503, self._page("login.html", prefix, ERROR=msg))
            who = self._who(headers)
            # -------- public
            if route == "/login":
                if method == "GET":
                    if who:
                        return Response(302, headers=[("Location", (prefix or "") + "/")])
                    return _html(200, self._page("login.html", prefix))
                if method == "POST":
                    if not self.limiter.allow(f"login:{client_ip}"):
                        return _html(429, self._page("login.html", prefix, ERROR="Too many attempts. Wait a minute."))
                    f = self._form(body)
                    try:
                        user = self.reports.authenticate(f.get("email", ""), f.get("password", ""))
                    except Exception as e:                       # tables missing = migration not applied yet
                        if "affiliate_team_users" in str(e):
                            return _html(503, self._page("login.html", prefix, ERROR="Not deployed yet: run  bash /opt/why-yellow/deploy/affiliate_deploy.sh <your-email>  on the server (applies the database migration and creates the first login)."))
                        raise
                    if not user:
                        return _html(401, self._page("login.html", prefix, ERROR="Wrong email or password."))
                    tok = self.sessions.issue(user)
                    return Response(302, headers=[("Location", (prefix or "") + "/"), ("Set-Cookie", self._cookie(tok, prefix, self.cfg.session_ttl_s))])
            if route == "/forgot":
                if method == "GET":
                    return _html(200, self._page("forgot.html", prefix, INFO="" if self.mailer.enabled else "Email sending is not configured on this server yet; ask an admin to reset your password."))
                if not self.limiter.allow(f"forgot:{client_ip}"):
                    return _html(429, self._page("forgot.html", prefix, ERROR="Too many attempts. Wait a minute."))
                f = self._form(body)
                rs = self.reports.create_reset(f.get("email", "")) if self.mailer.enabled else None
                if rs:
                    link = f"{self.cfg.base_url}/reset?token={rs['token']}"
                    html_ = (f"<div style='font:15px/1.6 -apple-system,Segoe UI,sans-serif;color:#111'><p>Hi {rs['name'] or ''},</p>"
                             f"<p>Someone asked to reset the password of your WHY affiliate dashboard login ({rs['email']}).</p>"
                             f"<p><a href='{link}' style='background:#f5c400;color:#111;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600'>Set a new password</a></p>"
                             f"<p>The link works once and expires in 60 minutes. If this wasn't you, ignore this email.</p><p>{link}</p></div>")
                    ok = self.mailer.send([rs["email"]], "Reset your WHY dashboard password", html_, f"Reset link (60 min): {link}")
                    if not ok:
                        log.warning("reset mail send failed for %s", rs["email"])
                return _html(200, self._page("forgot.html", prefix, INFO="If that email belongs to a team or partner login, a reset link is on its way. Check spam too."))
            if route == "/reset":
                if method == "GET":
                    tok = q.get("token", "")
                    email = self.reports.check_reset(tok)
                    if not email:
                        return _html(400, self._page("reset.html", prefix, TOKEN="", EMAIL="", ERROR="This reset link is invalid or has expired. Request a new one."))
                    return _html(200, self._page("reset.html", prefix, TOKEN=tok, EMAIL=email))
                f = self._form(body)
                tok = f.get("token", "")
                if f.get("password", "") != f.get("password2", ""):
                    return _html(400, self._page("reset.html", prefix, TOKEN=tok, EMAIL=self.reports.check_reset(tok) or "", ERROR="Passwords don't match."))
                try:
                    email = self.reports.consume_reset(tok, f.get("password", ""))
                except ReportError as e:
                    msg = "Use at least 12 characters." if e.code == "password_too_short" else "This reset link is invalid or has expired. Request a new one."
                    return _html(400, self._page("reset.html", prefix, TOKEN=tok if e.code == "password_too_short" else "", EMAIL=self.reports.check_reset(tok) or "", ERROR=msg))
                return _html(200, self._page("login.html", prefix, INFO=f"Password updated for {email}. Sign in."))
            if route == "/logout" and method == "POST":
                return Response(302, headers=[("Location", (prefix or "") + "/login"), ("Set-Cookie", self._cookie("", prefix, 0))])
            # -------- authenticated
            if not who:
                if route.startswith("/api"):
                    return _json(401, {"error": "unauthorized"})
                return Response(302, headers=[("Location", (prefix or "") + "/login")])
            if route == "/" and method == "GET":
                return _html(200, _read("index.html").replace("{{PREFIX}}", prefix).replace("{{WHO}}", json.dumps(who)).replace("{{REVSHARE}}", str(self.cfg.rev_share_pct)))
            is_partner = who["role"] == "partner"
            scope = (who.get("partner_code") or "") if is_partner else None
            if is_partner and not scope:
                return _json(403, {"error": "partner_login_without_partner"}) if route.startswith("/api") else _html(403, "<h1>Partner login has no partner assigned</h1>")
            if route == "/api/me" and method == "GET":
                return _json(200, {"email": who["email"], "role": who["role"], "name": who.get("name"), "partner_code": who.get("partner_code")})
            if route == "/api/overview" and method == "GET":
                return _json(200, self.reports.overview(int(q.get("days") or 30), partner=scope))
            if route == "/api/signups" and method == "GET":
                return _json(200, self.reports.signups(partner=scope or q.get("partner") or None, q=q.get("q") or None,
                                                       days=int(q["days"]) if q.get("days") else None,
                                                       limit=int(q.get("limit") or 200), offset=int(q.get("offset") or 0), mask_emails=is_partner))
            if route == "/api/export.csv" and method == "GET":
                csv = self.reports.signups_csv(partner=scope or q.get("partner") or None, q=q.get("q") or None, days=int(q["days"]) if q.get("days") else None, mask_emails=is_partner)
                return Response(200, csv.encode("utf-8"), "text/csv; charset=utf-8", [("Content-Disposition", 'attachment; filename="why-signups.csv"')])
            if route == "/api/partners" and method == "GET":
                return _json(200, {"items": self.reports.partners(scope=scope)})
            if route == "/api/team" and method == "GET":
                return _json(403, {"error": "team_only"}) if is_partner else _json(200, {"items": self.reports.team()})
            if route == "/api/audit" and method == "GET":
                return _json(403, {"error": "team_only"}) if is_partner else _json(200, {"items": self.reports.audit(int(q.get("limit") or 50))})
            # -------- own password (any role) + CSRF header
            if method == "POST" and route == "/api/me/password":
                if (headers.get("X-Requested-With") or "") != "why-affiliate":
                    return _json(403, {"error": "csrf"})
                d = self._body_json(body)
                return _json(200, self.reports.change_password(who["uid"], d.get("current", ""), d.get("new", "")))
            # -------- mutations: admin + CSRF header
            if method == "POST" and route.startswith("/api/"):
                if (headers.get("X-Requested-With") or "") != "why-affiliate":
                    return _json(403, {"error": "csrf"})
                if who["role"] != "admin":
                    return _json(403, {"error": "admin_only"})
                d = self._body_json(body)
                actor = who["email"]
                if route == "/api/alerts/test":
                    return _json(200, self.alerts.test_message() if self.alerts else {"error": "alerts_unavailable"})
                if route == "/api/partners":
                    return _json(200, self.reports.add_partner(actor, d.get("code", ""), d.get("name", ""), d.get("notes")))
                if route.startswith("/api/partners/") and route.endswith("/revshare"):
                    code = route[len("/api/partners/"):-len("/revshare")]
                    return _json(200, self.reports.set_rev_share(actor, code, d.get("pct")))
                if route.startswith("/api/partners/"):
                    code = route[len("/api/partners/"):]
                    return _json(200, self.reports.update_partner(actor, code, d.get("name"), d.get("notes"), d.get("active")))
                if route == "/api/team":
                    return _json(200, self.reports.add_team_user(actor, d.get("email", ""), d.get("name"), d.get("role") or "member", partner_code=d.get("partner_code")))
                if route.startswith("/api/team/"):
                    email = route[len("/api/team/"):]
                    from urllib.parse import unquote
                    email = unquote(email)
                    if d.get("reset_password"):
                        return _json(200, self.reports.set_team_password(actor, email))
                    if "active" in d:
                        if email.lower() == actor.lower() and not d["active"]:
                            return _json(422, {"error": "cannot_deactivate_self"})
                        return _json(200, self.reports.set_team_active(actor, email, bool(d["active"])))
                    return _json(400, {"error": "nothing_to_do"})
            return _json(404, {"error": "not_found"}) if route.startswith("/api") else _html(404, "<h1>404</h1>")
        except ReportError as e:
            return _json(e.status, {"error": e.code})
        except ValueError:
            return _json(400, {"error": "bad_request"})
        except Exception as e:
            log.error("unhandled %s %s: %s", method, route, safe_exc(e))
            return _json(500, {"error": "internal_error"})


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WHYAffiliate/1"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def _send(self, r: Response):
            self.send_response(r.status)
            self.send_header("Content-Type", r.ctype)
            self.send_header("Content-Length", str(len(r.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            for k, v in r.headers:
                self.send_header(k, v)
            self.end_headers()
            if r.body:
                self.wfile.write(r.body)

        def _ip(self) -> str:
            return (self.headers.get("X-Real-IP") or self.client_address[0] or "").strip()

        def do_GET(self):
            self._send(app.handle("GET", self.path, self.headers, b"", self._ip()))

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                self._send(_json(413, {"error": "body_too_large"}))
                return
            self._send(app.handle("POST", self.path, self.headers, self.rfile.read(n) if n else b"", self._ip()))

    return Handler


def create_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(app))
    srv.daemon_threads = True
    return srv


def main() -> int:
    cfg = Config.from_env()
    db = Database(cfg.db_url, max_conn=3) if cfg.db_url else None
    app = App(cfg, db)
    if app.alerts:
        app.alerts.schema_gate = app.schema_ok
        app.alerts.start()
    srv = create_server(app, cfg.host, cfg.port)
    stop = threading.Event()

    def _sig(*_):
        stop.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log.info("affiliate dashboard on %s:%s configured=%s mail=%s alerts=%s", cfg.host, cfg.port, cfg.ready, app.mailer.enabled, app.alerts.channels() if app.alerts else None)
    srv.serve_forever()
    if db:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
