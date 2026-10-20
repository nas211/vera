"""why-yellow-customer.service entry point: API + tenant runner + market feed.

Trading can only happen when ALL of these hold, and none is set by this module:
  YELLOW_ARMED=1 in the environment, state/exec_switches.json allow_new_entries (owner
  global brake), the customer's account ACTIVE with entries not paused, the venue READY
  after reconciliation, and fresh market data. With no Supabase configuration the API
  still answers /health and /public-config truthfully and nothing else."""
from __future__ import annotations
import os
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from .accounts import AccountService
from .api import Api, create_server
from .auth import Verifier
from .config import Config
from .context import LazyYellowClient, TenantYellowTransport, VaultCredentialResolver
from .db import Database
from .fanout import JournalSignalSource
from .feed import YellowMarketFeed
from .log import get_logger, safe_exc
from .readiness import Readiness
from .tenants import CanonicalSignal, CloseSignal, TenantRegistry
from .yellow_probe import YellowVerifier

log = get_logger("service")
MARKETS = ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP", "XRPUSDT-PERP", "DOGEUSDT-PERP",
           "BNBUSDT-PERP", "LINKUSDT-PERP", "SUIUSDT-PERP", "TAOUSDT-PERP", "HYPEUSDT-PERP"]


class Runner(threading.Thread):
    def __init__(self, cfg: Config, registry: TenantRegistry, source: JournalSignalSource):
        super().__init__(name="tenant-runner", daemon=True)
        self.cfg, self.registry, self.source = cfg, registry, source
        self.stop_event = threading.Event()
        self.last_tick = 0.0
        self.last_manage = 0.0
        self.dispatched = 0
        from .ai_gateway import AIGateway
        self.ai_gateway = AIGateway()
        from .discovery_gateway import DiscoveryGateway
        self.discovery_gateway = DiscoveryGateway()

    def alive(self) -> bool:
        return self.is_alive() and time.time() - self.last_tick < 60.0

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.last_tick = time.time()
            try:
                if time.time() - self.last_manage >= self.cfg.manage_interval_s:
                    self.registry.refresh()
                    self.registry.manage_all()
                    self.last_manage = time.time()
                core_markets = set()
                for sig in self.source.poll():
                    if isinstance(sig, CanonicalSignal):
                        core_markets.add(sig.market)
                        out = self.registry.dispatch(sig)
                        self.dispatched += 1
                        log.info("signal %s -> %s", sig.signal_id, out)
                    elif isinstance(sig, CloseSignal):
                        log.info("close %s %s %s -> %s", sig.market, sig.direction, sig.reason, self.registry.dispatch_close(sig))
                confirmed = self.registry.dispatch_confirmed()
                if confirmed:
                    log.info("confirmed entries -> %s", confirmed)
                discovery_signals = self.discovery_gateway.poll(self.registry.yellow.discovery_contexts())
                self.registry.yellow.discovery_status = self.discovery_gateway.status()
                for sig in discovery_signals:
                    out=self.registry.yellow.dispatch_discovery(sig)
                    log.info('Discovery signal %s -> %s',sig.signal_id,out)
                # The public research worker never receives exchange credentials.
                # Yellow forward evidence cannot authorise another venue.
                for sig in self.ai_gateway.poll(core_markets):
                    out = self.registry.dispatch_ai(sig, live_enabled=self.ai_gateway.live_enabled,
                                                    paper_enabled=self.ai_gateway.paper_enabled)
                    log.info('AI signal %s -> %s', sig.signal_id, out)
            except Exception as e:
                log.error("runner loop error: %s", safe_exc(e))
            self.stop_event.wait(2.0)


