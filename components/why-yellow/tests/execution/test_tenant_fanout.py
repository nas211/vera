"""Multi-tenant execution over the mock venue (no network): tenant-namespaced idempotency,
failure isolation, drain/transition sizing, restart/reconciliation with tenant context."""
import os, shutil, tempfile, unittest
from decimal import Decimal
from .harness import FakeClock, make_metadata, NETWORK_ATTEMPTS
from execution.market_data import FreshnessMonitor
from execution.metadata import MarketFilters
from execution.mock_adapter import MockYellowAdapter
from execution.engine import EngineConfig
from execution.ledger import make_coid
from execution.sizing import ExposureLimits
from customer.context import CustomerExecutionContext
from customer.tenants import CanonicalSignal, CloseSignal, TenantRegistry


class FakeAccounts:
    """In-memory stand-in for AccountService's runner-facing surface."""
    def __init__(self):
        self.ctx = {}
        self.drains = []

    def set(self, ctx):
        self.ctx[ctx.account_id] = ctx

    def list_execution_contexts(self):
        return list(self.ctx.values())

    def complete_drain(self, account_id):
        c = self.ctx[account_id]
        if c.state != "DRAINING":
            return None
        new = CustomerExecutionContext(c.user_id, c.account_id, c.credential_ref, c.pending_position_size_usd or c.position_size_usd, "ACTIVE",
                                       entries_paused=False, pending_position_size_usd=None, app_session_id=c.app_session_id)
        self.ctx[account_id] = new
        self.drains.append(account_id)
        return new


def ctx(aid, size, state="ACTIVE", paused=False, pending=None, user=None):
    return CustomerExecutionContext(user_id=user or f"user-{aid}", account_id=aid, credential_ref=f"cred-{aid}", position_size_usd=Decimal(size),
                                    state=state, entries_paused=paused, pending_position_size_usd=(Decimal(pending) if pending else None))


class BrokenTransport:
    capabilities = MockYellowAdapter().capabilities
    def __getattr__(self, name):
        raise RuntimeError("tenant transport exploded")


class Env:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="why-tenant-")
        self.clock = FakeClock()
        self.mocks = {}
        self.accounts = FakeAccounts()
        self.meta = make_metadata()
        self.fresh = FreshnessMonitor(bar_interval_ms=300_000, max_market_data_age_s=30.0, clock=self.clock)
        self.broken = set()
        self.build()

    def transport(self, c):
        if c.account_id in self.broken:
            return BrokenTransport()
        m = self.mocks.get(c.account_id)
        if m is None:
            m = MockYellowAdapter(page_size=2, clock=self.clock)
            m.set_account(c.namespace, "100000", "100000")
            self.mocks[c.account_id] = m
        return m

    def build(self):
        self.reg = TenantRegistry(self.accounts, self.transport, self.meta, self.fresh, self.dir, clock=self.clock,
                                  engine_config_factory=lambda: EngineConfig(unknown_grace_s=5.0, protection_timeout_s=60.0, protection_max_attempts=3))
        return self.reg

    def feed(self, market="BTCUSDT-PERP", price="77000"):
        now = self.clock()
        base = int(now * 1000) // 300_000 * 300_000
        self.fresh.seed(market, [base - i * 300_000 for i in range(260, 0, -1)])
        self.fresh.on_bar_close(market, base, now)
        self.fresh.on_mark(market, float(price), now)
        self.reg.on_mark(market, price, now)

    def ready(self):
        self.reg.refresh()
        self.feed("BTCUSDT-PERP"); self.feed("ETHUSDT-PERP", "2500")
        self.reg.manage_all()   # reconcile -> READY
        return self.reg

    def sig(self, sid="sig-1", market="BTCUSDT-PERP", direction="long", strategy="WY-REVERT", price="77000"):
        return CanonicalSignal(sid, market, direction, strategy, Decimal(price), self.clock())

    def fill_entries(self, price="77000"):
        for aid, t in self.reg.tenants.items():
            m = self.mocks[aid]
            for o in t.ledger.orders_where([__import__("execution.types", fromlist=["OrderState"]).OrderState.ACKNOWLEDGED], purpose="ENTRY"):
                tr = m.fill(o["client_order_id"], o["qty"], price)
                t.eng.on_fill(tr)

    def cleanup(self):
        for t in list(self.reg.tenants.values()):
            t.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)


