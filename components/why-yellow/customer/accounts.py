"""Account service: the ONLY writer of the yellow_* tables and of Vault secrets.

Identity always comes from a verified Identity (customer.auth); no method accepts a
client-supplied user/account id. Every customer read goes through db.as_user() so RLS is
exercised on the server path too.

State machine (yellow_accounts.state):
  DISCONNECTED -> (connect) -> PENDING_YELLOW_VERIFICATION
  PENDING_YELLOW_VERIFICATION -> (real verification: credential OK + permissions known-safe) -> VERIFIED
  VERIFIED -> (activate) -> ACTIVE <-> PAUSED
  ACTIVE -> (size change with open positions) -> DRAINING -> (book flat) -> ACTIVE
  any -> (disconnect) -> DISCONNECTED     any -> (definitive credential failure) -> ERROR / DISCONNECTED
VERIFIED is never set by anything but a successful real Yellow verification (or an
explicit owner attestation recorded with evidence, via the admin CLI, when Yellow does
not expose permission metadata — see attest_permissions)."""
from __future__ import annotations
import hashlib
import re
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from .auth import Identity
from .config import ALLOWED_SIZES_USD, FIXED_LEVERAGE, MAX_OPEN_POSITIONS
from .context import CustomerExecutionContext, LazyYellowClient, VaultCredentialResolver
from .log import get_logger, register_secret, safe_exc
from .size_agent import recommend_trade_margin_pct
from .vault import Vault, VaultError
from .strategy_profiles import strategy_profile
from .yellow_probe import PERMISSION_SOURCE_API, PERMISSION_SOURCE_UNAVAILABLE, VerifyResult, scopes_forbidden

log = get_logger("accounts")

S_DISCONNECTED, S_PENDING, S_VERIFIED, S_ACTIVE, S_PAUSED, S_DRAINING, S_ERROR = (
    "DISCONNECTED", "PENDING_YELLOW_VERIFICATION", "VERIFIED", "ACTIVE", "PAUSED", "DRAINING", "ERROR")
V_NONE, V_PENDING, V_VERIFIED, V_FAILED, V_FORBIDDEN = "NONE", "PENDING", "VERIFIED", "FAILED", "FORBIDDEN_PERMISSION"
MANAGED_STATES = (S_PENDING, S_VERIFIED, S_ACTIVE, S_PAUSED, S_DRAINING, S_ERROR)

CLIENT_IDENTITY_KEYS = {"user_id", "userid", "account_id", "accountid", "tenant", "tenant_id", "uid", "sub", "owner_id", "customer_id"}
KEY_RE = re.compile(r"^[\x21-\x7e]{16,256}$")      # printable ASCII, no whitespace
SECRET_RE = re.compile(r"^[\x21-\x7e]{16,512}$")

ACCOUNT_PUBLIC_COLS = ("id", "state", "state_reason", "api_key_masked", "app_session_id", "verification_state", "verification_reason",
                       "permission_source", "verified_at", "last_verify_attempt_at", "balance_equity", "balance_available", "balance_at",
                       "connected_at", "disconnected_at", "created_at", "updated_at")
CONFIG_PUBLIC_COLS = ("account_id", "position_size_usd", "pending_position_size_usd", "leverage", "max_open_positions", "entries_paused",
                      "activated_at", "updated_at", "trade_margin_pct", "trading_style")


class AccountError(Exception):
    def __init__(self, code: str, status: int = 400, detail: Optional[dict] = None):
        super().__init__(code)
        self.code, self.status, self.detail = code, status, (detail or {})


def mask_key(k: str) -> str:
    k = str(k)
    if len(k) < 12:
        return "••••••••"
    return f"{k[:4]}••••••••{k[-4:]}"


def fingerprint(k: str) -> str:
    return hashlib.sha256(str(k).encode()).hexdigest()


def validate_credential_input(api_key, api_secret) -> None:
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise AccountError("invalid_credential_format", 422)
    if not KEY_RE.match(api_key):
        raise AccountError("invalid_api_key_format", 422)
    if not SECRET_RE.match(api_secret):
        raise AccountError("invalid_api_secret_format", 422)
    if api_key == api_secret:
        raise AccountError("key_equals_secret", 422)


def reject_client_identity(body: dict) -> None:
    """Forged identity guard: any client-supplied identity field is a hard reject."""
    if not isinstance(body, dict):
        return
    bad = sorted(k for k in body if str(k).lower() in CLIENT_IDENTITY_KEYS)
    if bad:
        raise AccountError("client_supplied_identity_rejected", 400, {"fields": bad})


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _num(v):
    return None if v is None else str(v)


