"""Fixtures for the customer-platform suite. Needs the local test Postgres prepared once:

    sudo -u postgres psql -c "create role why_yellow_test login password 'why_yellow_test_pw' createrole bypassrls;"
    sudo -u postgres createdb -O why_yellow_test why_yellow_test
    sudo -u postgres psql -d why_yellow_test -c "create extension if not exists pgcrypto;"
    sudo -u postgres psql -c "alter database why_yellow_test set why.vault_shim_key = 'local-test-shim-key-not-production';"

The harness applies tests/customer/supabase_local_shim.sql (roles, auth.uid(), Vault API
stand-in) and the PRODUCTION migration, both idempotent. This suite talks to Postgres over
loopback, so it runs in its own process (tests/execution installs a socket-denying audit
hook). It never talks to Yellow: the verifier is a scripted fake."""
from __future__ import annotations
import http.client
import json
import os
import sys
import threading
import time
import uuid
from decimal import Decimal
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
assert "YELLOW_ARMED" not in os.environ, "tests must never run with YELLOW_ARMED set"

import jwt
import psycopg

from customer.accounts import AccountService
from customer.api import Api, create_server
from customer.auth import Identity, Verifier
from customer.config import Config
from customer.db import Database
from customer.readiness import Readiness
from customer.yellow_probe import VerifyResult

DSN = os.environ.get("WY_TEST_DB_URL", "postgresql://why_yellow_test:why_yellow_test_pw@127.0.0.1:5432/why_yellow_test")
# WY_TEST_HOSTED=1: run against a REAL Supabase project (DSN = SUPABASE_DB_URL). The local shim is
# NOT applied (it would overwrite auth.uid()/vault.*), the migration is assumed applied, and cleanup
# deletes only the throwaway auth.users rows this process created (never a truncate of auth.users).
HOSTED = os.environ.get("WY_TEST_HOSTED") == "1"
_CREATED_USERS: set = set()
if HOSTED:
    import atexit
    atexit.register(lambda: reset_db())   # leave no throwaway users behind on the real project
SUPA_URL = "https://test-project.supabase.co"
JWT_SECRET = "test-jwt-secret-not-production-0123456789"
ANON_KEY = "anon-key-public-test"
_DB: Optional[Database] = None
_SCHEMA_READY = False


def _apply_sql(path: str) -> None:
    with open(path) as f:
        sql = f.read()
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql)


def db() -> Database:
    global _DB, _SCHEMA_READY
    if _DB is None:
        _DB = Database(DSN)
    if not _SCHEMA_READY:
        here = os.path.dirname(os.path.abspath(__file__))
        if not HOSTED:
            _apply_sql(os.path.join(here, "supabase_local_shim.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20260923000000_yellow_customer.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20260930000000_affiliate.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20260930100000_affiliate_v2.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20260930200000_why_subscriptions.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20261001000000_public_saas.sql"))
        if not HOSTED:
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20261001020000_free_live_and_5000_paper.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20261001030000_paper_usd_25000.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20261006000000_yellow_position_limit.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20261006010000_yellow_trade_size.sql"))
            _apply_sql(os.path.join(ROOT, "supabase", "migrations", "20261011000000_trading_style.sql"))
        _SCHEMA_READY = True
    return _DB


def reset_db() -> None:
    if HOSTED:
        with db().service() as c:
            ids = list(_CREATED_USERS)
            if ids:
                accts = [r["id"] for r in c.execute("select id from public.yellow_accounts where user_id = any(%s::uuid[])", (ids,)).fetchall()]
                for a in accts:
                    c.execute("delete from vault.secrets where name like %s", (f"yellow:{a}:%",))
                c.execute("delete from public.yellow_accounts where user_id = any(%s::uuid[])", (ids,))
                c.execute("delete from auth.users where id = any(%s::uuid[])", (ids,))
        return
    with db().service() as c:
        c.execute("""truncate public.why_billing_events, public.why_paper_trades, public.why_paper_positions,
                     public.why_paper_accounts, public.why_paper_sessions, public.weex_credentials, public.weex_accounts,
                     public.yellow_account_events, public.yellow_bot_configs, public.yellow_credentials,
                     public.yellow_accounts, public.why_subscriptions, auth.users cascade""")
        c.execute("truncate public.affiliate_team_users, public.affiliate_audit, public.affiliate_partners, public.affiliate_password_resets cascade")
        c.execute("insert into public.affiliate_partners (code, name, created_by) values ('yellow', 'Yellow', 'migration')")
        c.execute("delete from vault.secrets")


def new_user(email: Optional[str] = None) -> Identity:
    uid = str(uuid.uuid4())
    email = email or f"{uid[:8]}@example.test"
    with db().service() as c:
        if HOSTED:
            c.execute("insert into auth.users(id, instance_id, aud, role, email, created_at, updated_at, raw_app_meta_data, raw_user_meta_data, is_sso_user) "
                      "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, now(), now(), '{\"provider\":\"email\",\"providers\":[\"email\"]}'::jsonb, '{\"why_test_user\":true}'::jsonb, false)", (uid, email))
        else:
            c.execute("insert into auth.users(id, email) values (%s, %s)", (uid, email))
    _CREATED_USERS.add(uid)
    return Identity(uid, email, "authenticated")