class TestTenantFanout(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.e.accounts.set(ctx("acctA", 100)); self.e.accounts.set(ctx("acctB", 2000))
        self.e.ready()

    def tearDown(self):
        self.e.cleanup()

    def test_available_margin_controls_coin_minimum(self):
        self.e.cleanup()
        self.e = Env()
        self.e.accounts.set(ctx("acctA", 20))
        self.e.ready()
        a = self.e.reg.tenants["acctA"]
        self.assertEqual((a.eng.cfg.limits.max_notional_per_order, a.eng.cfg.limits.max_total_notional), (Decimal('Infinity'), Decimal('Infinity')))
        self.e.mocks['acctA'].set_account(a.ctx.namespace, '2', '2')
        self.assertEqual(self.e.reg.dispatch(self.e.sig("btc", price="77000"))["acctA"],
                         "REJECTED:sizing:insufficient_available_balance")
        self.assertEqual(self.e.mocks["acctA"].place_calls, 0)
        self.e.mocks['acctA'].set_account(a.ctx.namespace, '100', '100')
        self.assertTrue(self.e.reg.dispatch(self.e.sig("eth", market="ETHUSDT-PERP", price="2500"))["acctA"].startswith('ACKNOWLEDGED'))

    def test_user_position_limit_refresh_blocks_second_entry(self):
        from dataclasses import replace
        current = self.e.accounts.ctx["acctA"]
        self.e.accounts.set(replace(current, max_open_positions=1))
        self.e.reg.refresh()
        first = self.e.reg.dispatch(self.e.sig("one-only"))
        self.assertTrue(first["acctA"].startswith("ACKNOWLEDGED"), first)
        # An unfilled entry reserves the slot as well.
        second = self.e.reg.dispatch(self.e.sig("blocked-pending", market="ETHUSDT-PERP", price="2500"))
        self.assertEqual(second["acctA"], "REJECTED:sizing:max_open_positions")
        self.e.fill_entries()
        second = self.e.reg.dispatch(self.e.sig("blocked-filled", market="ETHUSDT-PERP", price="2500"))
        self.assertEqual(second["acctA"], "REJECTED:sizing:max_open_positions")
        self.assertEqual(len(self.e.reg.tenants["acctA"].open_positions()), 1)
        self.assertEqual(self.e.reg.tenants["acctB"].eng.cfg.limits.max_open_positions, 8)

    def test_lowering_limit_keeps_existing_positions_managed(self):
        from dataclasses import replace
        first = self.e.reg.dispatch(self.e.sig("existing"))
        self.e.fill_entries()
        tenant = self.e.reg.tenants["acctA"]
        before = tenant.open_positions()
        self.e.accounts.set(replace(self.e.accounts.ctx["acctA"], max_open_positions=1))
        self.e.reg.refresh()
        self.assertEqual(tenant.open_positions(), before)
        self.assertTrue(tenant.ledger.get_switches().manage_existing_positions)
        result = self.e.reg.dispatch(self.e.sig("new-after-cap", market="ETHUSDT-PERP", price="2500"))
        self.assertEqual(result["acctA"], "REJECTED:sizing:max_open_positions")

    def test_maximum_eight_open_positions(self):
        self.e.cleanup()
        self.e = Env()
        self.e.accounts.set(ctx("acctA", 20))
        self.e.ready()
        for i in range(9):
            market = f"COIN{i}USDT-PERP"
            self.e.meta._f[market] = MarketFilters(market, Decimal("0.01"), Decimal("1"), Decimal("5"),
                                                    Decimal("5"), Decimal("20"), "TRADING")
            self.e.feed(market, "1")
            out = self.e.reg.dispatch(self.e.sig(f"five-{i}", market=market, price="1"))
            if i < 8:
                self.assertTrue(out["acctA"].startswith("ACKNOWLEDGED"), out)
                self.e.fill_entries(price="1")
            else:
                self.assertEqual(out["acctA"], "REJECTED:sizing:max_open_positions")
        self.assertEqual(len(self.e.reg.tenants["acctA"].open_positions()), 8)

    def test_configured_size_does_not_cap_live_trades(self):
        self.e.cleanup()
        self.e = Env()
        self.e.accounts.set(ctx("acctA", 20))
        self.e.ready()
        for market in ("ONEUSDT-PERP", "TWOUSDT-PERP"):
            self.e.meta._f[market] = MarketFilters(market, Decimal("0.01"), Decimal("1"), Decimal("20"),
                                                    Decimal("20"), Decimal("20"), "TRADING")
            self.e.feed(market, "1")
        first = self.e.reg.dispatch(self.e.sig("twenty-1", market="ONEUSDT-PERP", price="1"))
        self.assertTrue(first["acctA"].startswith("ACKNOWLEDGED"), first)
        self.e.fill_entries(price="1")
        second = self.e.reg.dispatch(self.e.sig("twenty-2", market="TWOUSDT-PERP", price="1"))
        self.assertTrue(second["acctA"].startswith('ACKNOWLEDGED'), second)
        self.e.fill_entries(price="1")
        self.assertEqual(len(self.e.reg.tenants["acctA"].open_positions()), 2)

    def test_same_signal_two_tenants_two_orders_distinct_coids_isolated_ledgers(self):
        out = self.e.reg.dispatch(self.e.sig("sig-1"))
        self.assertEqual(out, {"acctA": "ACKNOWLEDGED:v-1", "acctB": "ACKNOWLEDGED:v-1"})
        cA = make_coid("yellow:acctA", "sig-1", "ENTRY", "BTCUSDT-PERP", "long"); cB = make_coid("yellow:acctB", "sig-1", "ENTRY", "BTCUSDT-PERP", "long")
        self.assertNotEqual(cA, cB)
        tA, tB = self.e.reg.tenants["acctA"], self.e.reg.tenants["acctB"]
        self.assertIsNotNone(tA.ledger.get_order(cA)); self.assertIsNone(tA.ledger.get_order(cB))
        self.assertIsNotNone(tB.ledger.get_order(cB)); self.assertIsNone(tB.ledger.get_order(cA))
        self.assertNotEqual(tA.ledger.path, tB.ledger.path)
        # Both accounts use this coin's same minimum lot.
        qA, qB = Decimal(tA.ledger.get_order(cA)["qty"]), Decimal(tB.ledger.get_order(cB)["qty"])
        self.assertEqual((qA, qB), (Decimal("0.001"), Decimal("0.001")))
        self.assertEqual((self.e.mocks["acctA"].place_calls, self.e.mocks["acctB"].place_calls), (1, 1))
        # replay of the same canonical signal: duplicates everywhere, no venue calls
        out = self.e.reg.dispatch(self.e.sig("sig-1"))
        self.assertTrue(all(v.startswith("DUPLICATE") for v in out.values()), out)
        self.assertEqual((self.e.mocks["acctA"].place_calls, self.e.mocks["acctB"].place_calls), (1, 1))

    def test_paused_or_non_active_tenant_skipped(self):
        self.e.accounts.set(ctx("acctB", 2000, paused=True)); self.e.reg.refresh()
        out = self.e.reg.dispatch(self.e.sig("sig-2"))
        self.assertEqual(out["acctB"], "SKIPPED:entries_not_wanted"); self.assertTrue(out["acctA"].startswith("ACKNOWLEDGED"))
        self.assertFalse(self.e.reg.tenants["acctB"].ledger.get_switches().allow_new_entries)
        self.assertTrue(self.e.reg.tenants["acctB"].ledger.get_switches().manage_existing_positions)

    def test_one_tenant_failure_does_not_touch_the_other(self):
        self.e.mocks["acctA"].script_place("http_503")          # ambiguous -> UNKNOWN for A
        out = self.e.reg.dispatch(self.e.sig("sig-3"))
        self.assertEqual(out["acctA"], "UNKNOWN:reconciliation_required"); self.assertTrue(out["acctB"].startswith("ACKNOWLEDGED"))
        self.assertEqual(self.e.reg.tenants["acctB"].venue.state.value, "READY")
        self.assertEqual(self.e.reg.tenants["acctA"].venue.state.value, "DEGRADED")
        # A's transport now explodes on every call; B keeps working; registry records, never raises
        self.e.broken.add("acctA"); self.e.accounts.set(ctx("acctA", 100, user="user-acctA"))
        self.e.reg.tenants["acctA"].eng.t = BrokenTransport()
        out = self.e.reg.dispatch(self.e.sig("sig-4", market="ETHUSDT-PERP", price="2500"))
        self.assertIn(out["acctA"], ("ERROR", "BLOCKED:venue_not_ready:DEGRADED")); self.assertTrue(out["acctB"].startswith("ACKNOWLEDGED"))
        self.e.reg.manage_all()
        self.assertGreaterEqual(self.e.reg.tenants["acctA"].failures + len(self.e.reg.errors), 1)
        self.assertTrue(self.e.reg.tenants["acctB"].last_reconcile_ok)

    def test_size_change_drains_without_touching_open_positions(self):
        self.e.reg.dispatch(self.e.sig("sig-5")); self.e.fill_entries()
        tA = self.e.reg.tenants["acctA"]
        pos = tA.open_positions(); self.assertEqual(len(pos), 1)
        qty_before = pos[0]["qty"]
        # customer picks $1000 while a position is open -> DRAINING (entries off, management on)
        self.e.accounts.set(ctx("acctA", 100, state="DRAINING", paused=True, pending=1000)); self.e.reg.refresh()
        self.assertFalse(tA.ledger.get_switches().allow_new_entries)
        out = self.e.reg.dispatch(self.e.sig("sig-6", market="ETHUSDT-PERP", price="2500"))
        self.assertEqual(out["acctA"], "SKIPPED:entries_not_wanted"); self.assertTrue(out["acctB"].startswith("ACKNOWLEDGED"))
        self.e.reg.manage_all()
        self.assertEqual(self.e.accounts.drains, [])                       # not flat -> no transition
        self.assertEqual(tA.open_positions()[0]["qty"], qty_before)        # existing position untouched
        self.assertEqual(tA.eng.cfg.limits.max_notional_per_order, Decimal('Infinity'))
        # the position closes on its own (target fill) -> book flat -> new size applied
        prot = tA.ledger.get_protection(pos[0]["position_id"])
        tr = self.e.mocks["acctA"].fill(prot["target_coid"], qty_before, prot["tp_price"]); tA.eng.on_fill(tr)
        self.assertTrue(tA.is_flat())
        self.e.reg.manage_all()
        self.assertEqual(self.e.accounts.drains, ["acctA"])
        self.assertEqual((tA.ctx.state, tA.ctx.position_size_usd), ("ACTIVE", Decimal(1000)))
        self.assertTrue(tA.ledger.get_switches().allow_new_entries)
        self.assertEqual(tA.eng.cfg.limits.max_notional_per_order, Decimal('Infinity'))
        self.e.feed("ETHUSDT-PERP", "2500")
        out = self.e.reg.dispatch(self.e.sig("sig-7", market="ETHUSDT-PERP", price="2500"))
        self.assertTrue(out["acctA"].startswith("ACKNOWLEDGED"))
        c = make_coid("yellow:acctA", "sig-7", "ENTRY", "ETHUSDT-PERP", "long")
        self.assertAlmostEqual(float(tA.ledger.get_order(c)["qty"]) * 2500, 25, delta=1)

    def test_timeout_close_signal_fans_out(self):
        self.e.reg.dispatch(self.e.sig("sig-8")); self.e.fill_entries()
        out = self.e.reg.dispatch_close(CloseSignal("BTCUSDT-PERP", "long", "TIMEOUT", self.e.clock()))
        self.assertEqual(out, {"acctA": "CLOSE_SUBMITTED", "acctB": "CLOSE_SUBMITTED"})
        out = self.e.reg.dispatch_close(CloseSignal("ETHUSDT-PERP", "short", "TIMEOUT", self.e.clock()))
        self.assertEqual(out, {"acctA": "NO_POSITION", "acctB": "NO_POSITION"})

    def test_restart_reconciles_each_tenant_and_keeps_idempotency(self):
        self.e.reg.dispatch(self.e.sig("sig-9")); self.e.fill_entries()
        self.e.mocks["acctB"].script_place("accept_lost")
        out = self.e.reg.dispatch(self.e.sig("sig-10", market="ETHUSDT-PERP", price="2500"))
        self.assertEqual(out["acctB"], "UNKNOWN:reconciliation_required")
        for t in self.e.reg.tenants.values():
            t.ledger.close()
        # process restart: new registry over the same state dir and the same venue state
        self.e.build(); self.e.reg.refresh()
        tA, tB = self.e.reg.tenants["acctA"], self.e.reg.tenants["acctB"]
        self.assertTrue(tA.started and tB.started)
        self.assertEqual(len(tA.open_positions()), 1); self.assertEqual(len(tB.open_positions()), 1)
        self.assertEqual(tA.open_positions()[0]["state"], "PROTECTED")
        cB = make_coid("yellow:acctB", "sig-10", "ENTRY", "ETHUSDT-PERP", "long")
        self.assertEqual(tB.ledger.get_order(cB)["state"], "ACKNOWLEDGED")      # found on venue by reconciliation
        self.e.feed("BTCUSDT-PERP"); self.e.feed("ETHUSDT-PERP", "2500"); self.e.reg.manage_all()
        out = self.e.reg.dispatch(self.e.sig("sig-9")); self.assertTrue(all(v.startswith("DUPLICATE") for v in out.values()), out)
        out = self.e.reg.dispatch(self.e.sig("sig-10", market="ETHUSDT-PERP", price="2500")); self.assertTrue(out["acctB"].startswith("DUPLICATE"))
        self.assertEqual(self.e.mocks["acctB"].place_calls, 3)   # entry, target, lost-ack entry: nothing re-sent

    def test_disconnected_tenant_is_unloaded_and_others_survive(self):
        del self.e.accounts.ctx["acctA"]
        r = self.e.reg.refresh()
        self.assertEqual(r["removed"], ["acctA"]); self.assertNotIn("acctA", self.e.reg.tenants)
        out = self.e.reg.dispatch(self.e.sig("sig-11")); self.assertEqual(list(out), ["acctB"])
        self.assertEqual(self.e.reg.runtime_info("acctA")["loaded"], False)

    def test_zero_network(self):
        self.assertEqual(NETWORK_ATTEMPTS["count"], 0)
        self.assertNotIn("YELLOW_ARMED", os.environ)
