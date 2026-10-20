import unittest
from .harness import Env, FakeClock
from execution.market_data import (FreshnessMonitor, BAR_ACCEPTED, BAR_DUPLICATE, BAR_FUTURE, BAR_GAP, BAR_OUT_OF_ORDER)


class TestFreshness(unittest.TestCase):
    def setUp(self):
        self.c = FakeClock(1_790_100_000.0)
        self.f = FreshnessMonitor(300_000, 30.0, clock=self.c)
        self.base = int(self.c() * 1000) // 300_000 * 300_000
        self.f.seed("BTC", [self.base - i * 300_000 for i in range(200, 0, -1)])
        self.f.on_bar_close("BTC", self.base)
        self.f.on_mark("BTC", 100.0)

    def test_fresh_allows(self):
        self.assertEqual(self.f.entry_allowed("BTC")[0], True)
        s = self.f.snapshot("BTC")
        for k in ("data_ts", "now", "age", "bar_completed", "continuity_ok"):
            self.assertIn(k, s)

    def test_11_stale_mark_blocks_entry(self):
        self.c.advance(31)
        ok, why = self.f.entry_allowed("BTC")
        self.assertFalse(ok); self.assertTrue(why.startswith("mark_stale"))
        e = Env(); e.ready()
        e.clock.advance(31)
        r = e.eng.submit_entry(e.signal("sig-1"))
        self.assertEqual(r.status, "BLOCKED"); self.assertTrue(r.reason.startswith("market_data:mark_stale"))
        self.assertEqual(e.mock.place_calls, 0)

    def test_stale_bar_blocks_entry(self):
        self.c.advance(700)
        self.f.on_mark("BTC", 100.0)
        ok, why = self.f.entry_allowed("BTC")
        self.assertFalse(ok); self.assertTrue(why.startswith("bar_stale"))

    def test_missing_bar_gap_breaks_continuity(self):
        self.c.advance(700); self.f.on_mark("BTC", 100.0)
        self.assertEqual(self.f.on_bar_close("BTC", self.base + 2 * 300_000), BAR_GAP)   # skipped one bar
        self.assertEqual(self.f.entry_allowed("BTC"), (False, "continuity_broken"))
        # re-seed from venue history restores continuity
        self.f.seed("BTC", [self.base + i * 300_000 for i in range(-200, 3)])
        self.assertTrue(self.f.entry_allowed("BTC")[0])

    def test_duplicate_and_out_of_order_bars(self):
        self.assertEqual(self.f.on_bar_close("BTC", self.base), BAR_DUPLICATE)
        self.assertEqual(self.f.on_bar_close("BTC", self.base - 300_000), BAR_OUT_OF_ORDER)
        self.assertTrue(self.f.entry_allowed("BTC")[0])                       # neither corrupts continuity
        self.assertEqual(self.f.markets["BTC"].last_bar_open_ms, self.base)

    def test_reconnect_gap(self):
        self.f.on_disconnect()
        self.assertEqual(self.f.entry_allowed("BTC"), (False, "continuity_broken"))
        self.c.advance(4 * 86400)
        self.assertEqual(self.f.on_bar_close("BTC", self.base + 4 * 86400 * 1000), BAR_GAP)
        self.f.on_mark("BTC", 100.0)
        self.assertEqual(self.f.entry_allowed("BTC")[0], False)

    def test_future_timestamp_rejected(self):
        self.assertEqual(self.f.on_mark("BTC", 5.0, self.c() + 60), BAR_FUTURE)
        self.assertEqual(self.f.markets["BTC"].last_mark, 100.0)
        self.assertEqual(self.f.on_bar_close("BTC", self.base + 3600 * 1000 * 24), BAR_FUTURE)
        self.assertEqual(self.f.markets["BTC"].last_bar_open_ms, self.base)

    def test_clock_discrepancy(self):
        ok, why = self.f.entry_allowed("BTC", now=self.c() - 60)
        self.assertEqual((ok, why), (False, "clock_discrepancy"))
        self.assertFalse(self.f.mark_fresh("BTC", now=self.c() - 60))

    def test_out_of_order_mark_ignored(self):
        self.assertEqual(self.f.on_mark("BTC", 1.0, self.c() - 10), BAR_OUT_OF_ORDER)
        self.assertEqual(self.f.markets["BTC"].last_mark, 100.0)

    def test_paper_engine_untouched_by_gate(self):
        # the gate lives in execution; yellow_paper.py equals the approved version (audited baseline
        # + owner-approved pause gate only, see test_strategy_integrity)
        import hashlib
        from .test_strategy_integrity import APPROVED, AUDITED_BASELINE, paper_without_pause_gate
        h = hashlib.sha256(open("/opt/why-yellow/yellow_paper.py", "rb").read()).hexdigest()
        self.assertEqual(h, APPROVED["yellow_paper.py"])
        self.assertEqual(hashlib.sha256(paper_without_pause_gate()).hexdigest(), AUDITED_BASELINE["yellow_paper.py"])
