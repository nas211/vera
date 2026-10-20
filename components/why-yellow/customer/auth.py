"""Server-side identity from a Supabase Auth JWT.

The backend NEVER trusts a user_id / account_id / tenant id from the client. The only
identity source is the verified `sub` claim of the bearer token issued by Supabase Auth:
  * signature: HS256 with the project JWT secret (legacy projects) or ES256/RS256 via the
    project's public JWKS (`${SUPABASE_URL}/auth/v1/.well-known/jwks.json`)
  * issuer  == `${SUPABASE_URL}/auth/v1`
  * audience == "authenticated", role == "authenticated"
  * exp / iat required; small leeway
Tokens are never logged (see customer.log patterns)."""
from __future__ import annotations
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

import jwt
from jwt import PyJWKSet

from .log import register_secret

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ALLOWED_ALGS = ("HS256", "ES256", "RS256")


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Identity:
    user_id: str
    email: Optional[str]
    role: str
    partner: Optional[str] = None     # affiliate/partner code from signup user_metadata (see affiliate/)

    def __repr__(self):
        return f"Identity(user_id={self.user_id!r})"


class Verifier:
    def __init__(self, supabase_url: str, jwt_secret: Optional[str] = None, jwks_fetch: Optional[Callable[[], dict]] = None,
                 jwks_ttl_s: float = 600.0, clock: Callable[[], float] = time.time, leeway_s: int = 10):
        self.url = supabase_url.rstrip("/")
        self.issuer = f"{self.url}/auth/v1"
        self.secret = jwt_secret
        if jwt_secret:
            register_secret(jwt_secret)
        self._jwks_fetch = jwks_fetch or self._default_jwks_fetch
        self._jwks: Optional[PyJWKSet] = None
        self._jwks_ts = 0.0
        self._jwks_ttl = jwks_ttl_s
        self.clock = clock
        self.leeway = leeway_s
        self._lock = threading.Lock()

    # ---- keys
    def _default_jwks_fetch(self) -> dict:
        import httpx
        r = httpx.get(f"{self.issuer}/.well-known/jwks.json", timeout=10.0)
        r.raise_for_status()
        return r.json()

    def _jwkset(self, force: bool = False) -> PyJWKSet:
        with self._lock:
            if force or self._jwks is None or self.clock() - self._jwks_ts > self._jwks_ttl:
                self._jwks = PyJWKSet.from_dict(self._jwks_fetch())
                self._jwks_ts = self.clock()
            return self._jwks

    def _key_for(self, header: Mapping) -> object:
        alg = header.get("alg")
        if alg == "HS256":
            if not self.secret:
                raise AuthError("hs256_not_configured")
            return self.secret
        kid = header.get("kid")
        if not kid:
            raise AuthError("missing_kid")
        for attempt in (False, True):   # refresh once on unknown kid (key rotation)
            try:
                return self._jwkset(force=attempt)[kid].key
            except KeyError:
                continue
            except Exception:
                raise AuthError("jwks_unavailable", 503)
        raise AuthError("unknown_kid")

    # ---- verify
    def verify(self, token: Optional[str]) -> Identity:
        if not token or len(token) > 8192 or token.count(".") != 2:
            raise AuthError("missing_or_malformed_token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("malformed_header")
        alg = header.get("alg")
        if alg not in ALLOWED_ALGS:
            raise AuthError("alg_not_allowed")
        key = self._key_for(header)
        try:
            claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=self.issuer,
                                leeway=self.leeway, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        except jwt.ExpiredSignatureError:
            raise AuthError("token_expired")
        except jwt.InvalidAudienceError:
            raise AuthError("bad_audience")
        except jwt.InvalidIssuerError:
            raise AuthError("bad_issuer")
        except jwt.PyJWTError:
            raise AuthError("invalid_token")
        if claims.get("role") != "authenticated":
            raise AuthError("role_not_authenticated", 403)
        sub = str(claims.get("sub", ""))
        if not UUID_RE.match(sub):
            raise AuthError("bad_subject")
        try:
            uuid.UUID(sub)
        except ValueError:
            raise AuthError("bad_subject")
        email = claims.get("email")
        meta = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}
        partner = meta.get("partner")
        partner = str(partner).strip().lower()[:32] if isinstance(partner, str) and partner.strip() else None
        return Identity(user_id=sub, email=str(email) if email else None, role="authenticated", partner=partner)


def bearer_token(headers: Mapping[str, str]) -> Optional[str]:
    h = headers.get("Authorization") or headers.get("authorization") or ""
    if not h.lower().startswith("bearer "):
        return None
    return h[7:].strip() or None
