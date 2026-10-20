"""Credential verification against Yellow — READ ONLY.

What it does with a submitted key/secret:
  1. GET /perpetual/accounts      (HMAC)  -> credential works? which app_session_id?
  2. GET /perpetual/account       (HMAC)  -> equity / available balance
  3. GET /accounts/api-key/permissions (HMAC) -> live scopes for this authenticated key.
     The legacy GET /accounts/api-keys listing is only a compatibility fallback.
     The self-permissions endpoint replaces the JWT-only key listing used previously.
     Missing, inactive, or mismatched metadata remains unverified.
What it never does: place orders, touch withdrawal/transfer endpoints, log key material.

Policy: withdrawal and transfer scopes are forbidden, except the explicitly allowed
internal transfer:spot-perp scope. Required scopes: read + trade on futures."""
from __future__ import annotations
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from .log import get_logger, register_secret, safe_exc

log = get_logger("yellow_probe")

FORBIDDEN_SCOPE_MARKERS = ("withdraw", "transfer")
REQUIRED_SCOPES = frozenset(("read:futures", "trade:futures"))
PERMISSION_SOURCE_API = "yellow_api"
PERMISSION_SOURCE_UNAVAILABLE = "unavailable"


@dataclass
class VerifyResult:
    reachable: bool
    credential_ok: Optional[bool]              # None when Yellow could not be reached
    app_session_id: Optional[str] = None
    equity: Optional[Decimal] = None
    available: Optional[Decimal] = None
    permission_source: str = PERMISSION_SOURCE_UNAVAILABLE
    scopes: List[str] = field(default_factory=list)
    forbidden: bool = False
    missing_required: bool = False
    error_code: str = ""
    detail: dict = field(default_factory=dict)   # never contains secrets

    @property
    def usable(self) -> bool:
        return bool(self.reachable and self.credential_ok and not self.forbidden and not self.missing_required)

    @property
    def permissions_verified(self) -> bool:
        return self.usable and self.permission_source == PERMISSION_SOURCE_API


def scopes_forbidden(scopes: List[str]) -> bool:
    return any(s != 'transfer:spot-perp' and
               (s in ('*', 'all', 'admin') or '*' in s or
                any(m in s for m in FORBIDDEN_SCOPE_MARKERS))
               for raw in scopes for s in (str(raw).strip().lower(),))


def scopes_missing_required(scopes: List[str]) -> bool:
    return not REQUIRED_SCOPES.issubset({str(s).strip().lower() for s in scopes})


def _dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v)) if v is not None and v != "" else None
    except Exception:
        return None


class YellowVerifier:
    """Real Yellow probe. `client_factory(api_key, secret)` must return a
    yellow_exec.YellowClient-compatible object; tests inject fakes."""

    def __init__(self, client_factory: Optional[Callable] = None, clock: Callable[[], float] = time.time):
        self.client_factory = client_factory or self._default_factory
        self.clock = clock

    @staticmethod
    def _default_factory(api_key: str, secret: str):
        from yellow_exec import YellowClient
        return YellowClient(api_key, secret, None)

    def verify(self, api_key: str, api_secret: str) -> VerifyResult:
        register_secret(api_key); register_secret(api_secret)
        from yellow_exec import YellowError
        try:
            from execution.transport import TransportAmbiguous
        except Exception:  # pragma: no cover
            class TransportAmbiguous(RuntimeError): ...
        c = self.client_factory(api_key, api_secret)
        # 1. accounts (credential check)
        try:
            accounts = c._req("GET", "/perpetual/accounts", tries=1)
        except YellowError as e:
            if e.status in (401, 403):
                return VerifyResult(reachable=True, credential_ok=False, error_code=f"invalid_credential:{e.code}")
            if e.status == 429 or e.status >= 500:
                return VerifyResult(reachable=False, credential_ok=None, error_code=f"yellow_unavailable:{e.status}")
            return VerifyResult(reachable=True, credential_ok=False, error_code=f"unexpected_status:{e.status}:{e.code}")
        except Exception as e:
            log.info("yellow unreachable during verify: %s", safe_exc(e))
            return VerifyResult(reachable=False, credential_ok=None, error_code="yellow_unreachable")
        asid = None
        if isinstance(accounts, list) and accounts:
            asid = accounts[0].get("app_session_id")
        elif isinstance(accounts, dict):
            asid = accounts.get("app_session_id")
        res = VerifyResult(reachable=True, credential_ok=True, app_session_id=str(asid) if asid else None)
        if not asid:
            res.credential_ok = False
            res.error_code = "no_perpetual_account"
            return res
        # 2. balances
        try:
            c.asid = asid
            a = c._req("GET", "/perpetual/account", {"app_session_id": asid}, tries=1)
            res.equity, res.available = _dec(a.get("total_account_equity")), _dec(a.get("available_balance"))
        except Exception as e:
            res.detail["balance_error"] = safe_exc(e)
        # 3. Live self-permissions are bound by Yellow to the authenticated key.
        try:
            metadata = c._req('GET', '/accounts/api-key/permissions', tries=1)
            if not isinstance(metadata, dict) or not isinstance(metadata.get('scopes'), list) or not all(isinstance(s, str) for s in metadata.get('scopes', [])):
                res.detail['permission_probe'] = 'invalid_permission_metadata'
                return res
            if metadata.get('active') is not True:
                res.detail['permission_probe'] = 'api_key_not_active'
                return res
            if str(metadata.get('app_session_id')) != str(asid):
                res.detail['permission_probe'] = 'permission_account_mismatch'
                return res
            res.scopes = metadata['scopes']
            res.permission_source = PERMISSION_SOURCE_API
            res.forbidden = scopes_forbidden(res.scopes)
            res.missing_required = scopes_missing_required(res.scopes)
            res.detail['permission_endpoint'] = '/accounts/api-key/permissions'
            return res
        except YellowError as e:
            if e.status not in (404, 405):
                res.detail['permission_probe'] = f'http_{e.status}:{e.code}'
                return res
        except Exception as e:
            res.detail['permission_probe'] = safe_exc(e)
            return res
        # Legacy compatibility: only an exact, unique key match is evidence.
        try:
            j = c._req("GET", "/accounts/api-keys", tries=1)
            keys = j if isinstance(j, list) else (j.get("keys") or j.get("api_keys") or []) if isinstance(j, dict) else []
            # A prefix (or a masked key) cannot identify this credential safely.
            matches = [k for k in keys if isinstance(k, dict)
                       and (k.get("api_key") or k.get("key")) == api_key]
            mine = matches[0] if len(matches) == 1 else None
            if mine is not None and isinstance(mine.get("scopes"), list):
                res.scopes = [str(s) for s in mine["scopes"]]
                res.permission_source = PERMISSION_SOURCE_API
                res.forbidden = scopes_forbidden(res.scopes)
                res.missing_required = scopes_missing_required(res.scopes)
            else:
                res.detail["permission_probe"] = "key_not_listed_or_no_scopes"
        except YellowError as e:
            res.detail["permission_probe"] = f"http_{e.status}:{e.code}"
        except Exception as e:
            res.detail["permission_probe"] = safe_exc(e)
        return res
