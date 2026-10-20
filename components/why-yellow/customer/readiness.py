"""Readiness that distinguishes layers instead of 'the web process is alive'."""
from __future__ import annotations
import os
import threading
import time
from typing import Callable, Optional

from .log import safe_exc
from .vault import Vault

YELLOW_PUBLIC_URL = os.environ.get("YELLOW_TRADE_BASE", "https://trade.api.yellow.pro") + "/perpetual/exchangeInfo"


class Readiness:
    def __init__(self, cfg, db=None, registry=None, feed=None, runner_alive: Optional[Callable[[], bool]] = None,
                 yellow_probe: Optional[Callable[[], bool]] = None, clock: Callable[[], float] = time.time, cache_s: float = 15.0, auth_probe=None):
        self.cfg, self.db, self.registry, self.feed = cfg, db, registry, feed
        self.runner_alive = runner_alive or (lambda: False)
        self.yellow_probe = yellow_probe or self._default_probe
        self.auth_probe = auth_probe or self._default_auth_probe
        self.clock, self.cache_s = clock, cache_s
        self._cache: Optional[dict] = None
        self._cache_ts = 0.0
        self._lock = threading.Lock()
        self.started_ts = clock()

    @staticmethod
    def _default_probe() -> bool:
        try:
            import httpx
            r = httpx.get(YELLOW_PUBLIC_URL, timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    def _default_auth_probe(self):
        if not self.cfg.supabase_configured:
            return False, "not_configured"
        try:
            import httpx
            response = httpx.get(self.cfg.supabase_url + "/auth/v1/health",
                                headers={"apikey": self.cfg.supabase_anon_key}, timeout=5.0)
            if response.status_code == 402:
                return False, "provider_quota_or_billing_restriction"
            return response.status_code == 200, ("ok" if response.status_code == 200 else "auth_http_" + str(response.status_code))
        except Exception:
            return False, "auth_service_unavailable"

    def snapshot(self, force: bool = False) -> dict:
        with self._lock:
            if not force and self._cache is not None and self.clock() - self._cache_ts < self.cache_s:
                return self._cache
            snap = self._compute()
            self._cache, self._cache_ts = snap, self.clock()
            return snap

    def _compute(self) -> dict:
        d: dict = {}
        app = True
        supa, supa_why = False, "not_configured"
        vault, vault_why = False, "not_configured"
        verified_accounts = 0
        if self.cfg.supabase_configured and self.db is not None:
            supa, supa_why = self.db.ping()
            if supa:
                try:
                    with self.db.service() as c:
                        vault, vault_why = Vault.health(c)
                        verified_accounts = int(c.execute("select count(*) as n from public.yellow_accounts where verification_state = 'VERIFIED'").fetchone()["n"])
                except Exception as e:
                    vault, vault_why = False, safe_exc(e)
        auth_ok, auth_reason = self.auth_probe()
        yellow_public = bool(self.yellow_probe())
        feed_status = self.feed.status() if self.feed else None
        feed_ok = True if self.feed is None else bool(feed_status.get("ready", False))
        yellow_connected = yellow_public and feed_ok
        armed = os.environ.get("YELLOW_ARMED") == "1"
        runner = bool(self.runner_alive())
        execution_ready = bool(app and supa and vault and yellow_connected and runner and armed)
        tenants = self.registry.status() if self.registry else None
        paper_ready = bool((tenants or {}).get("paper", {}).get("ready", True))
        d.update(
            APP_HEALTHY=app, SUPABASE_HEALTHY=bool(supa), VAULT_HEALTHY=bool(vault), YELLOW_CONNECTED=yellow_connected,
            YELLOW_VERIFIED=verified_accounts > 0, EXECUTION_READY=execution_ready,
            SUPABASE_AUTH_HEALTHY=bool(auth_ok), CUSTOMER_ACCESS_READY=bool(supa and auth_ok),
            PAPER_READY=paper_ready,
            ready=bool(execution_ready and auth_ok and paper_ready),
            READINESS_SCOPE="Overall readiness includes customer authentication and paper processing; EXECUTION_READY describes existing-account execution infrastructure, not signal eligibility",
            detail=dict(supabase=supa_why, supabase_auth=auth_reason, vault=vault_why, yellow_public_api=yellow_public, feed=feed_status,
                        verified_accounts=verified_accounts, runner_alive=runner, armed=armed,
                        tenants=tenants, uptime_s=int(self.clock() - self.started_ts)),
        )
        return d
