"""WEEX account lifecycle and Vault boundary, parallel to (not inside) Yellow."""
from __future__ import annotations

from decimal import Decimal

from customer.accounts import AccountError, fingerprint, mask_key
from customer.auth import Identity
from customer.config import ALLOWED_LEVERAGE, ALLOWED_POSITION_PCT, MAX_OPEN_POSITIONS
from customer.context import CustomerExecutionContext
from customer.log import register_secret
from customer.vault import SecretBox, Vault, VaultError
from .client import WeexClient, WeexError


class WeexCredential:
    def __init__(self, key: SecretBox, secret: SecretBox, passphrase: SecretBox):
        self.key, self.secret, self.passphrase = key, secret, passphrase
    def __repr__(self): return "WeexCredential(api_key=***, api_secret=***, passphrase=***)"


class WeexCredentialResolver:
    def __init__(self, db): self.db = db
    def resolve(self, ctx):
        with self.db.service() as c:
            r = c.execute("""select c.*,a.user_id from public.weex_credentials c join public.weex_accounts a on a.id=c.account_id
                             where c.id=%s and c.account_id=%s""", (ctx.credential_ref, ctx.account_id)).fetchone()
            if not r or str(r["user_id"]) != str(ctx.user_id): return None
            vals = [Vault.read(c, str(r[k])) for k in ("vault_api_key_id", "vault_api_secret_id", "vault_passphrase_id")]
        return WeexCredential(*vals) if all(vals) else None


