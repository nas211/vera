"""Shared fixtures for the execution-safety test suite. Installs a Python audit hook
that raises on any socket/subprocess use, so a real venue call is impossible."""
from __future__ import annotations
import os, sys, tempfile, threading, time
from decimal import Decimal
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from execution.engine import EngineConfig, ExecutionEngine
from execution.ledger import Ledger
from execution.market_data import FreshnessMonitor
from execution.metadata import MarketFilters, StaticMetadata
from execution.mock_adapter import MockYellowAdapter
from execution.sizing import ExposureLimits
from execution.types import EntrySignal
from execution.venue_state import VenueMonitor

NETWORK_ATTEMPTS = {"count": 0}


def _hook(event, args):
    if event == "socket.__new__":
        # bare socket object creation (e.g. urllib3's import-time IPv6 capability probe); no
        # address, no connection. Recorded, not counted, not denied.
        NETWORK_ATTEMPTS.setdefault("sockets_created", []).append(str(args)[:120])
        return
    if event == "socket.bind" and len(args) > 1 and isinstance(args[1], tuple) and args[1] and str(args[1][0]) in ("::1", "127.0.0.1", "localhost"):
        # urllib3 import-time IPv6 capability probe binds to loopback; denied, recorded, not a network attempt
        NETWORK_ATTEMPTS.setdefault("loopback_probes_denied", []).append(str(args)[:120])
        raise RuntimeError("TEST HARNESS DENIED " + event)
    if event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn")):
        NETWORK_ATTEMPTS["count"] += 1; NETWORK_ATTEMPTS.setdefault("events", []).append((event, str(args)[:200]))
        raise RuntimeError("TEST HARNESS DENIED " + event)


if not getattr(sys, "_why_audit_hook", False):
    sys.addaudithook(_hook)
    sys._why_audit_hook = True

assert "YELLOW_ARMED" not in os.environ, "tests must never run with YELLOW_ARMED set"


class FakeClock:
    def __init__(self, start=1_790_100_000.0):
        self.now = float(start)
        self._lock = threading.Lock()

    def __call__(self):
        return self.now

    def advance(self, s):
        with self._lock:
            self.now += float(s)
        return self.now


BTC = MarketFilters("BTCUSDT-PERP", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), Decimal("20"), "TRADING")
ETH = MarketFilters("ETHUSDT-PERP", Decimal("0.01"), Decimal("0.01"), Decimal("0.01"), Decimal("10"), Decimal("20"), "TRADING")
HALT = MarketFilters("HALTUSDT-PERP", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("10"), Decimal("20"), "HALTED")
ACCT = "acct-A"


def make_metadata():
    return StaticMetadata({"BTCUSDT-PERP": BTC, "ETHUSDT-PERP": ETH, "HALTUSDT-PERP": HALT})


class Env:
    """One engine + mock venue + ledger on a temp dir. `restart()` builds a fresh engine
    on the SAME ledger file and mock venue (process restart simulation)."""
    def __init__(self, page_size=2, clock=None, config=None, capabilities=None, dedupe=False):
        self.dir = tempfile.mkdtemp(prefix="why-exec-test-")
        self.clock = clock or FakeClock()
        self.mock = MockYellowAdapter(page_size=page_size, clock=self.clock, capabilities=capabilities, dedupe_client_order_id=dedupe)
        self.mock.set_account(ACCT, "100000", "100000")
        self.meta = make_metadata()
        self.config = config or EngineConfig(leverage=10, limits=ExposureLimits(Decimal("2000"), Decimal("16000"), 8),
                                             unknown_grace_s=5.0, protection_timeout_s=60.0, protection_max_attempts=3, accounts=[ACCT])
        self.build()

    def build(self):
        self.ledger = Ledger(os.path.join(self.dir, "ledger.sqlite"), switches_mirror=os.path.join(self.dir, "exec_switches.json"), clock=self.clock)
        self.fresh = FreshnessMonitor(bar_interval_ms=300_000, max_market_data_age_s=30.0, clock=self.clock)
        self.venue = VenueMonitor(clock=self.clock)
        self.eng = ExecutionEngine(self.ledger, self.mock, self.meta, self.fresh, self.venue, self.config, clock=self.clock)
        return self.eng

    def restart(self):
        self.ledger.close()
        self.build()
        return self.eng.start()

    def feed(self, market="BTCUSDT-PERP", price="77000"):
        now = self.clock()
        base = int(now * 1000) // 300_000 * 300_000
        self.fresh.seed(market, [base - i * 300_000 for i in range(260, 0, -1)])
        self.fresh.on_bar_close(market, base, now)
        self.eng.on_mark(market, price, now)

    def ready(self, entries=True):
        self.feed("BTCUSDT-PERP"); self.feed("ETHUSDT-PERP", "2500")
        rep = self.eng.start()
        assert rep.ok, rep
        self.eng.set_switches(allow_new_entries=entries, manage_existing_positions=True, reason="test")
        return self.eng

    def signal(self, sid="sig-1", market="BTCUSDT-PERP", direction="long", strategy="WY-REVERT", notional="2000", price="77000"):
        return EntrySignal(sid, ACCT, market, direction, strategy, Decimal(notional), Decimal(price), self.clock())

    def order(self, coid):
        return self.ledger.get_order(coid)

    def pos(self, pid):
        return self.ledger.get_position(pid)

    def open_and_fill(self, sid="sig-1", price="77000", **kw):
        """Submit an entry, fill it fully on the mock, deliver the fill. Returns (coid, result)."""
        r = self.eng.submit_entry(self.signal(sid, price=price, **kw))
        assert r.status == "ACKNOWLEDGED", r
        o = self.order(r.client_order_id)
        t = self.mock.fill(r.client_order_id, o["qty"], price)
        self.eng.on_fill(t)
        return r.client_order_id, r