def build(cfg: Config):
    db = verifier = accounts = registry = feed = runner = None
    subs = results = paper = weex_accounts = billing = None
    if cfg.supabase_configured:
        db = Database(cfg.supabase_db_url)
        verifier = Verifier(cfg.supabase_url, cfg.supabase_jwt_secret)
        from .subscriptions import SubscriptionService
        subs = SubscriptionService(db)
        from execution.market_data import FreshnessMonitor
        from execution.metadata import AdapterMetadata
        from yellow_exec import YellowClient
        fresh = FreshnessMonitor(bar_interval_ms=300_000, max_market_data_age_s=30.0)
        metadata = AdapterMetadata(YellowClient())          # public exchangeInfo only
        resolver = VaultCredentialResolver(db)
        registry_holder = {}
        accounts = AccountService(db, YellowVerifier(), state_dir=cfg.state_dir,
            runtime_info=lambda aid: registry_holder.get("router", registry_holder["yellow"]).runtime_info(aid) if "yellow" in registry_holder else {"open_positions": 0, "ambiguous_orders": 0, "loaded": False})
        from .live_entry_filter import AccountEntryFilters
        yellow_registry = TenantRegistry(accounts, lambda ctx: TenantYellowTransport(LazyYellowClient(resolver, ctx)), metadata, fresh, cfg.state_dir, entry_filter=AccountEntryFilters())
        registry_holder["yellow"] = yellow_registry

        from .paper import PaperService
        from .router import ExecutionRouter
        from .results import ResultsReader
        from .billing import BillingService
        paper = PaperService(db, subs)
        def history_client(aid):
            ctx = next((ctx for ctx in accounts.list_execution_contexts() if str(ctx.account_id) == str(aid)), None)
            return LazyYellowClient(resolver, ctx).ensure() if ctx else None
        results = ResultsReader(os.path.join(cfg.state_dir, "tenants"), history_client=history_client)
        billing = BillingService(db, cfg)

        weex_registry = None
        from weex.service import WeexAccountService, WeexCredentialResolver
        from weex.client import WeexClient
        from weex.transport import WeexMetadata, WeexTransport
        weex_holder = {}
        weex_accounts = WeexAccountService(db, subs, cfg,
            runtime_info=lambda aid: weex_holder["r"].runtime_info(aid) if "r" in weex_holder else {"open_positions": 0, "ambiguous_orders": 0, "loaded": False})
        if cfg.weex_live_configured:
            public_weex = WeexClient(base_url=cfg.weex_base_url, broker_id=cfg.weex_broker_id)
            weex_resolver = WeexCredentialResolver(db)
            def weex_transport(ctx):
                cred = weex_resolver.resolve(ctx)
                if cred is None:
                    return WeexTransport(WeexClient(base_url=cfg.weex_base_url, broker_id=cfg.weex_broker_id))
                return WeexTransport(WeexClient(cred.key.reveal(), cred.secret.reveal(), cred.passphrase.reveal(),
                                                base_url=cfg.weex_base_url, broker_id=cfg.weex_broker_id,
                                                timeout=cfg.yellow_timeout_s))
            weex_registry = TenantRegistry(weex_accounts, weex_transport, WeexMetadata(public_weex), fresh,
                                           os.path.join(cfg.state_dir, "weex"))
            weex_holder["r"] = weex_registry
        from .scalp.registry import ScalpRegistry
        from .scalp.transport import ScalpYellowTransport
        scalp = ScalpRegistry(accounts, lambda ctx: ScalpYellowTransport(LazyYellowClient(resolver, ctx)), metadata, cfg.state_dir)
        from .paper_worker import PaperWorker
        registry = ExecutionRouter(yellow_registry, paper=PaperWorker(paper), weex=weex_registry, scalp=scalp)
        registry_holder['router'] = registry
        if cfg.feed_enabled:
            feed = YellowMarketFeed(MARKETS, fresh, registry)
        if cfg.runner_enabled:
            os.makedirs(cfg.state_dir, exist_ok=True)
            runner = Runner(cfg, registry, JournalSignalSource(cfg.journal_path, os.path.join(cfg.state_dir, "fanout_offset.json"), max_age_s=cfg.max_signal_age_s))
    readiness = Readiness(cfg, db, registry, feed, runner_alive=(runner.alive if runner else (lambda: False)))
    api = Api(cfg, verifier, accounts, readiness, registry, subscriptions=subs, results=results,
              paper=paper, weex=weex_accounts, billing=billing)
    return api, feed, runner


def main() -> int:
    cfg = Config.from_env()
    if "YELLOW_ARMED" in os.environ:
        log.warning("YELLOW_ARMED is set in the customer service environment (owner-armed process)")
    api, feed, runner = build(cfg)
    srv = create_server(api, cfg.api_host, cfg.api_port)
    if feed:
        feed.start()
    if runner:
        runner.start()
    log.info("customer API on %s:%s supabase_configured=%s runner=%s feed=%s", cfg.api_host, cfg.api_port, cfg.supabase_configured, bool(runner), bool(feed))

    def stop(*_):
        log.info("shutting down")
        if runner:
            runner.stop_event.set()
        if feed:
            feed.stop_event.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    srv.serve_forever()
    if api.registry and hasattr(api.registry.paper, "shutdown"):
        api.registry.paper.shutdown()
    if api.registry and api.registry.scalp:
        api.registry.scalp.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
