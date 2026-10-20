#!/usr/bin/env python3
"""WHY Partners portal — read-only HTTP service on 127.0.0.1:8099.

Serves the login page, the dashboard shell and the authenticated JSON API
under /partners. Everything it knows about the book comes from book.py, which
reads two engine files O_RDONLY. There is no code path here that writes to
anything but its own SQLite (accounts/sessions). Stdlib only + bcrypt.
"""
from __future__ import annotations

import html
import http.cookies
import json
import mimetypes
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import auth  # noqa: E402
import book  # noqa: E402
import mailer  # noqa: E402
import exch  # noqa: E402

BIND = (os.environ.get("PARTNERS_BIND", "127.0.0.1"), int(os.environ.get("PARTNERS_PORT", "8099")))
PREFIX = "/partners"
# Public origin of the portal, used only to build links inside emails. Behind
# nginx the request Host is trustworthy, but a mail link is worth pinning.
BASE_URL = os.environ.get("PARTNERS_BASE_URL", "https://whytradingbot.com").rstrip("/") + PREFIX
STATIC = os.path.join(HERE, "static")
COOKIE = "why_partner"
COOKIE_SECURE = os.environ.get("PARTNERS_COOKIE_SECURE", "1") != "0"
# PARTNERS_AUTH=0 opens the portal without login (owner's call 2026-08-18,
# "remove the login for now"). Every auth path stays in place; flip to 1 (or
# unset) and restart to require invited accounts again.
AUTH_REQUIRED = os.environ.get("PARTNERS_AUTH", "1") != "0"
MAX_BODY = 8192
STATIC_FILES = {"app.css", "app.js", "brand.svg"}   # allow-list, no directory walking

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _log(**kw):
    kw.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    print(json.dumps(kw), flush=True)


