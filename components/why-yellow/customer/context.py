"""Customer execution context + server-side credential resolution.

CustomerExecutionContext is what the execution engine receives for a tenant. It carries
identifiers, sizing and state — never secret material. Credentials are resolved from
Vault by LazyYellowClient only when a venue call actually needs them, held in SecretBox
objects (opaque repr), re-resolved after a TTL so rotation/revocation takes effect, and
dropped with forget()."""
from __future__ import annotations
import os
import sys
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional, Protocol

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from execution.sizing import ExposureLimits
from execution.transport import TransportAmbiguous, VenueRejected, VenueUnavailable
from execution.yellow_transport import YellowTransport
from .config import FIXED_LEVERAGE, MAX_OPEN_POSITIONS, VENUE
from .log import get_logger, safe_exc
from .vault import SecretBox, Vault

log = get_logger("context")


@dataclass(frozen=True)
class CustomerExecutionContext:
    user_id: str
    account_id: str
    credential_ref: Optional[str]
    position_size_usd: Decimal
    state: str
    entries_paused: bool = True
    pending_position_size_usd: Optional[Decimal] = None
    leverage: int = FIXED_LEVERAGE
    max_open_positions: int = MAX_OPEN_POSITIONS
    venue: str = VENUE
    app_session_id: Optional[str] = None
    trade_margin_pct: Optional[int] = None
    trading_style: str = "swing"

    @property
    def namespace(self) -> str:
        """Engine account id: unique per (venue, account). Combined with signal_id inside
        make_coid this yields tenant-unique client_order_ids."""
        return f"{self.venue}:{self.account_id}"

    def limits(self) -> ExposureLimits:
        # Scalp owns its entry-slot budget; max_open_positions is the saved
        # Swing setting and must not shrink the Scalp grid after a switch.
        if self.trading_style == 'scalp':
            from .scalp.strategy import CONFIG
            return ExposureLimits(max_notional_per_order=Decimal('Infinity'),
                                  max_total_notional=Decimal('Infinity'),
                                  max_open_positions=CONFIG['max_entry_slots'])
        # Live capacity comes from the venue's current available balance on each
        # entry. The configured size is retained for old records, not risk gating.
        return ExposureLimits(max_notional_per_order=Decimal("Infinity"), max_total_notional=Decimal("Infinity"),
                              max_open_positions=self.max_open_positions)

    @property
    def entries_wanted(self) -> bool:
        return self.state == "ACTIVE" and not self.entries_paused

    @property
    def managed(self) -> bool:
        return self.credential_ref is not None and self.state != "DISCONNECTED"


@dataclass(frozen=True)
class YellowCredential:
    api_key: SecretBox
    api_secret: SecretBox
    app_session_id: Optional[str]

    def __repr__(self):
        return f"YellowCredential(app_session_id={self.app_session_id!r}, api_key=***, api_secret=***)"


class CredentialResolver(Protocol):
    def resolve(self, ctx: CustomerExecutionContext) -> Optional[YellowCredential]: ...


class VaultCredentialResolver:
    def __init__(self, db):
        self.db = db

    def resolve(self, ctx: CustomerExecutionContext) -> Optional[YellowCredential]:
        if not ctx.credential_ref:
            return None
        with self.db.service() as c:
            row = c.execute("select c.vault_api_key_id, c.vault_api_secret_id, a.app_session_id, c.user_id "
                            "from public.yellow_credentials c join public.yellow_accounts a on a.id = c.account_id "
                            "where c.id = %s and c.account_id = %s", (ctx.credential_ref, ctx.account_id)).fetchone()
            if row is None or str(row["user_id"]) != str(ctx.user_id):
                return None   # ownership must match the context; never resolve across tenants
            k = Vault.read(c, str(row["vault_api_key_id"]))
            s = Vault.read(c, str(row["vault_api_secret_id"]))
        if k is None or s is None:
            return None
        return YellowCredential(k, s, row["app_session_id"] or ctx.app_session_id)


class CredentialsUnavailable(VenueUnavailable):
    def __init__(self, reason: str = "credentials_unavailable"):
        super().__init__(0, reason)


class LazyYellowClient:
    """yellow_exec.YellowClient built on demand from Vault. Public endpoints work without
    credentials; private ones resolve them first. Attribute access is proxied."""

    def __init__(self, resolver: CredentialResolver, ctx: CustomerExecutionContext, ttl_s: float = 600.0,
                 client_factory: Optional[Callable] = None, clock: Callable[[], float] = time.time):
        self.resolver, self.ctx, self.ttl, self.clock = resolver, ctx, ttl_s, clock
        self._factory = client_factory or self._default_factory
        self._client = None
        self._resolved_ts = 0.0
        self._lock = threading.Lock()
        self.resolutions = 0

    @staticmethod
    def _default_factory(api_key: str, secret: str, asid: Optional[str]):
        from yellow_exec import YellowClient
        return YellowClient(api_key, secret, asid)

    def ensure(self):
        with self._lock:
            if self._client is not None and self.clock() - self._resolved_ts < self.ttl:
                return self._client
            try:
                cred = self.resolver.resolve(self.ctx)
            except Exception as e:
                log.warning("credential resolution failed for %s: %s", self.ctx.account_id, safe_exc(e))
                raise CredentialsUnavailable("credential_resolution_error")
            if cred is None:
                self._client = None
                raise CredentialsUnavailable()
            self._client = self._factory(cred.api_key.reveal(), cred.api_secret.reveal(), cred.app_session_id)
            self._resolved_ts = self.clock()
            self.resolutions += 1
            return self._client

    def forget(self) -> None:
        with self._lock:
            self._client = None
            self._resolved_ts = 0.0

    @property
    def resolved(self) -> bool:
        return self._client is not None

    def _req(self, *args, **kwargs):
        # YellowTransport's verified cancel path uses the signed request method.
        # Resolve through Vault just as for public client methods.
        return self.ensure()._req(*args, **kwargs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.ensure(), name)

    def __repr__(self):
        return f"LazyYellowClient({self.ctx.namespace}, resolved={self.resolved})"


class TenantYellowTransport(YellowTransport):
    """YellowTransport over a LazyYellowClient. Adds: credentials/arming are checked BEFORE
    an order request so a missing credential or an un-armed process yields a definitive
    'not processed' (FAILED) instead of an ambiguous UNKNOWN. Everything else is the
    audited YellowTransport behaviour unchanged."""

    def _wrap(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (TransportAmbiguous, VenueUnavailable, VenueRejected):
            raise
        except Exception as e:
            status = getattr(e, "status", None)
            code = getattr(e, "code", "error")
            if status in (429,):
                raise VenueUnavailable(429, str(e)) from e
            if status is not None and status >= 500:
                raise VenueUnavailable(status, str(e)) from e
            if status is not None:
                raise VenueRejected(str(code), getattr(e, "message", str(e)), status) from e
            raise TransportAmbiguous(safe_exc(e)) from e

    def place_order(self, req):
        client = self.c.ensure()                      # CredentialsUnavailable -> VenueUnavailable(0)
        if not client.armed():
            raise VenueUnavailable(0, "not_armed")    # YELLOW_ARMED=1 + keys required; definitive not-sent
        return super().place_order(req)

    def forget_credentials(self) -> None:
        self.c.forget()
