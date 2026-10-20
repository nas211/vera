"""Invite-only accounts + sessions for the Partners portal. SQLite, bcrypt.

No signup path exists anywhere: accounts are created only by partnersctl.py on
the box. Sessions are random 256-bit tokens stored HASHED (sha256) so a copy of
the DB cannot be replayed; the cookie is the only place the raw token lives.
Login is generic on failure (same message, same timing class) and rate-limited
per user AND per IP. Session is rotated on every login (no fixation).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time

import bcrypt

DB_PATH = os.environ.get("PARTNERS_DB", "/var/lib/why-partners/partners.db")
SESSION_IDLE_SEC = int(os.environ.get("PARTNERS_SESSION_IDLE_SEC", str(12 * 3600)))
SESSION_MAX_SEC = int(os.environ.get("PARTNERS_SESSION_MAX_SEC", str(7 * 24 * 3600)))
LOGIN_MAX_FAILS = int(os.environ.get("PARTNERS_LOGIN_MAX_FAILS", "6"))
LOGIN_WINDOW_SEC = int(os.environ.get("PARTNERS_LOGIN_WINDOW_SEC", "900"))
LOGIN_LOCK_SEC = int(os.environ.get("PARTNERS_LOGIN_LOCK_SEC", "900"))
# A constant hash to burn the same bcrypt cost on unknown users as on known
# ones, so "no such account" and "wrong password" take the same time.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(12))

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS partners(
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
          display_name TEXT, pw_hash BLOB NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          created_ts REAL NOT NULL, last_login_ts REAL, role TEXT NOT NULL DEFAULT 'partner');
        CREATE TABLE IF NOT EXISTS signups(
          email TEXT PRIMARY KEY COLLATE NOCASE, display_name TEXT, pw_hash BLOB NOT NULL,
          code_hash TEXT NOT NULL, created_ts REAL NOT NULL, expires_ts REAL NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, ip TEXT, ex_json BLOB, ex_exchange TEXT, ex_status TEXT, ex_verified INTEGER);
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, partner_id INTEGER NOT NULL,
          created_ts REAL NOT NULL, seen_ts REAL NOT NULL, ip TEXT);
        CREATE TABLE IF NOT EXISTS login_fails(
          key TEXT NOT NULL, ts REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS lf_key ON login_fails(key, ts);
        """)
        # migration for DBs created before `role` existed
        cols = [r[1] for r in c.execute("PRAGMA table_info(partners)")]
        if "role" not in cols:
            c.execute("ALTER TABLE partners ADD COLUMN role TEXT NOT NULL DEFAULT 'partner'")
        scols = [r[1] for r in c.execute("PRAGMA table_info(signups)")]
        for col, typ in (("ex_json", "BLOB"), ("ex_exchange", "TEXT"), ("ex_status", "TEXT"), ("ex_verified", "INTEGER")):
            if col not in scols:
                c.execute(f"ALTER TABLE signups ADD COLUMN {col} {typ}")
        import exch
        exch.ensure_schema(c)
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def _norm(u: str) -> str:
    return (u or "").strip().lower()[:120]


def _thash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- accounts (CLI) ------------------------------------------------------------
MIN_PW = int(os.environ.get("PARTNERS_MIN_PASSWORD", "10"))


def create_partner(username: str, password: str, display_name: str = "",
                   role: str = "partner", force: bool = False) -> int:
    """`force` (CLI only) skips the length rule — used for operator-chosen
    admin passwords. Self-signup never passes it."""
    u = _norm(username)
    if not u or (len(password) < MIN_PW and not force):
        raise ValueError(f"username required; password must be >= {MIN_PW} characters")
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12))
    with _lock, _conn() as c:
        cur = c.execute("INSERT INTO partners(username,display_name,pw_hash,enabled,created_ts,role) "
                        "VALUES(?,?,?,1,?,?)", (u, display_name or u, h, time.time(), role))
        return int(cur.lastrowid)


def exists(username: str) -> bool:
    with _lock, _conn() as c:
        return c.execute("SELECT 1 FROM partners WHERE username=?", (_norm(username),)).fetchone() is not None


# --- self-signup with emailed code ---------------------------------------------
SIGNUP_TTL_SEC = int(os.environ.get("PARTNERS_SIGNUP_TTL_SEC", "900"))
SIGNUP_MAX_ATTEMPTS = 5