class H(BaseHTTPRequestHandler):
    server_version = "why-partners"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # our own JSON lines only
        pass

    # --- helpers ---------------------------------------------------------------
    def _ip(self) -> str:
        return (self.headers.get("X-Real-IP") or self.client_address[0] or "?")[:64]

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = http.cookies.SimpleCookie(); c.load(raw)
            m = c.get(COOKIE)
            return m.value if m else None
        except Exception:
            return None

    def _set_cookie(self, token: str | None):
        c = http.cookies.SimpleCookie()
        c[COOKIE] = token or ""
        m = c[COOKIE]
        m["path"] = PREFIX
        m["httponly"] = True
        m["samesite"] = "Strict"
        if COOKIE_SECURE:
            m["secure"] = True
        m["max-age"] = auth.SESSION_MAX_SEC if token else 0
        self.send_header("Set-Cookie", m.OutputString())

    def _headers(self, code: int, ctype: str, length: int, private: bool = True, cache: str | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache or ("no-store, private" if private else "no-cache"))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _json(self, code: int, obj, cookie: str | None = "__unset__"):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self._headers(code, "application/json; charset=utf-8", len(body))
        if cookie != "__unset__":
            self._set_cookie(cookie)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, body: bytes, ctype: str, private=True, cache=None):
        self._headers(code, ctype, len(body), private, cache)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _user(self):
        if not AUTH_REQUIRED:
            return {"username": "open", "display_name": ""}
        return auth.session_user(self._cookie())

    def _body_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return None
        try:
            d = json.loads(self.rfile.read(n))
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    def _same_origin(self) -> bool:
        """Login/logout accept only same-origin fetches (CSRF: SameSite=Strict
        already blocks cross-site cookies; this also refuses cross-site posts
        that carry no cookie but would burn the rate limiter)."""
        o = self.headers.get("Origin") or ""
        h = self.headers.get("Host") or ""
        if not o:
            return True
        return urllib.parse.urlsplit(o).netloc == h

    def _file(self, name: str, private=False):
        if name not in STATIC_FILES:
            return self._text(404, b"not found", "text/plain")
        p = os.path.join(STATIC, name)
        with open(p, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript") or ctype.endswith("svg+xml"):
            ctype += "; charset=utf-8"
        # Static shell only — no partner data inside; short cache is fine.
        self._text(200, data, ctype, private=False, cache="private, max-age=300")

    def _page(self, name: str):
        with open(os.path.join(STATIC, name), "rb") as fh:
            self._text(200, fh.read(), "text/html; charset=utf-8", private=True)

    # --- routing --------------------------------------------------------------
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path = u.path
        if path == PREFIX or path == PREFIX + "/":
            if not self._user():
                return self._redirect(PREFIX + "/login")
            return self._page("index.html")
        if path == PREFIX + "/login":
            if not AUTH_REQUIRED or self._user():
                return self._redirect(PREFIX)
            return self._page("login.html")
        if path in (PREFIX + "/signup", PREFIX + "/verify"):
            if not AUTH_REQUIRED or self._user():
                return self._redirect(PREFIX)
            return self._page("signup.html")
        if path.startswith(PREFIX + "/static/"):
            return self._file(path[len(PREFIX) + 8:])
        if path.startswith(PREFIX + "/api/"):
            return self._api_get(path[len(PREFIX) + 5:], urllib.parse.parse_qs(u.query))
        return self._text(404, b"not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        if not u.path.startswith(PREFIX + "/api/"):
            return self._text(404, b"not found", "text/plain")
        ep = u.path[len(PREFIX) + 5:]
        if not self._same_origin():
            return self._json(403, {"error": "forbidden"})
        if ep == "login":
            d = self._body_json() or {}
            token, why = auth.login(str(d.get("username", ""))[:120], str(d.get("password", ""))[:256], self._ip())
            _log(event="login", ok=bool(token), why=why, ip=self._ip())
            if not token:
                time.sleep(0.4)
                return self._json(401, {"error": "Invalid credentials."}, cookie=None)
            return self._json(200, {"ok": True}, cookie=token)
        if ep == "logout":
            auth.logout(self._cookie())
            return self._json(200, {"ok": True}, cookie=None)
        if ep == "signup":
            d = self._body_json() or {}
            ex_block = None
            ex_in = d.get("exchange") if isinstance(d.get("exchange"), dict) else None
            if ex_in and (ex_in.get("api_key") or ex_in.get("api_secret")):
                ok, status, meta = exch.validate(str(ex_in.get("exchange", ""))[:20], str(ex_in.get("api_key", ""))[:256],
                                                 str(ex_in.get("api_secret", ""))[:256])
                if not ok:
                    return self._json(400, {"error": status})
                ex_block = {"sealed": exch.seal(str(ex_in.get("api_key")), str(ex_in.get("api_secret"))),
                            "exchange": str(ex_in.get("exchange")).lower(), "status": status, "verified": meta.get("verified")}
            code, why = auth.start_signup(str(d.get("email", ""))[:120], str(d.get("password", ""))[:256],
                                          str(d.get("name", ""))[:80], self._ip(), exchange=ex_block)
            _log(event="signup", why=why, ip=self._ip())
            if why == "bad_email":
                return self._json(400, {"error": "Enter a valid email address."})
            if why == "weak_password":
                return self._json(400, {"error": f"Password must be at least {auth.MIN_PW} characters."})
            if why == "rate_limited":
                return self._json(429, {"error": "Too many attempts. Try again later."})
            # already_registered and ok answer IDENTICALLY so the form cannot be
            # used to enumerate partner emails. The difference is delivered to
            # the mailbox instead, which only the real owner can read — without
            # it, an existing partner is told to check their inbox for a code
            # that was never generated, and concludes signup is broken.
            if why == "already_registered":
                em = str(d.get("email", "")).strip()
                if not mailer.send_exists(em, BASE_URL):
                    _log(event="signup.exists_undelivered", email=auth._norm(em))
            if code:
                sent = mailer.send_code(str(d.get("email", "")).strip(), code)
                if not sent:
                    # No mailer configured (or delivery failed): the operator
                    # reads the code from the journal or approves via partnersctl.
                    _log(event="signup.code_undelivered", email=auth._norm(str(d.get("email", ""))), code=code)
            time.sleep(0.3)
            return self._json(200, {"ok": True, "message": "If that address is eligible, a 6-digit code has been sent. Enter it to activate your account.",
                                    "mail_configured": mailer.configured()})
        if ep in ("exchange", "exchange/remove"):
            user = self._user()
            if not user:
                return self._json(401, {"error": "unauthorized"})
            if ep == "exchange/remove":
                auth.exchange_remove(user["username"])
                return self._json(200, {"ok": True, "exchange": None})
            d = self._body_json() or {}
            ok, status = auth.exchange_set(user["username"], str(d.get("exchange", ""))[:20],
                                           str(d.get("api_key", ""))[:256], str(d.get("api_secret", ""))[:256])
            _log(event="exchange.set", ok=ok, user=user["username"], exchange=str(d.get("exchange", ""))[:20])
            if not ok:
                return self._json(400, {"error": status})
            return self._json(200, {"ok": True, "exchange": auth.exchange_get(user["username"])})
        if ep == "verify":
            d = self._body_json() or {}
            token, why = auth.verify_signup(str(d.get("email", ""))[:120], str(d.get("code", ""))[:12], self._ip())
            _log(event="verify", ok=bool(token), why=why, ip=self._ip())
            if not token:
                time.sleep(0.4)
                return self._json(401, {"error": "Invalid or expired code."})
            return self._json(200, {"ok": True}, cookie=token)
        return self._json(404, {"error": "not found"})

    def _api_get(self, ep: str, q: dict):
        user = self._user()
        if ep == "session":
            if not user:
                return self._json(401, {"authenticated": False})
            return self._json(200, {"authenticated": True, "user": user["display_name"],
                                    "role": user.get("role", "partner"), "auth_required": AUTH_REQUIRED})
        if not user:
            return self._json(401, {"error": "unauthorized"})
        try:
            snap = book.snapshot()
        except Exception as e:  # never leak paths/tracebacks
            _log(event="book.error", err=type(e).__name__)
            return self._json(503, {"error": "book temporarily unavailable"})
        base = {"ts": snap["ts"], "paper": True, "since_iso": snap["since_iso"]}
        if ep == "summary":
            return self._json(200, {**base, "summary": snap["summary"], "notes": snap["notes"],
                                    "clip_usd": snap["clip_usd"], "geometry": snap["geometry"]})
        if ep == "open":
            return self._json(200, {**base, "open": snap["open"]})
        if ep == "trades":
            return self._json(200, {**base, "trades": snap["trades"], "notes": snap["notes"]})
        if ep == "equity":
            return self._json(200, {**base, "equity": snap["equity"], "capital_usd": snap["summary"]["capital_usd"]})
        if ep == "exchange":
            return self._json(200, {"exchange": auth.exchange_get(user["username"]), "supported": exch.SUPPORTED, "note": exch.NOTE})
        if ep == "prices":
            syms = {r["symbol"] for r in snap["open"]}
            return self._json(200, {"ts": time.time(), "prices": book.prices(syms)})
        return self._json(404, {"error": "not found"})


def main() -> int:
    auth.init_db()
    auth.purge_expired()
    srv = ThreadingHTTPServer(BIND, H)
    srv.daemon_threads = True
    _log(event="start", bind=f"{BIND[0]}:{BIND[1]}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