class WeexAccountService:
    def __init__(self, db, subscriptions, cfg, runtime_info=None, client_factory=WeexClient):
        self.db, self.subscriptions, self.cfg = db, subscriptions, cfg
        self.runtime_info = runtime_info or (lambda aid: {"open_positions": 0, "loaded": False, "positions": []})
        self.client_factory = client_factory

    def _ensure(self, c, ident):
        r = c.execute("select * from public.weex_accounts where user_id=%s", (ident.user_id,)).fetchone()
        if r is None:
            r = c.execute("insert into public.weex_accounts(user_id) values (%s) returning *", (ident.user_id,)).fetchone()
        return r

    def _owned(self, c, ident):
        r = c.execute("select * from public.weex_accounts where user_id=%s", (ident.user_id,)).fetchone()
        if not r: raise AccountError("weex_account_not_found", 404)
        return r

    def get(self, ident):
        with self.db.service() as c: self._ensure(c, ident)
        with self.db.as_user(ident.user_id, ident.email) as c:
            a = c.execute("""select id,state,state_reason,api_key_masked,balance_equity,balance_available,
                              connected_at,allocation_pct,position_size_pct,leverage,risk_preset,strategy
                              from public.weex_accounts""").fetchone()
        rt = self.runtime_info(str(a["id"]))
        return {"id": str(a["id"]), "state": a["state"], "state_reason": a["state_reason"],
                "api_key_masked": a["api_key_masked"], "balance_equity": float(a["balance_equity"]) if a["balance_equity"] is not None else None,
                "balance_available": float(a["balance_available"]) if a["balance_available"] is not None else None,
                "connected_at": a["connected_at"], "settings": {"allocation_pct": float(a["allocation_pct"]),
                "position_size_pct": float(a["position_size_pct"]), "leverage": int(a["leverage"]),
                "risk_preset": a["risk_preset"], "strategy": a["strategy"]}, "runtime": rt,
                "status": "live" if self.cfg.weex_live_configured else "integrating"}

    def _client(self, key, secret, passphrase):
        return self.client_factory(key, secret, passphrase, base_url=self.cfg.weex_base_url,
                                   broker_id=self.cfg.weex_broker_id, timeout=self.cfg.yellow_timeout_s)

    def connect(self, ident, key, secret, passphrase):
        if not self.cfg.weex_live_configured:
            raise AccountError("weex_not_enabled", 409)
        ent = self.subscriptions.entitlement(ident.user_id)
        if not ent["live_allowed"]: raise AccountError("live_subscription_required", 403)
        if not all(isinstance(x, str) and 8 <= len(x) <= 512 and not any(ch.isspace() for ch in x) for x in (key, secret)) \
                or not isinstance(passphrase, str) or not (8 <= len(passphrase) <= 31) or not passphrase.isalnum():
            raise AccountError("invalid_weex_credential_format", 422)
        for x in (key, secret, passphrase): register_secret(x)
        # Validate reachability, read permission, futures permission, and balances before storage.
        try:
            client = self._client(key, secret, passphrase)
            info = client.exchange_info()
            eligible = client.api_trading_symbols()
            balances = client.balances()
            client.positions()
        except WeexError as e:
            raise AccountError("weex_credentials_rejected", 422, {"code": e.code}) from e
        symbols = [x.get("symbol") for x in (info or {}).get("symbols", []) if x.get("contractType") == "PERPETUAL" and x.get("symbol")]
        eligible = eligible if isinstance(eligible, list) else []
        trade_symbol = next((s for s in eligible if s in symbols), None)
        if not symbols or not trade_symbol or not isinstance(balances, list):
            raise AccountError("weex_futures_permission_required", 422)
        try:
            client.set_leverage(trade_symbol, 10)
        except WeexError as e:
            raise AccountError("weex_futures_permission_required", 422, {"code": e.code}) from e
        usdt = next((x for x in balances if x.get("asset") == "USDT"), None)
        if usdt is None: raise AccountError("weex_balance_unavailable", 422)
        with self.db.service() as c:
            a = self._ensure(c, ident)
            limit = ent["exchange_limit"]
            yellow_n = c.execute("select count(*) n from public.yellow_accounts where user_id=%s and state<>'DISCONNECTED'", (ident.user_id,)).fetchone()["n"]
            already = a["state"] != "DISCONNECTED"
            if limit is not None and yellow_n + (0 if already else 1) > limit:
                raise AccountError("exchange_limit_reached", 409, {"limit": limit})
            if c.execute("select 1 from public.weex_accounts where api_key_fingerprint=%s and id<>%s", (fingerprint(key), a["id"])).fetchone():
                raise AccountError("credential_already_bound", 409)
            old = c.execute("select * from public.weex_credentials where account_id=%s", (a["id"],)).fetchone()
            try:
                if old:
                    for col, value in (("vault_api_key_id",key),("vault_api_secret_id",secret),("vault_passphrase_id",passphrase)):
                        Vault.update(c, str(old[col]), value)
                    cid = old["id"]
                    c.execute("update public.weex_credentials set rotated_at=now() where id=%s", (cid,))
                else:
                    ids = [Vault.create(c, f"weex:{a['id']}:{n}", v, f"WEEX {n} (WHY customer)") for n,v in
                           (("api_key",key),("api_secret",secret),("passphrase",passphrase))]
                    cid = c.execute("""insert into public.weex_credentials(account_id,user_id,vault_api_key_id,vault_api_secret_id,vault_passphrase_id)
                                       values (%s,%s,%s,%s,%s) returning id""", (a["id"],ident.user_id,*ids)).fetchone()["id"]
            except VaultError as e: raise AccountError("vault_unavailable", 503) from e
            c.execute("""update public.weex_accounts set state='VERIFIED',state_reason=null,api_key_masked=%s,api_key_fingerprint=%s,
                         credential_ref=%s,balance_equity=%s,balance_available=%s,balance_at=now(),connected_at=now(),entries_paused=true,
                         updated_at=now() where id=%s""", (mask_key(key),fingerprint(key),cid,usdt.get("balance"),usdt.get("availableBalance"),a["id"]))
        return self.get(ident)

    def settings(self, ident, data):
        try: alloc, pos, lev = Decimal(str(data.get("allocation_pct"))), Decimal(str(data.get("position_size_pct"))), int(data.get("leverage"))
        except Exception as e: raise AccountError("invalid_risk_settings", 422) from e
        preset, strategy = str(data.get("risk_preset") or "custom").lower(), str(data.get("strategy") or "WHY Alpha")[:80]
        if not (1 <= alloc <= 100) or pos != int(pos) or int(pos) not in ALLOWED_POSITION_PCT or lev not in ALLOWED_LEVERAGE:
            raise AccountError("invalid_risk_settings", 422)
        if preset not in ("conservative","balanced","aggressive","custom"): raise AccountError("invalid_risk_preset", 422)
        with self.db.service() as c:
            a = self._owned(c, ident)
            c.execute("""update public.weex_accounts set allocation_pct=%s,position_size_pct=%s,leverage=%s,risk_preset=%s,
                         strategy=%s,updated_at=now() where id=%s""", (alloc,pos,lev,preset,strategy,a["id"]))
        return self.get(ident)

    def set_running(self, ident, running):
        with self.db.service() as c:
            c.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (str(ident.user_id),))
            a = self._owned(c, ident)
            if running:
                style=c.execute("select trading_style from public.yellow_bot_configs where user_id=%s",(ident.user_id,)).fetchone()
                if style and style['trading_style']=='scalp':raise AccountError('scalp_yellow_only',409)
            if running:
                if not self.cfg.weex_live_configured: raise AccountError("weex_not_enabled", 409)
                if not self.subscriptions.entitlement(ident.user_id)["live_allowed"]: raise AccountError("live_subscription_required", 403)
                if a["state"] not in ("VERIFIED","PAUSED"): raise AccountError("activation_not_allowed_from_state", 409)
                c.execute("update public.weex_accounts set state='ACTIVE',entries_paused=false,updated_at=now() where id=%s", (a["id"],))
            else:
                if a["state"] != "ACTIVE": raise AccountError("pause_not_allowed_from_state", 409)
                c.execute("update public.weex_accounts set state='PAUSED',entries_paused=true,updated_at=now() where id=%s", (a["id"],))
        return self.get(ident)

    def disconnect(self, ident, confirm_unmanaged=False):
        with self.db.service() as c:
            a = self._owned(c, ident)
            rt = self.runtime_info(str(a["id"]))
            if int(rt.get("open_positions", 0) or 0) > 0 and not confirm_unmanaged:
                raise AccountError("open_positions_managed", 409)
            cred = c.execute("select * from public.weex_credentials where account_id=%s", (a["id"],)).fetchone()
            if cred:
                for col in ("vault_api_key_id", "vault_api_secret_id", "vault_passphrase_id"):
                    Vault.delete(c, str(cred[col]))
                c.execute("delete from public.weex_credentials where id=%s", (cred["id"],))
            c.execute("""update public.weex_accounts set state='DISCONNECTED',state_reason=null,api_key_masked=null,
                         api_key_fingerprint=null,credential_ref=null,balance_equity=null,balance_available=null,
                         balance_at=null,connected_at=null,entries_paused=true,updated_at=now() where id=%s""", (a["id"],))
        return self.get(ident)

    def list_execution_contexts(self):
        if not self.cfg.weex_live_configured: return []
        with self.db.service() as c:
            rows = c.execute("""select a.*,coalesce(b.trading_style, 'swing')='swing' live_entries_allowed
                                from public.weex_accounts a left join public.yellow_bot_configs b on b.user_id=a.user_id
                                where a.credential_ref is not null and a.state in ('VERIFIED','ACTIVE','PAUSED','ERROR')""").fetchall()
        out = []
        for r in rows:
            equity = Decimal(r["balance_equity"]) if r["balance_equity"] is not None else Decimal(0)
            requested = equity * Decimal(r["allocation_pct"]) / 100 * Decimal(r["position_size_pct"]) / 100 * int(r["leverage"])
            out.append(CustomerExecutionContext(str(r["user_id"]), str(r["id"]), str(r["credential_ref"]),
                min(Decimal("2000"), requested) if requested > 0 else Decimal("2000"), r["state"],
                bool(r["entries_paused"]) or not bool(r["live_entries_allowed"]), leverage=int(r["leverage"]),
                max_open_positions=MAX_OPEN_POSITIONS, venue="weex"))
        return out

    def complete_drain(self, account_id): return None
    def mark_error(self, account_id, reason):
        with self.db.service() as c:
            c.execute("update public.weex_accounts set state='ERROR',state_reason=%s,entries_paused=true where id=%s", (str(reason)[:200],account_id))
