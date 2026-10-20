"""Partner exchange API keys — sealed at rest, never returned, never logged.

Same threat model as the Telegram bot's secrets_box: the DB alone must be
worthless (Fernet ciphertext only), the master key lives OUTSIDE the DB and
the repo at /etc/why-partners/keys.key (0600), there is NO plaintext fallback,
and the API only ever returns the exchange name, the key's last 4 characters
and the validation status. Withdraw-enabled Binance keys are refused at
onboarding by the bot's own perms_ok(); Bybit keys are stored unverified for
now (marked as such) — read-only until the Bybit connector exists.

Supported today: binance, bybit. Anything else is refused with the same
"coming soon" message the signup page shows.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

KEYFILE = os.environ.get("PARTNERS_KEYFILE", "/etc/why-partners/keys.key")
SUPPORTED = {"binance": "Binance", "bybit": "Bybit"}
NOTE = ("We currently accommodate Binance and Bybit only — other exchanges are "
        "coming soon. Create a key with trading enabled and withdrawals DISABLED; "
        "WHY will never hold a key that can move funds.")

_TGBOT = os.environ.get("PARTNERS_PUBLISHER_DIR", "/opt/alpha/tgbot")
if _TGBOT not in sys.path:
    sys.path.insert(0, _TGBOT)

_lock = threading.Lock()
_fernet: Fernet | None = None


def _box() -> Fernet:
    global _fernet
    if _fernet:
        return _fernet
    if not os.path.exists(KEYFILE):
        os.makedirs(os.path.dirname(KEYFILE), exist_ok=True)
        fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(Fernet.generate_key())
    st = os.stat(KEYFILE)
    if st.st_mode & 0o077:
        raise RuntimeError("keyfile is too widely readable")
    with open(KEYFILE, "rb") as fh:
        _fernet = Fernet(fh.read().strip())
    return _fernet


def seal(api_key: str, api_secret: str) -> bytes:
    return _box().encrypt(json.dumps({"k": api_key, "s": api_secret}).encode())


def unseal(blob: bytes) -> tuple[str, str]:
    d = json.loads(_box().decrypt(bytes(blob)))
    return d["k"], d["s"]


def tail(api_key: str) -> str:
    return (api_key or "")[-4:]


def validate(exchange: str, api_key: str, api_secret: str) -> tuple[bool, str, dict]:
    """(accepted, status_text, meta). Binance keys are probed read-only through
    the bot's own onboarding check (permissions + futures reachability, refuses
    withdraw-enabled keys). Bybit is stored as 'unverified' until a connector
    exists. Never raises for a user mistake."""
    ex = (exchange or "").lower().strip()
    if ex not in SUPPORTED:
        return False, "That exchange is not supported yet — Binance and Bybit only for now.", {}
    if not api_key or not api_secret or len(api_key) < 16 or len(api_secret) < 16:
        return False, "Enter both the API key and the API secret.", {}
    if ex == "binance":
        try:
            from exchange import probe_key  # tgbot, read-only signed GETs
            p = probe_key(api_key, api_secret)
        except Exception:
            return True, "stored — verification unavailable right now", {"verified": False}
        if not p.get("ok"):
            return False, f"Binance refused this key: {p.get('reason') or 'unknown error'}", {}
        return True, "verified · trading enabled, withdrawals disabled", {"verified": True, "perms": p.get("perms") or {}}
    return True, "stored — Bybit verification coming soon", {"verified": False}


def ensure_schema(c: sqlite3.Connection) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS exchange_keys(
      partner_id INTEGER PRIMARY KEY, exchange TEXT NOT NULL, sealed BLOB NOT NULL,
      key_tail TEXT NOT NULL, status TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
      created_ts REAL NOT NULL, updated_ts REAL NOT NULL);
    """)


def put(c: sqlite3.Connection, partner_id: int, exchange: str, api_key: str, api_secret: str,
        status: str, verified: bool) -> None:
    now = time.time()
    c.execute("INSERT INTO exchange_keys(partner_id,exchange,sealed,key_tail,status,verified,created_ts,updated_ts) "
              "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(partner_id) DO UPDATE SET exchange=excluded.exchange, sealed=excluded.sealed, "
              "key_tail=excluded.key_tail, status=excluded.status, verified=excluded.verified, updated_ts=excluded.updated_ts",
              (partner_id, exchange.lower(), seal(api_key, api_secret), tail(api_key), status, 1 if verified else 0, now, now))


def get_public(c: sqlite3.Connection, partner_id: int) -> dict | None:
    r = c.execute("SELECT exchange,key_tail,status,verified,updated_ts FROM exchange_keys WHERE partner_id=?", (partner_id,)).fetchone()
    if not r:
        return None
    return {"exchange": r[0], "exchange_label": SUPPORTED.get(r[0], r[0]), "key_tail": r[1], "status": r[2],
            "verified": bool(r[3]), "updated_ts": r[4]}


def remove(c: sqlite3.Connection, partner_id: int) -> bool:
    return c.execute("DELETE FROM exchange_keys WHERE partner_id=?", (partner_id,)).rowcount > 0
