"""Team login for the affiliate dashboard: pbkdf2 password hashes (stdlib), HMAC-signed session
cookie (stateless, expiring), per-IP login rate limit. No third-party auth: the team is small and
the accounts live in public.affiliate_team_users (server-only table)."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Callable, Dict, Optional

PBKDF2_ITER = 210_000
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(n: int = 16) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Sessions:
    """`user_id|email|role|exp` signed with HMAC-SHA256 over a server secret."""

    def __init__(self, secret: str, ttl_s: int, clock: Callable[[], float] = time.time):
        if len(secret) < 32:
            raise ValueError("session secret too short")
        self.key = secret.encode("utf-8")
        self.ttl, self.clock = int(ttl_s), clock

    def issue(self, user: dict) -> str:
        payload = json.dumps({"uid": str(user["id"]), "email": user["email"], "role": user["role"], "name": user.get("name") or "",
                              "partner_code": user.get("partner_code"),
                              "exp": int(self.clock()) + self.ttl, "n": secrets.token_hex(4)}, separators=(",", ":")).encode()
        sig = hmac.new(self.key, payload, hashlib.sha256).digest()
        return _b64(payload) + "." + _b64(sig)

    def read(self, token: Optional[str]) -> Optional[dict]:
        if not token or "." not in token:
            return None
        try:
            p, s = token.split(".", 1)
            payload, sig = _unb64(p), _unb64(s)
        except Exception:
            return None
        if not hmac.compare_digest(hmac.new(self.key, payload, hashlib.sha256).digest(), sig):
            return None
        try:
            d = json.loads(payload)
        except Exception:
            return None
        if not isinstance(d, dict) or int(d.get("exp", 0)) < self.clock():
            return None
        return d


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


def new_secret() -> str:
    return secrets.token_urlsafe(48)


def env_secret_line() -> str:
    return f"AFFILIATE_SESSION_SECRET={new_secret()}\n"


__all__ = ["generate_password", "hash_password", "check_password", "Sessions", "RateLimiter", "new_secret", "env_secret_line", "os"]