def start_signup(email: str, password: str, display_name: str, ip: str,
                 exchange: dict | None = None) -> tuple[str | None, str]:
    """Stage an account behind a 6-digit code. Returns (code, reason). The code
    is returned ONLY so the caller can hand it to the mailer (or log it when no
    mailer is configured); it is stored hashed. Never reveals whether the email
    already has an account — the HTTP layer answers identically either way."""
    e = _norm(email)
    if not e or "@" not in e or "." not in e.split("@")[-1] or len(e) > 120:
        return None, "bad_email"
    if len(password) < MIN_PW:
        return None, "weak_password"
    now = time.time()
    with _lock, _conn() as c:
        if _fails(c, "signup-ip:" + ip, now) >= 10:
            return None, "rate_limited"
        c.execute("INSERT INTO login_fails(key,ts) VALUES(?,?)", ("signup-ip:" + ip, now))
        if c.execute("SELECT 1 FROM partners WHERE username=?", (e,)).fetchone():
            return None, "already_registered"
        code = f"{secrets.randbelow(10**6):06d}"
        ex = exchange or {}
        c.execute("INSERT OR REPLACE INTO signups(email,display_name,pw_hash,code_hash,created_ts,expires_ts,attempts,ip,"
                  "ex_json,ex_exchange,ex_status,ex_verified) VALUES(?,?,?,?,?,?,0,?,?,?,?,?)",
                  (e, (display_name or "").strip()[:80] or e, bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)),
                   _thash(code), now, now + SIGNUP_TTL_SEC, ip,
                   ex.get("sealed"), ex.get("exchange"), ex.get("status"), 1 if ex.get("verified") else 0))
        return code, "ok"


def verify_signup(email: str, code: str, ip: str) -> tuple[str | None, str]:
    """Consume the code; on success create the partner and return a session token."""
    e = _norm(email)
    now = time.time()
    with _lock, _conn() as c:
        row = c.execute("SELECT display_name,pw_hash,code_hash,expires_ts,attempts,ex_json,ex_exchange,ex_status,ex_verified "
                        "FROM signups WHERE email=?", (e,)).fetchone()
        if not row:
            return None, "no_pending"
        dname, pw_hash, code_hash, exp, attempts, ex_json, ex_exchange, ex_status, ex_verified = row
        if now > exp or attempts >= SIGNUP_MAX_ATTEMPTS:
            c.execute("DELETE FROM signups WHERE email=?", (e,))
            return None, "expired"
        if not hmac.compare_digest(code_hash, _thash((code or "").strip())):
            c.execute("UPDATE signups SET attempts=attempts+1 WHERE email=?", (e,))
            return None, "bad_code"
        c.execute("DELETE FROM signups WHERE email=?", (e,))
        if c.execute("SELECT 1 FROM partners WHERE username=?", (e,)).fetchone():
            return None, "already_registered"
        cur = c.execute("INSERT INTO partners(username,display_name,pw_hash,enabled,created_ts,role,last_login_ts) "
                        "VALUES(?,?,?,1,?,'partner',?)", (e, dname, pw_hash, now, now))
        if ex_json and ex_exchange:
            import exch
            try:
                k_tail = exch.tail(exch.unseal(ex_json)[0])
            except Exception:
                k_tail = ""
            c.execute("INSERT INTO exchange_keys(partner_id,exchange,sealed,key_tail,status,verified,created_ts,updated_ts) "
                      "VALUES(?,?,?,?,?,?,?,?)", (cur.lastrowid, ex_exchange, ex_json, k_tail, ex_status or "stored",
                                                  ex_verified or 0, now, now))
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(token_hash,partner_id,created_ts,seen_ts,ip) VALUES(?,?,?,?,?)",
                  (_thash(token), cur.lastrowid, now, now, ip))
        return token, "ok"


def pending_signups() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT email,display_name,created_ts,expires_ts,attempts FROM signups ORDER BY created_ts").fetchall()
    return [{"email": r[0], "display_name": r[1], "created_ts": r[2], "expires_ts": r[3], "attempts": r[4]} for r in rows]


def approve_signup(email: str) -> bool:
    """Operator bypass for the code (e.g. while no mailer is configured)."""
    e = _norm(email); now = time.time()
    with _lock, _conn() as c:
        row = c.execute("SELECT display_name,pw_hash FROM signups WHERE email=?", (e,)).fetchone()
        if not row: return False
        c.execute("DELETE FROM signups WHERE email=?", (e,))
        c.execute("INSERT INTO partners(username,display_name,pw_hash,enabled,created_ts,role) VALUES(?,?,?,1,?,'partner')",
                  (e, row[0], row[1], now))
        return True


def set_enabled(username: str, enabled: bool) -> bool:
    with _lock, _conn() as c:
        cur = c.execute("UPDATE partners SET enabled=? WHERE username=?", (1 if enabled else 0, _norm(username)))
        if not enabled:
            c.execute("DELETE FROM sessions WHERE partner_id IN (SELECT id FROM partners WHERE username=?)",
                      (_norm(username),))
        return cur.rowcount > 0


def reset_password(username: str, password: str) -> bool:
    if len(password) < 12:
        raise ValueError("password must be >= 12 characters")
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12))
    with _lock, _conn() as c:
        cur = c.execute("UPDATE partners SET pw_hash=? WHERE username=?", (h, _norm(username)))
        c.execute("DELETE FROM sessions WHERE partner_id IN (SELECT id FROM partners WHERE username=?)",
                  (_norm(username),))
        return cur.rowcount > 0