class AccountService:
    def __init__(self, db, verifier, clock: Callable[[], float] = time.time,
                 runtime_info: Optional[Callable[[str], dict]] = None, state_dir: Optional[str] = None):
        self.db, self.verifier, self.clock = db, verifier, clock
        self.runtime_info = runtime_info or (lambda account_id: {"open_positions": 0, "ambiguous_orders": 0, "loaded": False})
        self.state_dir = state_dir
        self._balance_lock = threading.Lock()
        self._balance_pending: set[str] = set()

    def _has_fixed_entry_override(self, account_id) -> bool:
        """True if the account already opted into a fixed-$ sizing override
        (customer/tenants.py fixed_entry_notional()). The size agent must never
        clobber that choice by also setting a trade_margin_pct."""
        if not self.state_dir:
            return False
        import os
        return os.path.exists(os.path.join(self.state_dir, "tenants", str(account_id), "fixed_entry_notional.json"))

    # ------------------------------------------------------------ helpers
    def _open_positions(self, account_id: str) -> int:
        try:
            return int(self.runtime_info(str(account_id)).get("open_positions", 0))
        except Exception as e:
            log.warning("runtime_info failed for %s: %s", account_id, safe_exc(e))
            return 1   # fail closed: assume positions exist

    @staticmethod
    def _event(c, account_id, user_id, kind: str, detail: Optional[dict] = None, actor: str = "user") -> None:
        import json
        c.execute("insert into public.yellow_account_events(account_id, user_id, kind, detail, actor) values (%s, %s, %s, %s::jsonb, %s)",
                  (account_id, user_id, kind, json.dumps(detail or {}), actor))

    def _ensure(self, c, ident: Identity) -> dict:
        row = c.execute("select * from public.yellow_accounts where user_id = %s", (ident.user_id,)).fetchone()
        if row is None:
            row = self._insert_account(c, ident)
            c.execute("insert into public.yellow_bot_configs(account_id, user_id) values (%s, %s) on conflict (account_id) do nothing",
                      (row["id"], ident.user_id))
            self._event(c, row["id"], ident.user_id, "account_created")
        return row

    @staticmethod
    def _insert_account(c, ident: Identity) -> dict:
        """First touch: the partner code from the signup metadata lands on the account row once.
        Runs in a savepoint so a project where the affiliate migration is not applied yet
        (no partner_code column) still creates the account."""
        import psycopg
        try:
            with c.transaction():
                return c.execute("insert into public.yellow_accounts(user_id, partner_code) values (%s, %s) on conflict (user_id) do update set updated_at = now() returning *",
                                 (ident.user_id, ident.partner)).fetchone()
        except psycopg.errors.UndefinedColumn:
            log.warning("yellow_accounts.partner_code missing (affiliate migration not applied); account created without attribution")
            return c.execute("insert into public.yellow_accounts(user_id) values (%s) on conflict (user_id) do update set updated_at = now() returning *",
                             (ident.user_id,)).fetchone()

    def _get_owned(self, c, ident: Identity, account_id: Optional[str] = None) -> dict:
        """Service-side fetch with explicit ownership check (never trusts account_id alone)."""
        row = c.execute("select * from public.yellow_accounts where user_id = %s", (ident.user_id,)).fetchone()
        if row is None or (account_id is not None and str(row["id"]) != str(account_id)):
            raise AccountError("account_not_found", 404)
        return row

    # ------------------------------------------------------------ reads (RLS path)
    def get_me(self, ident: Identity) -> dict:
        with self.db.service() as c:
            current = self._ensure(c, ident)
        balance_at = current["balance_at"]
        if (current["credential_ref"] and current["state"] in (S_VERIFIED, S_ACTIVE, S_PAUSED, S_DRAINING)
                and (balance_at is None or self.clock() - balance_at.timestamp() > 60)):
            self._schedule_balance_refresh(ident.user_id)
        with self.db.as_user(ident.user_id, ident.email) as c:
            acct = c.execute(f"select {', '.join(ACCOUNT_PUBLIC_COLS)} from public.yellow_accounts").fetchone()
            cfg = c.execute(f"select {', '.join(CONFIG_PUBLIC_COLS)} from public.yellow_bot_configs").fetchone()
            events = c.execute("select id, kind, detail, actor, created_at from public.yellow_account_events order by id desc limit 20").fetchall()
        if acct is None or cfg is None:
            raise AccountError("account_not_found", 404)
        return self._public(acct, cfg, events)

    def _schedule_balance_refresh(self, user_id: str) -> None:
        with self._balance_lock:
            if user_id in self._balance_pending:
                return
            self._balance_pending.add(user_id)
        threading.Thread(target=self._balance_refresh_task, args=(user_id,), daemon=True).start()

    def _balance_refresh_task(self, user_id: str) -> None:
        try:
            self._refresh_balance_if_stale(user_id)
        finally:
            with self._balance_lock:
                self._balance_pending.discard(user_id)

    def _refresh_balance_if_stale(self, user_id: str) -> None:
        """Keep the profile balance current without altering trading state or keys."""
        try:
            with self.db.service() as c:
                row = c.execute("""select id, credential_ref, app_session_id, state
                    from public.yellow_accounts where user_id = %s and credential_ref is not null
                    and state in ('VERIFIED','ACTIVE','PAUSED','DRAINING')
                    and (balance_at is null or balance_at < now() - interval '60 seconds')""", (user_id,)).fetchone()
            if row is None:
                return
            ctx = CustomerExecutionContext(user_id=str(user_id), account_id=str(row["id"]),
                credential_ref=str(row["credential_ref"]), app_session_id=row["app_session_id"],
                state=row["state"], position_size_usd=Decimal(20))
            account = LazyYellowClient(VaultCredentialResolver(self.db), ctx).account()
            equity = Decimal(str(account["total_account_equity"]))
            available = Decimal(str(account["available_balance"]))
            if not equity.is_finite() or not available.is_finite() or equity < 0 or available < 0:
                raise ValueError("invalid Yellow balance")
            with self.db.service() as c:
                c.execute("""update public.yellow_accounts set balance_equity=%s,
                    balance_available=%s, balance_at=now() where id=%s and user_id=%s""",
                    (equity, available, row["id"], user_id))
        except Exception as e:
            log.warning("balance refresh failed for user %s: %s", user_id, safe_exc(e))

    def _public(self, acct, cfg, events=()) -> dict:
        rt = self.runtime_info(str(acct["id"]))
        return dict(
            account=dict(id=str(acct["id"]), state=acct["state"], state_reason=acct["state_reason"], api_key_masked=acct["api_key_masked"],
                         app_session_id=acct["app_session_id"], verification_state=acct["verification_state"],
                         verification_reason=acct["verification_reason"], permission_source=acct["permission_source"],
                         verified_at=_iso(acct["verified_at"]), last_verify_attempt_at=_iso(acct["last_verify_attempt_at"]),
                         balance_equity=_num(acct["balance_equity"]), balance_available=_num(acct["balance_available"]),
                         balance_at=_iso(acct["balance_at"]), connected_at=_iso(acct["connected_at"]), disconnected_at=_iso(acct["disconnected_at"])),
            config=dict(fixed_entry_notional_usd=rt.get("fixed_entry_notional_usd"), fixed_entry_sizing_error=rt.get("fixed_entry_sizing_error"),
                        position_size_usd=cfg["position_size_usd"], pending_position_size_usd=cfg["pending_position_size_usd"],
                        leverage=cfg["leverage"], max_open_positions=cfg["max_open_positions"], entries_paused=cfg["entries_paused"],
                        trade_margin_pct=cfg["trade_margin_pct"], trading_style=cfg["trading_style"],
                        strategy_profile=strategy_profile(acct['id'], cfg, rt.get('equity_usd',acct['balance_equity']), rt),
                        activated_at=_iso(cfg["activated_at"]), sizes_usd=list(ALLOWED_SIZES_USD)),
            runtime=dict(open_positions=rt.get("open_positions", 0), ambiguous_orders=rt.get("ambiguous_orders", 0),
                         pending_entries=rt.get("pending_entries", 0),
                         entries_allowed=bool(rt.get("entries_allowed")),
                         entry_risk=rt.get("entry_risk"),
                         emergency_close_pending=bool(rt.get("emergency_close_pending")),
                         last_error=rt.get("last_error"),
                         venue=rt.get("venue"), loaded=bool(rt.get("loaded")), positions=rt.get("positions", []),
                         last_signal=rt.get("last_signal"), size_overrides=rt.get("size_overrides", {})),
            events=[dict(id=e["id"], kind=e["kind"], detail=e["detail"], actor=e["actor"], at=_iso(e["created_at"])) for e in events],
        )

    # ------------------------------------------------------------ connect / verify
    def connect(self, ident: Identity, api_key: str, api_secret: str) -> dict:
        validate_credential_input(api_key, api_secret)
        register_secret(api_key); register_secret(api_secret)
        fp, masked = fingerprint(api_key), mask_key(api_key)
        with self.db.service() as c:
            acct = self._ensure(c, ident)
            if acct["state"] in (S_ACTIVE, S_DRAINING):
                raise AccountError("pause_before_reconnect", 409)
            if self._open_positions(acct["id"]) > 0:
                raise AccountError("open_positions_managed", 409)
            dup = c.execute("select 1 from public.yellow_accounts where api_key_fingerprint = %s and id <> %s", (fp, acct["id"])).fetchone()
            if dup:
                raise AccountError("credential_already_bound", 409)
            cred = c.execute("select * from public.yellow_credentials where account_id = %s", (acct["id"],)).fetchone()
            try:
                if cred:
                    Vault.update(c, str(cred["vault_api_key_id"]), api_key)
                    Vault.update(c, str(cred["vault_api_secret_id"]), api_secret)
                    c.execute("update public.yellow_credentials set rotated_at = now() where id = %s", (cred["id"],))
                    cred_id = cred["id"]
                else:
                    kid = Vault.create(c, f"yellow:{acct['id']}:api_key", api_key, "Yellow API key (WHY customer)")
                    sid = Vault.create(c, f"yellow:{acct['id']}:api_secret", api_secret, "Yellow API secret (WHY customer)")
                    cred_id = c.execute("insert into public.yellow_credentials(account_id, user_id, vault_api_key_id, vault_api_secret_id) "
                                        "values (%s, %s, %s, %s) returning id", (acct["id"], ident.user_id, kid, sid)).fetchone()["id"]
            except VaultError as e:
                log.error("vault write failed for account %s: %s", acct["id"], safe_exc(e))
                raise AccountError("vault_unavailable", 503)
            c.execute("""update public.yellow_accounts set api_key_masked=%s, api_key_fingerprint=%s, credential_ref=%s, state=%s, state_reason=null,
                         verification_state=%s, verification_reason=null, permission_source=null, permission_scopes=null, verified_at=null,
                         app_session_id=null, balance_equity=null, balance_available=null, balance_at=null, connected_at=now(), disconnected_at=null
                         where id=%s""", (masked, fp, cred_id, S_PENDING, V_PENDING, acct["id"]))
            c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (acct["id"],))
            self._event(c, acct["id"], ident.user_id, "credential_submitted", {"api_key_masked": masked, "rotated": bool(cred)})
            account_id = acct["id"]
        # network verification happens outside the transaction
        result = self._safe_verify(api_key, api_secret)
        return self._apply_verification(ident, account_id, result)

    def _safe_verify(self, api_key: str, api_secret: str) -> VerifyResult:
        try:
            return self.verifier.verify(api_key, api_secret)
        except Exception as e:
            log.warning("verifier raised: %s", safe_exc(e))
            return VerifyResult(reachable=False, credential_ok=None, error_code="verifier_error")

    def verify(self, ident: Identity) -> dict:
        """Re-run verification using the Vault-stored credential (server-side only)."""
        with self.db.service() as c:
            acct = self._get_owned(c, ident)
            if not acct["credential_ref"]:
                raise AccountError("not_connected", 409)
            cred = c.execute("select * from public.yellow_credentials where id = %s and account_id = %s", (acct["credential_ref"], acct["id"])).fetchone()
            if cred is None:
                raise AccountError("credential_missing", 409)
            k, s = Vault.read(c, str(cred["vault_api_key_id"])), Vault.read(c, str(cred["vault_api_secret_id"]))
            account_id = acct["id"]
        if k is None or s is None:
            raise AccountError("credential_missing", 409)
        result = self._safe_verify(k.reveal(), s.reveal())
        return self._apply_verification(ident, account_id, result)

    def _purge_credentials(self, c, acct_id) -> None:
        cred = c.execute("select * from public.yellow_credentials where account_id = %s", (acct_id,)).fetchone()
        if cred:
            for sid in (cred["vault_api_key_id"], cred["vault_api_secret_id"]):
                try:
                    Vault.delete(c, str(sid))
                except VaultError as e:
                    log.error("vault delete failed %s: %s", sid, safe_exc(e))
            c.execute("delete from public.yellow_credentials where id = %s", (cred["id"],))
        c.execute("update public.yellow_accounts set credential_ref = null, api_key_fingerprint = null, api_key_masked = null where id = %s", (acct_id,))

    def _apply_verification(self, ident: Identity, account_id, r: VerifyResult) -> dict:
        with self.db.service() as c:
            acct = self._get_owned(c, ident, account_id)
            st = acct["state"]
            was_live = st in (S_ACTIVE, S_PAUSED, S_DRAINING, S_VERIFIED)
            detail = dict(reachable=r.reachable, credential_ok=r.credential_ok, permission_source=r.permission_source,
                          scopes=r.scopes, forbidden=r.forbidden, missing_required=r.missing_required, error_code=r.error_code, probe=r.detail)
            sets: Dict[str, object] = {"last_verify_attempt_at": "now()"}
            if not r.reachable:
                sets.update(verification_reason=f"yellow_unreachable:{r.error_code}")
                if st == S_DISCONNECTED:
                    pass
                elif not was_live:
                    sets.update(state=S_PENDING, verification_state=V_PENDING)
                kind = "verification_unreachable"
            elif r.credential_ok is False:
                self._purge_credentials(c, acct["id"])
                sets.update(verification_state=V_FAILED, verification_reason=r.error_code or "invalid_credential",
                            state=(S_ERROR if was_live else S_DISCONNECTED), state_reason="invalid_credential", verified_at=None,
                            permission_source=None, permission_scopes=None)
                c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (acct["id"],))
                kind = "verification_failed"
            elif r.forbidden:
                self._purge_credentials(c, acct["id"])
                sets.update(verification_state=V_FORBIDDEN, verification_reason="forbidden_permission", permission_source=PERMISSION_SOURCE_API,
                            permission_scopes=r.scopes, state=(S_ERROR if was_live else S_DISCONNECTED), state_reason="forbidden_permission", verified_at=None)
                c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (acct["id"],))
                kind = "verification_forbidden_permission"
            elif r.missing_required:
                self._purge_credentials(c, acct["id"])
                sets.update(verification_state=V_FAILED, verification_reason="missing_required_scope", permission_source=PERMISSION_SOURCE_API,
                            permission_scopes=r.scopes, state=(S_ERROR if was_live else S_DISCONNECTED), state_reason="missing_required_scope", verified_at=None)
                c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (acct["id"],))
                kind = "verification_failed"
            else:
                sets.update(app_session_id=r.app_session_id, balance_equity=r.equity, balance_available=r.available, balance_at="now()")
                attested = acct["permission_source"] == "owner_attestation" and acct["permission_scopes"] and not scopes_forbidden(acct["permission_scopes"])
                if r.permission_source == PERMISSION_SOURCE_API:
                    sets.update(verification_state=V_VERIFIED, verification_reason=None, permission_source=PERMISSION_SOURCE_API,
                                permission_scopes=r.scopes, verified_at="now()")
                    if st == S_PENDING:
                        sets.update(state=S_VERIFIED, state_reason=None)
                    kind = "verification_succeeded"
                elif attested:
                    sets.update(verification_state=V_VERIFIED, verification_reason="owner_attestation", verified_at="now()")
                    if st == S_PENDING:
                        sets.update(state=S_VERIFIED, state_reason=None)
                    kind = "verification_succeeded_attested"
                else:
                    # credential works, but Yellow did not expose permission scopes: NOT verified.
                    sets.update(verification_reason="permission_metadata_unavailable", permission_source=PERMISSION_SOURCE_UNAVAILABLE)
                    if not was_live:
                        sets.update(state=S_PENDING, verification_state=V_PENDING)
                    kind = "verification_pending_permissions"
            cols, vals = [], []
            for k, v in sets.items():
                if v == "now()":
                    cols.append(f"{k} = now()")
                else:
                    cols.append(f"{k} = %s"); vals.append(v)
            vals.append(acct["id"])
            c.execute(f"update public.yellow_accounts set {', '.join(cols)} where id = %s", vals)
            self._event(c, acct["id"], ident.user_id, kind, detail, actor="system")
        return self.get_me(ident)

    # ------------------------------------------------------------ owner attestation (admin CLI only)
    def attest_permissions(self, account_id: str, scopes: List[str], evidence: str, actor: str) -> dict:
        """Owner-only. Records that the owner verified the key's scopes out-of-band (Yellow
        UI) because Yellow does not expose them to API-key auth. A forbidden scope is still
        rejected here — no override exists for that."""
        if not evidence or len(evidence) < 8:
            raise AccountError("evidence_required", 400)
        with self.db.service() as c:
            acct = c.execute("select * from public.yellow_accounts where id = %s", (account_id,)).fetchone()
            if acct is None:
                raise AccountError("account_not_found", 404)
            if not acct["credential_ref"]:
                raise AccountError("not_connected", 409)
            if scopes_forbidden(scopes):
                # Owner evidence says the key can withdraw/transfer: reject it exactly like a probe result
                # would — purge the secret, never VERIFIED, entries paused. No override exists.
                was_live = acct["state"] in (S_ACTIVE, S_PAUSED, S_DRAINING, S_VERIFIED)
                self._purge_credentials(c, acct["id"])
                c.execute("""update public.yellow_accounts set verification_state=%s, verification_reason='forbidden_permission',
                             permission_source='owner_attestation', permission_scopes=%s, state=%s, state_reason='forbidden_permission',
                             verified_at=null, last_verify_attempt_at=now() where id=%s""",
                          (V_FORBIDDEN, list(scopes), (S_ERROR if was_live else S_DISCONNECTED), acct["id"]))
                c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (acct["id"],))
                self._event(c, account_id, acct["user_id"], "owner_permission_rejection",
                            {"scopes": list(scopes), "evidence": evidence}, actor=actor)
                rejected = True
            else:
                rejected = False
            if rejected:
                pass
        if rejected:
            raise AccountError("forbidden_permission", 422)
        with self.db.service() as c:
            acct = c.execute("select * from public.yellow_accounts where id = %s", (account_id,)).fetchone()
            cred_ok_recent = acct["verification_reason"] == "permission_metadata_unavailable" and acct["balance_at"] is not None
            sets = ["permission_source = 'owner_attestation'", "permission_scopes = %s"]
            vals: list = [list(scopes)]
            if cred_ok_recent:
                sets += ["verification_state = %s", "verification_reason = 'owner_attestation'", "verified_at = now()"]
                vals.append(V_VERIFIED)
                if acct["state"] == S_PENDING:
                    sets += ["state = %s", "state_reason = null"]; vals.append(S_VERIFIED)
            vals.append(account_id)
            c.execute(f"update public.yellow_accounts set {', '.join(sets)} where id = %s", vals)
            self._event(c, account_id, acct["user_id"], "owner_permission_attestation",
                        {"scopes": list(scopes), "evidence": evidence, "applied_verified": cred_ok_recent}, actor=actor)
            return {"account_id": str(account_id), "verified": cred_ok_recent}

    # ------------------------------------------------------------ lifecycle
    def disconnect(self, ident: Identity, confirm_unmanaged: bool = False) -> dict:
        with self.db.service() as c:
            acct = self._get_owned(c, ident)
            n = self._open_positions(acct["id"])
            if n > 0 and not confirm_unmanaged:
                raise AccountError("open_positions", 409, {"open_positions": n})
            self._purge_credentials(c, acct["id"])
            c.execute("""update public.yellow_accounts set state=%s, state_reason=null, api_key_masked=null, app_session_id=null, verification_state=%s,
                         verification_reason=null, permission_source=null, permission_scopes=null, verified_at=null, balance_equity=null,
                         balance_available=null, balance_at=null, disconnected_at=now() where id=%s""", (S_DISCONNECTED, V_NONE, acct["id"]))
            c.execute("update public.yellow_bot_configs set entries_paused = true, pending_position_size_usd = null where account_id = %s", (acct["id"],))
            self._event(c, acct["id"], ident.user_id, "disconnected", {"open_positions_left_unmanaged": n})
        return self.get_me(ident)

    def set_trading_style(self, ident: Identity, style) -> dict:
        if style == 'scalp':
            raise AccountError('scalp_coming_soon', 409)
        if style not in ('swing', 'scalp'):
            raise AccountError('trading_style_not_allowed', 422)
        with self.db.service() as c:
            c.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (str(ident.user_id),))
            acct = self._ensure(c, ident)
            row = c.execute("select trading_style from public.yellow_bot_configs where account_id=%s for update", (acct['id'],)).fetchone()
            if row['trading_style'] != style:
                rt = self.runtime_info(str(acct['id']))
                weex = c.execute("select state from public.weex_accounts where user_id=%s", (ident.user_id,)).fetchone()
                if acct['state'] in (S_ACTIVE, S_DRAINING) or (weex and weex['state'] == 'ACTIVE'):
                    raise AccountError('trading_style_stop_first', 409)
                if acct['state'] != S_DISCONNECTED and (not rt.get('loaded') or rt.get('entries_allowed', True) or rt.get('open_positions', 0) or rt.get('ambiguous_orders', 0) or rt.get('pending_entries', 0)):
                    raise AccountError('trading_style_positions_open', 409)
                c.execute("update public.yellow_bot_configs set trading_style=%s where account_id=%s", (style, acct['id']))
                self._event(c, acct['id'], ident.user_id, 'trading_style_changed', {'trading_style': style})
        return self.get_me(ident)

    def activate(self, ident: Identity, trading_style=None) -> dict:
        with self.db.service() as c:
            c.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (str(ident.user_id),))
            acct = self._get_owned(c, ident)
            if acct["state"] not in (S_VERIFIED, S_PAUSED):
                raise AccountError("activation_not_allowed_from_state", 409, {"state": acct["state"]})
            if acct["verification_state"] != V_VERIFIED:
                raise AccountError("not_verified", 409)
            cfg = c.execute("select * from public.yellow_bot_configs where account_id = %s", (acct["id"],)).fetchone()
            if (trading_style is not None or cfg['trading_style']=='scalp') and trading_style != cfg['trading_style']:
                raise AccountError('trading_style_changed', 409)
            if cfg['trading_style'] not in ('swing', 'scalp'):
                raise AccountError('trading_style_not_allowed', 422)
            if cfg['trading_style']=='scalp':
                raise AccountError('scalp_coming_soon', 409)
                from .scalp.policy import protected_execution_required
                if protected_execution_required('yellow:'+str(acct['id'])):
                    raise AccountError('scalp_account_loss_policy',409)
            if cfg["pending_position_size_usd"] is not None and self._open_positions(acct["id"]) == 0:
                c.execute("update public.yellow_bot_configs set position_size_usd = pending_position_size_usd, pending_position_size_usd = null where account_id = %s", (acct["id"],))
                self._event(c, acct["id"], ident.user_id, "size_change_applied", {"position_size_usd": cfg["pending_position_size_usd"]})
            if (cfg["activated_at"] is None and cfg["trade_margin_pct"] is None
                    and not self._has_fixed_entry_override(acct["id"])):
                pct = recommend_trade_margin_pct(acct["balance_equity"])
                if pct is not None:
                    c.execute("update public.yellow_bot_configs set trade_margin_pct=%s where account_id=%s", (pct, acct["id"]))
                    self._event(c, acct["id"], ident.user_id, "size_agent_default_applied",
                                {"trade_margin_pct": pct, "balance_equity": _num(acct["balance_equity"])}, actor="system")
            c.execute("update public.yellow_accounts set state=%s, state_reason=null where id=%s", (S_ACTIVE, acct["id"]))
            c.execute("update public.yellow_bot_configs set entries_paused = false, activated_at = coalesce(activated_at, now()) where account_id = %s", (acct["id"],))
            self._event(c, acct["id"], ident.user_id, "activated")
        return self.get_me(ident)

    def pause(self, ident: Identity) -> dict:
        with self.db.service() as c:
            acct = self._get_owned(c, ident)
            if acct["state"] not in (S_ACTIVE, S_DRAINING):
                raise AccountError("pause_not_allowed_from_state", 409, {"state": acct["state"]})
            c.execute("update public.yellow_accounts set state=%s, state_reason=null where id=%s", (S_PAUSED, acct["id"]))
            c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (acct["id"],))
            self._event(c, acct["id"], ident.user_id, "paused")
        return self.get_me(ident)

    def set_position_size(self, ident: Identity, size) -> dict:
        """Legacy endpoint: live entries now size from venue funds and coin minima."""
        raise AccountError("live_sizing_automatic", 422)

    def set_trade_size(self, ident: Identity, data: dict) -> dict:
        """Owner-selected percentage for future core entries; no trading side effects."""
        if "trade_margin_pct" not in data:
            raise AccountError("trade_size_required", 422)
        pct = data["trade_margin_pct"]
        if pct is not None and (type(pct) is not int or not 1 <= pct <= 100):
            raise AccountError("trade_size_not_allowed", 422)
        with self.db.service() as c:
            acct = self._get_owned(c, ident)
            row = c.execute("select trade_margin_pct from public.yellow_bot_configs "
                            "where account_id=%s and user_id=%s for update",
                            (acct["id"], ident.user_id)).fetchone()
            if row is None:
                raise AccountError("account_not_found", 404)
            if row["trade_margin_pct"] != pct:
                c.execute("update public.yellow_bot_configs set trade_margin_pct=%s "
                          "where account_id=%s and user_id=%s", (pct, acct["id"], ident.user_id))
                self._event(c, acct["id"], ident.user_id, "trade_size_changed",
                            {"previous": row["trade_margin_pct"], "trade_margin_pct": pct})
        return self.get_me(ident)

    def set_position_limit(self, ident: Identity, limit) -> dict:
        """Save an owner's position cap; no orders, sizing or activation changes."""
        if type(limit) is not int or not 1 <= limit <= MAX_OPEN_POSITIONS:
            raise AccountError("position_limit_not_allowed", 422)
        with self.db.service() as c:
            acct = self._get_owned(c, ident)
            row = c.execute("select max_open_positions from public.yellow_bot_configs "
                            "where account_id=%s and user_id=%s for update",
                            (acct["id"], ident.user_id)).fetchone()
            if row is None:
                raise AccountError("account_not_found", 404)
            if row["max_open_positions"] != limit:
                c.execute("update public.yellow_bot_configs set max_open_positions=%s "
                          "where account_id=%s and user_id=%s", (limit, acct["id"], ident.user_id))
                self._event(c, acct["id"], ident.user_id, "position_limit_changed",
                            {"previous": row["max_open_positions"], "max_open_positions": limit})
        return self.get_me(ident)

    def cancel_size_change(self, ident: Identity) -> dict:
        with self.db.service() as c:
            acct = self._get_owned(c, ident)
            c.execute("update public.yellow_bot_configs set pending_position_size_usd = null where account_id = %s", (acct["id"],))
            if acct["state"] == S_DRAINING:
                c.execute("update public.yellow_accounts set state=%s, state_reason=null where id=%s", (S_ACTIVE, acct["id"]))
                c.execute("update public.yellow_bot_configs set entries_paused = false where account_id = %s", (acct["id"],))
            self._event(c, acct["id"], ident.user_id, "size_change_cancelled")
        return self.get_me(ident)

    # ------------------------------------------------------------ runner-facing (service role)
    @staticmethod
    def _ctx(row) -> CustomerExecutionContext:
        return CustomerExecutionContext(user_id=str(row["user_id"]), account_id=str(row["id"]),
                                        credential_ref=str(row["credential_ref"]) if row["credential_ref"] else None,
                                        position_size_usd=Decimal(row["position_size_usd"]), state=row["state"],
                                        entries_paused=bool(row["entries_paused"]),
                                        pending_position_size_usd=(Decimal(row["pending_position_size_usd"]) if row["pending_position_size_usd"] is not None else None),
                                        leverage=int(row["leverage"]), max_open_positions=int(row["max_open_positions"]),
                                        app_session_id=row["app_session_id"], trade_margin_pct=row.get("trade_margin_pct"), trading_style=row.get("trading_style", "swing"))

    def list_execution_contexts(self) -> List[CustomerExecutionContext]:
        with self.db.service() as c:
            rows = c.execute(f"""select a.id, a.user_id, a.credential_ref, a.state, a.app_session_id, b.position_size_usd, b.pending_position_size_usd,
                                 b.leverage, b.max_open_positions, b.entries_paused, b.trade_margin_pct, b.trading_style
                                 from public.yellow_accounts a join public.yellow_bot_configs b on b.account_id = a.id
                                 where a.state in ({', '.join(['%s'] * len(MANAGED_STATES))}) and a.credential_ref is not null order by a.created_at""",
                             list(MANAGED_STATES)).fetchall()
        return [self._ctx(r) for r in rows]

    def complete_drain(self, account_id: str) -> Optional[CustomerExecutionContext]:
        """Runner calls this once the tenant's book is flat. Applies the pending size and
        resumes entries. Returns the new context (or None if nothing to do)."""
        with self.db.service() as c:
            acct = c.execute("select * from public.yellow_accounts where id = %s", (account_id,)).fetchone()
            if acct is None or acct["state"] != S_DRAINING:
                return None
            cfg = c.execute("select * from public.yellow_bot_configs where account_id = %s", (account_id,)).fetchone()
            new_size = cfg["pending_position_size_usd"] if cfg["pending_position_size_usd"] is not None else cfg["position_size_usd"]
            c.execute("update public.yellow_bot_configs set position_size_usd = %s, pending_position_size_usd = null, entries_paused = false where account_id = %s",
                      (new_size, account_id))
            c.execute("update public.yellow_accounts set state = %s, state_reason = null where id = %s", (S_ACTIVE, account_id))
            self._event(c, account_id, acct["user_id"], "size_change_applied", {"position_size_usd": new_size}, actor="runner")
            row = c.execute("""select a.id, a.user_id, a.credential_ref, a.state, a.app_session_id, b.position_size_usd, b.pending_position_size_usd,
                               b.leverage, b.max_open_positions, b.entries_paused, b.trade_margin_pct, b.trading_style from public.yellow_accounts a
                               join public.yellow_bot_configs b on b.account_id = a.id where a.id = %s""", (account_id,)).fetchone()
            return self._ctx(row)

    def mark_error(self, account_id: str, reason: str) -> None:
        with self.db.service() as c:
            acct = c.execute("select user_id, state from public.yellow_accounts where id = %s", (account_id,)).fetchone()
            if acct is None:
                return
            c.execute("update public.yellow_accounts set state = %s, state_reason = %s where id = %s", (S_ERROR, reason[:200], account_id))
            c.execute("update public.yellow_bot_configs set entries_paused = true where account_id = %s", (account_id,))
            self._event(c, account_id, acct["user_id"], "error", {"reason": reason[:200]}, actor="runner")
