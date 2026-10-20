"""Customer HTTP API (loopback, behind nginx at /yellow/api/). Standard library server,
JSON only, bearer-token auth (Supabase access token) on every /me endpoint.

Hard rules enforced here:
  * identity comes from the verified JWT; any client-supplied identity field is rejected;
  * responses never contain secrets (the service layer never returns them; the log
    redactor covers the rest);
  * errors are sanitized: clients get a code, logs get type + scrubbed message;
  * Cache-Control: no-store, X-Content-Type-Options: nosniff on every response;
  * bodies are capped at 8 KiB; connect/verify are rate limited per user."""
from __future__ import annotations
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .accounts import AccountError, AccountService, reject_client_identity
from .auth import AuthError, Identity, Verifier, bearer_token
from .log import get_logger, safe_exc

log = get_logger("api")
PREFIX = "/yellow/api"
MAX_BODY = 8 * 1024
MAX_WEBHOOK_BODY = 256 * 1024


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.time):
        self.n, self.clock = int(per_minute), clock
        self.hits: Dict[str, list] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self.lock:
            h = [t for t in self.hits.get(key, []) if now - t < 60.0]
            if len(h) >= self.n:
                self.hits[key] = h
                return False
            h.append(now)
            self.hits[key] = h
            return True


class Api:
    def __init__(self, cfg, verifier: Optional[Verifier], accounts: Optional[AccountService], readiness, registry=None,
                 clock: Callable[[], float] = time.time, subscriptions=None, results=None, paper=None, weex=None, billing=None):
        self.cfg, self.verifier, self.accounts, self.readiness, self.registry, self.clock = cfg, verifier, accounts, readiness, registry, clock
        # SaaS profile layer (2026-09-30), both optional and additive: the WHY plan
        # (customer.subscriptions) and the read-only trade results (customer.results).
        self.subscriptions, self.results = subscriptions, results
        self.paper, self.weex, self.billing = paper, weex, billing
        self.limiter = RateLimiter(cfg.verify_rate_per_min, clock)

    # ---- routing
    def handle(self, method: str, path: str, headers, body: bytes) -> Tuple[int, dict]:
        url = urlsplit(path)
        p = url.path.rstrip("/")
        if not p.startswith(PREFIX):
            return 404, {"error": "not_found"}
        route = p[len(PREFIX):] or "/"
        try:
            if method == "GET" and route == "/health":
                return 200, self.readiness.snapshot()
            if method == "GET" and route == "/public-config":
                return 200, self.cfg.public_view()
            if method == "POST" and route == "/billing/webhook":
                if self.billing is None:
                    return 503, {"error": "billing_not_configured"}
                return 200, self.billing.webhook(body, headers.get("Stripe-Signature", ""))
            if not self.accounts or not self.verifier:
                return 503, {"error": "supabase_not_configured"}
            # -------- authenticated
            if any(k.lower() in ("user_id", "account_id", "tenant", "tenant_id", "uid") for k in parse_qs(url.query)):
                return 400, {"error": "client_supplied_identity_rejected"}
            ident = self.verifier.verify(bearer_token(headers))
            data = self._json(body) if method == "POST" else {}
            reject_client_identity(data)
            if self.subscriptions is not None:
                self.subscriptions.ensure(ident)
            if method == "GET" and route == "/me":
                return 200, self.accounts.get_me(ident)
            if method == "POST" and route == "/me/yellow/connect":
                if self.subscriptions is not None:
                    ent = self.subscriptions.entitlement(ident.user_id)
                    if not ent["live_allowed"]:
                        return 403, {"error": "live_subscription_required"}
                    with self.accounts.db.service() as c:
                        yellow = c.execute("select state from public.yellow_accounts where user_id=%s", (ident.user_id,)).fetchone()
                        weex_n = c.execute("select count(*) n from public.weex_accounts where user_id=%s and state<>'DISCONNECTED'", (ident.user_id,)).fetchone()["n"]
                    if ent["exchange_limit"] is not None and weex_n + (0 if yellow and yellow["state"] != "DISCONNECTED" else 1) > ent["exchange_limit"]:
                        return 409, {"error": "exchange_limit_reached", "detail": {"limit": ent["exchange_limit"]}}
                if not self.limiter.allow(f"connect:{ident.user_id}"):
                    return 429, {"error": "rate_limited"}
                return 200, self.accounts.connect(ident, data.get("api_key"), data.get("api_secret"))
            if method == "POST" and route == "/me/yellow/verify":
                if not self.limiter.allow(f"verify:{ident.user_id}"):
                    return 429, {"error": "rate_limited"}
                return 200, self.accounts.verify(ident)
            if method == "POST" and route == "/me/yellow/disconnect":
                return 200, self.accounts.disconnect(ident, confirm_unmanaged=bool(data.get("confirm_unmanaged")))
            if method == "POST" and route == "/me/trading-style":
                result = self.accounts.set_trading_style(ident, data.get('trading_style'))
                if self.registry: self.registry.refresh()
                return 200, result
            if method == "POST" and route == "/me/bot/trade-size":
                return 200, self.accounts.set_trade_size(ident, data)
            if method == "POST" and route == "/me/bot/position-limit":
                return 200, self.accounts.set_position_limit(ident, data.get("max_open_positions"))
            if method == "POST" and route == "/me/bot/size":
                return 200, self.accounts.set_position_size(ident, data.get("position_size_usd"))
            if method == "POST" and route == "/me/bot/size/cancel":
                return 200, self.accounts.cancel_size_change(ident)
            if method == "POST" and route == "/me/bot/activate":
                if self.subscriptions is not None and not self.subscriptions.entitlement(ident.user_id)["live_allowed"]:
                    return 403, {"error": "live_subscription_required"}
                result = self.accounts.activate(ident, data.get('trading_style'))
                if self.registry: self.registry.refresh()
                return 200, result
            if method == "POST" and route == "/me/positions/close":
                from uuid import UUID
                venue, pid = data.get("venue"), data.get("position_id")
                if data.get("confirm") is not True:
                    return 422, {"error": "close_confirmation_required"}
                if venue not in ("yellow", "weex", "paper") or not isinstance(pid, str) or not 1 <= len(pid) <= 160:
                    return 422, {"error": "invalid_position"}
                if venue == "paper":
                    if self.paper is None:
                        return 503, {"error": "execution_unavailable"}
                    try: UUID(pid)
                    except ValueError: return 422, {"error": "invalid_position"}
                    result = self.paper.close_position(ident, pid)
                else:
                    if self.registry is None:
                        return 503, {"error": "execution_unavailable"}
                    # Resolve ownership from the JWT and current account state.
                    table = "yellow_accounts" if venue == "yellow" else "weex_accounts"
                    with self.accounts.db.as_user(ident.user_id, ident.email) as c:
                        account = c.execute(f"select state from public.{table}").fetchone()
                    if not account or account["state"] == "DISCONNECTED":
                        return 409, {"error": "exchange_disconnected"}
                    result = self.registry.close_for_user(ident, venue, pid)
                if result not in ("CLOSED", "ALREADY_CLOSED", "CLOSE_SUBMITTED", "CLOSE_PENDING"):
                    return 409, {"error": "position_close_incomplete", "result": result}
                return 200, {"result": result}
            if method == "POST" and route == "/me/bot/pause":
                result = self.accounts.pause(ident)
                if self.registry: self.registry.refresh()
                return 200, result
            # -------- SaaS profile layer (additive): plan + read-only results
            if route == "/me/subscription" and self.subscriptions is not None:
                if method == "GET":
                    return 200, self.subscriptions.get(ident)
            if method == "POST" and route == "/billing/checkout":
                return 503, {"error": "payments_coming_soon"}
            if self.paper is not None:
                if method == "GET" and route == "/me/paper":
                    try: limit = int((parse_qs(url.query).get("limit") or ["50"])[0])
                    except ValueError: limit = 50
                    return 200, self.paper.get(ident, limit)
                if method == "POST" and route == "/me/paper/settings":
                    return 200, self.paper.settings(ident, data)
                if method == "POST" and route == "/me/paper/start":
                    return 200, self.paper.set_running(ident, True)
                if method == "POST" and route == "/me/paper/stop":
                    return 200, self.paper.set_running(ident, False)
                if method == "POST" and route == "/me/paper/reset":
                    return 200, self.paper.reset(ident)
            if self.weex is not None:
                if method == "GET" and route == "/me/weex":
                    return 200, self.weex.get(ident)
                if method == "POST" and route == "/me/weex/connect":
                    if not self.limiter.allow(f"weex-connect:{ident.user_id}"):
                        return 429, {"error": "rate_limited"}
                    return 200, self.weex.connect(ident, data.get("api_key"), data.get("api_secret"), data.get("api_passphrase"))
                if method == "POST" and route == "/me/weex/settings":
                    return 200, self.weex.settings(ident, data)
                if method == "POST" and route == "/me/weex/start":
                    with self.accounts.db.as_user(ident.user_id, ident.email) as c:
                        style = c.execute("select trading_style from public.yellow_bot_configs").fetchone()
                    if style and style['trading_style'] == 'scalp':
                        return 409, {'error': 'scalp_yellow_only'}
                    return 200, self.weex.set_running(ident, True)
                if method == "POST" and route == "/me/weex/stop":
                    return 200, self.weex.set_running(ident, False)
                if method == "POST" and route == "/me/weex/disconnect":
                    return 200, self.weex.disconnect(ident, bool(data.get("confirm_unmanaged")))
            if method == "GET" and route == "/me/trades" and self.results is not None:
                me = self.accounts.get_me(ident)
                try:
                    limit = max(1, min(100, int((parse_qs(url.query).get("limit") or ["20"])[0])))
                except ValueError:
                    limit = 20
                if me['config']['trading_style'] == 'scalp' and self.registry and self.registry.scalp:
                    result = self.registry.scalp.results_for_user(ident, limit)
                    if result is not None: return 200, result
                return 200, dict(account_id=me["account"]["id"], mode="live", **self.results.for_account(me["account"]["id"], limit=limit))
            return 404, {"error": "not_found"}
        except AuthError as e:
            return e.status, {"error": "unauthorized", "code": e.code}
        except AccountError as e:
            return e.status, {"error": e.code, **({"detail": e.detail} if e.detail else {})}
        except ValueError as e:
            return 400, {"error": "bad_request"}
        except Exception as e:
            log.error("unhandled %s %s: %s", method, route, safe_exc(e))
            return 500, {"error": "internal_error"}

    @staticmethod
    def _json(body: bytes) -> dict:
        if not body:
            return {}
        try:
            d = json.loads(body.decode("utf-8"))
        except Exception:
            raise ValueError("bad_json")
        if not isinstance(d, dict):
            raise ValueError("bad_json")
        return d


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WHYYellow/1"
        sys_version = ""

        def log_message(self, fmt, *args):   # never log query strings / headers
            pass

        def _respond(self, status: int, payload: dict):
            b = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(b)

        def _body(self) -> Optional[bytes]:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            cap = MAX_WEBHOOK_BODY if self.path.split("?", 1)[0].rstrip("/").endswith("/billing/webhook") else MAX_BODY
            if n > cap:
                return None
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            status, payload = api.handle("GET", self.path, self.headers, b"")
            self._respond(status, payload)

        def do_POST(self):
            body = self._body()
            if body is None:
                self._respond(413, {"error": "body_too_large"})
                return
            status, payload = api.handle("POST", self.path, self.headers, body)
            self._respond(status, payload)

    return Handler


def create_server(api: Api, host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(api))
    srv.daemon_threads = True
    return srv