def list_partners() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT p.id,p.username,p.display_name,p.enabled,p.created_ts,p.last_login_ts,"
                         "(SELECT COUNT(*) FROM sessions s WHERE s.partner_id=p.id),p.role FROM partners p ORDER BY p.id").fetchall()
    return [{"id": r[0], "username": r[1], "display_name": r[2], "enabled": bool(r[3]),
             "created_ts": r[4], "last_login_ts": r[5], "sessions": r[6], "role": r[7]} for r in rows]


# --- login / sessions ----------------------------------------------------------
def _fails(c, key: str, now: float) -> int:
    c.execute("DELETE FROM login_fails WHERE ts < ?", (now - max(LOGIN_WINDOW_SEC, LOGIN_LOCK_SEC),))
    return c.execute("SELECT COUNT(*) FROM login_fails WHERE key=? AND ts > ?",
                     (key, now - LOGIN_WINDOW_SEC)).fetchone()[0]


def login(username: str, password: str, ip: str) -> tuple[str | None, str]:
    """Returns (raw_session_token | None, reason). reason is for the server LOG
    only — the HTTP response is always the same generic message on failure."""
    u = _norm(username)
    now = time.time()
    with _lock, _conn() as c:
        if _fails(c, "ip:" + ip, now) >= LOGIN_MAX_FAILS * 3 or _fails(c, "user:" + u, now) >= LOGIN_MAX_FAILS:
            bcrypt.checkpw(b"x", _DUMMY_HASH)
            return None, "locked"
        row = c.execute("SELECT id,pw_hash,enabled FROM partners WHERE username=?", (u,)).fetchone()
        ok = False
        if row:
            ok = bcrypt.checkpw((password or "").encode(), row[1]) and bool(row[2])
        else:
            bcrypt.checkpw((password or "").encode(), _DUMMY_HASH)
        if not ok:
            c.execute("INSERT INTO login_fails(key,ts) VALUES(?,?),(?,?)", ("ip:" + ip, now, "user:" + u, now))
            return None, "bad_credentials" if row else "no_such_user"
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(token_hash,partner_id,created_ts,seen_ts,ip) VALUES(?,?,?,?,?)",
                  (_thash(token), row[0], now, now, ip))
        c.execute("UPDATE partners SET last_login_ts=? WHERE id=?", (now, row[0]))
        c.execute("DELETE FROM login_fails WHERE key=?", ("user:" + u,))
        return token, "ok"


def session_user(token: str | None) -> dict | None:
    if not token or len(token) > 128:
        return None
    now = time.time()
    with _lock, _conn() as c:
        row = c.execute("SELECT s.token_hash,s.created_ts,s.seen_ts,p.username,p.display_name,p.enabled,p.role "
                        "FROM sessions s JOIN partners p ON p.id=s.partner_id WHERE s.token_hash=?",
                        (_thash(token),)).fetchone()
        if not row:
            return None
        th, created, seen, uname, dname, enabled, role = row
        if not enabled or now - created > SESSION_MAX_SEC or now - seen > SESSION_IDLE_SEC:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            return None
        if now - seen > 60:
            c.execute("UPDATE sessions SET seen_ts=? WHERE token_hash=?", (now, th))
        return {"username": uname, "display_name": dname, "role": role}


def logout(token: str | None) -> None:
    if not token:
        return
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_thash(token),))


def purge_expired() -> int:
    now = time.time()
    with _lock, _conn() as c:
        cur = c.execute("DELETE FROM sessions WHERE created_ts < ? OR seen_ts < ?",
                        (now - SESSION_MAX_SEC, now - SESSION_IDLE_SEC))
        return cur.rowcount


# --- exchange keys (per partner) ------------------------------------------------
def partner_id(username: str) -> int | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT id FROM partners WHERE username=?", (_norm(username),)).fetchone()
        return int(r[0]) if r else None


def exchange_get(username: str) -> dict | None:
    import exch
    pid = partner_id(username)
    if pid is None:
        return None
    with _lock, _conn() as c:
        return exch.get_public(c, pid)


def exchange_set(username: str, exchange: str, api_key: str, api_secret: str) -> tuple[bool, str]:
    import exch
    ok, status, meta = exch.validate(exchange, api_key, api_secret)
    if not ok:
        return False, status
    pid = partner_id(username)
    if pid is None:
        return False, "no such partner"
    with _lock, _conn() as c:
        exch.put(c, pid, exchange, api_key, api_secret, status, bool(meta.get("verified")))
    return True, status


def exchange_remove(username: str) -> bool:
    import exch
    pid = partner_id(username)
    if pid is None:
        return False
    with _lock, _conn() as c:
        return exch.remove(c, pid)