def make_config(**over) -> Config:
    base = dict(supabase_url=SUPA_URL, supabase_anon_key=ANON_KEY, supabase_jwt_secret=JWT_SECRET, supabase_db_url=DSN,
                state_dir=os.path.join("/tmp", f"why-cust-test-{os.getpid()}"), runner_enabled=False, feed_enabled=False, verify_rate_per_min=5)
    base.update(over)
    return Config(**base)


class FakeYellow:
    """Scripted stand-in for customer.yellow_probe.YellowVerifier. No network."""
    KEY_OK = "yk_" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    SECRET_OK = "ys_" + "0f1e2d3c4b5a69788796a5b4c3d2e1f0aabbccdd"

    def __init__(self, mode: str = "ok_scopes"):
        self.mode = mode
        self.calls = []

    def verify(self, api_key: str, api_secret: str) -> VerifyResult:
        self.calls.append((api_key, api_secret))
        m = self.mode
        if m == "unreachable":
            return VerifyResult(reachable=False, credential_ok=None, error_code="yellow_unreachable")
        if m == "invalid" or api_key.endswith("BAD"):
            return VerifyResult(reachable=True, credential_ok=False, error_code="invalid_credential:unauthorized")
        base = dict(reachable=True, credential_ok=True, app_session_id="0xabc0000000000000000000000000000000000001",
                    equity=Decimal("1234.56"), available=Decimal("1000.00"))
        if m == "ok_no_scopes":
            return VerifyResult(**base, permission_source="unavailable", detail={"permission_probe": "http_403:forbidden_auth_method"})
        if m == "forbidden":
            return VerifyResult(**base, permission_source="yellow_api", scopes=["read:futures", "trade:futures", "withdraw:spot"], forbidden=True)
        if m == "missing_trade":
            return VerifyResult(**base, permission_source="yellow_api", scopes=["read:futures"], missing_required=True)
        if m == "raise":
            raise RuntimeError("probe exploded with secret " + api_secret)
        return VerifyResult(**base, permission_source="yellow_api", scopes=["read:futures", "trade:futures"])


class RuntimeStub:
    def __init__(self):
        self.open = {}

    def __call__(self, account_id: str) -> dict:
        n = self.open.get(str(account_id), 0)
        return dict(loaded=True, open_positions=n, ambiguous_orders=0, venue="READY", positions=[])


def make_service(mode: str = "ok_scopes", runtime: Optional[RuntimeStub] = None):
    fake = FakeYellow(mode)
    svc = AccountService(db(), fake, runtime_info=runtime or RuntimeStub())
    return svc, fake


# ---------------------------------------------------------------- JWT helpers
def token(user_id: str, email: str = "u@example.test", ttl: int = 3600, role: str = "authenticated", aud="authenticated",
          iss: Optional[str] = None, secret: str = JWT_SECRET, alg: str = "HS256", key=None, headers=None, iat_offset: int = 0) -> str:
    now = int(time.time())
    claims = {"sub": user_id, "email": email, "role": role, "aud": aud, "iss": iss or f"{SUPA_URL}/auth/v1",
              "iat": now + iat_offset, "exp": now + iat_offset + ttl, "session_id": str(uuid.uuid4())}
    return jwt.encode(claims, key or secret, algorithm=alg, headers=headers)


def verifier(**kw) -> Verifier:
    return Verifier(SUPA_URL, JWT_SECRET, **kw)


# ---------------------------------------------------------------- API server
class ApiServer:
    def __init__(self, svc: Optional[AccountService], cfg: Optional[Config] = None, ver: Optional[Verifier] = None):
        self.cfg = cfg or make_config()
        from customer.results import ResultsReader
        from customer.subscriptions import SubscriptionService
        from customer.paper import PaperService
        subscriptions = SubscriptionService(db()) if svc else None
        self.api = Api(self.cfg, ver if ver is not None else (verifier() if svc else None), svc,
                       Readiness(self.cfg, db() if svc else None, yellow_probe=lambda: False, cache_s=0, auth_probe=lambda: (True, "test_auth_provider")),
                       subscriptions=subscriptions,
                       results=ResultsReader(os.path.join(self.cfg.state_dir, "tenants")) if svc else None,
                       paper=PaperService(db(), subscriptions) if svc else None)
        self.srv = create_server(self.api, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def call(self, method: str, path: str, tok: Optional[str] = None, body=None, raw: Optional[bytes] = None, headers: Optional[dict] = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        if headers:
            h.update(headers)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        text = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(text), text, dict(r.getheaders())
        except ValueError:
            return r.status, None, text, dict(r.getheaders())

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def scan_public_tables_for(value: str) -> list:
    """Every row of every public.yellow_* table rendered as text; returns hits."""
    hits = []
    with db().service() as c:
        tables = [r["tablename"] for r in c.execute("select tablename from pg_tables where schemaname='public' and tablename like 'yellow_%'").fetchall()]
        for t in tables:
            for r in c.execute(f"select t::text as row from public.{t} t").fetchall():
                if value in r["row"]:
                    hits.append((t, r["row"][:80]))
        for r in c.execute("select secret::text as raw, name from vault.secrets").fetchall():
            if value in (r["raw"] or ""):
                hits.append(("vault.secrets(raw ciphertext)", r["name"]))
    return hits
