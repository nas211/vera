import unittest
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from customer.live_entry_filter import LiveEntryFilter, INTERVALS
from customer.tenants import CanonicalSignal, CloseSignal
from customer.router import ExecutionRouter
from customer.feed import YellowMarketFeed
from execution.market_data import FreshnessMonitor
from tests.execution.harness import FakeClock
from tests.execution.test_tenant_fanout import Env, ctx


def history(now, interval, direction="long"):
    step = INTERVALS[interval]
    end = int(now * 1000) // step * step
    rows = []
    for i in range(250):
        price = 70000 + i * 10 if direction == "long" else 80000 - i * 10
        rows.append([end - (250 - i) * step, price, price + 2, price - 2, price])
    return rows


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(1_791_900_005)
        self.gate = LiveEntryFilter("acctA", self.clock)
        self.fresh = FreshnessMonitor(clock=self.clock)
        self.market = "BTCUSDT-PERP"
        self.seed()

    def seed(self, direction="long"):
        for interval in INTERVALS:
            self.assertTrue(self.gate.seed(self.market, interval, history(self.clock(), interval, direction)))
        opens = [r[0] for r in self.gate.bars[(self.market, "5m")]]
        self.fresh.seed(self.market, opens)
        self.fresh.on_mark(self.market, self.gate.bars[(self.market, "5m")][-1][4])

    def sig(self, direction="long", strategy="WY-PULLBACK"):
        return CanonicalSignal("setup-1", self.market, direction, strategy, Decimal("72490"), self.clock())

    def recover(self, direction="long", success=True):
        prev = self.gate.bars[(self.market, "5m")][-1]
        self.clock.advance(300)
        close = prev[2] + 5 if direction == "long" else prev[3] - 5
        if not success:
            close = prev[4]
        row = [prev[0] + 300_000, prev[4], max(prev[4], close) + 1, min(prev[4], close) - 1, close]
        self.gate.on_bar(self.market, "5m", row)
        # At hour boundaries the corresponding completed hourly bar is required.
        if not self.gate._current(self.market, "1h"):
            old = self.gate.bars[(self.market, "1h")][-1]
            self.gate.on_bar(self.market, "1h", [old[0]+3_600_000, old[4], old[4]+1, old[4]-1, old[4]])
        self.fresh.on_bar_close(self.market, row[0])
        self.fresh.on_mark(self.market, close)

    def test_long_waits_for_new_completed_recovery_and_reprices_once(self):
        sig = self.sig()
        self.assertEqual(self.gate.offer(sig), "waiting_for_recovery_close")
        self.assertEqual(self.gate.ready(self.fresh), [])
        self.recover()
        ready = self.gate.ready(self.fresh)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].signal_id, sig.signal_id)
        self.assertNotEqual(ready[0].ref_price, sig.ref_price)
        self.assertGreater(ready[0].ts, sig.ts)
        self.assertIn("live_entry_filter", ready[0].detail)
        self.assertEqual(self.gate.ready(self.fresh), [])

    def test_short_is_symmetric(self):
        self.seed("short")
        self.gate.offer(self.sig("short"))
        self.recover("short")
        self.assertEqual(len(self.gate.ready(self.fresh)), 1)

    def test_revert_disabled(self):
        self.assertEqual(self.gate.offer(self.sig(strategy="WY-REVERT")), "revert_entries_disabled")
        self.assertFalse(self.gate.pending)

    def test_unrelated_strategy_passes_through(self):
        self.assertIsNone(self.gate.offer(self.sig(strategy="AI-OTHER")))

    def test_hourly_opposition_blocks_setup(self):
        self.gate.seed(self.market, "1h", history(self.clock(), "1h", "short"))
        self.assertEqual(self.gate.offer(self.sig()), "trend_unconfirmed_or_data_unready")

    def test_hourly_flat_blocks_both_directions(self):
        rows = history(self.clock(), "1h")
        rows = [[r[0], 100, 101, 99, 100] for r in rows]
        self.gate.seed(self.market, "1h", rows)
        self.assertEqual(self.gate.offer(self.sig()), "trend_unconfirmed_or_data_unready")

    def test_stale_future_signals_blocked(self):
        for age in (-1, 121):
            self.assertEqual(self.gate.offer(replace(self.sig(), ts=self.clock()-age)), "stale_or_future_setup")

    def test_no_recovery_no_entry(self):
        self.gate.offer(self.sig())
        self.recover(success=False)
        self.assertEqual(self.gate.ready(self.fresh), [])

    def test_forming_bar_cannot_confirm(self):
        self.gate.offer(self.sig())
        p = self.gate.bars[(self.market, "5m")][-1]
        self.gate.on_bar(self.market, "5m", [p[0]+300_000, p[4], p[4]+20, p[4]-1, p[4]+10])
        self.assertEqual(self.gate.ready(self.fresh), [])

    def test_stale_mark_blocks_recovery(self):
        self.gate.offer(self.sig()); self.recover()
        self.clock.advance(31)
        self.assertEqual(self.gate.ready(self.fresh), [])

    def test_reversal_after_confirmation_blocks_entry(self):
        self.gate.offer(self.sig()); self.recover()
        self.fresh.on_mark(self.market, 70000)
        self.assertEqual(self.gate.ready(self.fresh), [])

    def test_expiry_disconnect_and_restart_discard_setups(self):
        self.gate.offer(self.sig()); self.clock.advance(901)
        self.assertEqual(self.gate.ready(self.fresh), [])
        self.assertFalse(self.gate.pending)
        self.seed(); self.gate.offer(self.sig()); self.gate.disconnect()
        self.assertFalse(self.gate.pending); self.assertFalse(self.gate.bars)
        self.assertFalse(LiveEntryFilter("acctA", self.clock).pending)

    def test_gap_invalidates_history_and_setup(self):
        self.gate.offer(self.sig())
        p = self.gate.bars[(self.market, "5m")][-1]
        self.clock.advance(600)
        self.gate.on_bar(self.market, "5m", [p[0]+600_000, 72500, 72501, 72499, 72500])
        self.assertFalse(self.gate.pending)
        self.assertFalse(self.gate._current(self.market, "5m"))

    def test_seed_sorts_excludes_forming_and_rejects_recent_gap(self):
        rows = history(self.clock(), "1h")
        future = [rows[-1][0]+3_600_000, 80000, 80001, 79999, 80000]
        self.assertTrue(self.gate.seed(self.market, "1h", list(reversed(rows+[future]))))
        self.assertEqual(self.gate.bars[(self.market, "1h")][-1], tuple(rows[-1]))
        self.assertFalse(self.gate.seed(self.market, "1h", rows[:-3]+rows[-2:]))

    def test_malformed_candle_blocks(self):
        for price in (float("nan"), -1, float("inf")):
            rows = history(self.clock(), "1h")
            rows[-1][4] = price
            self.assertFalse(self.gate.seed(self.market, "1h", rows))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.e.accounts.set(ctx("acctA", 100)); self.e.accounts.set(ctx("acctB", 100))
        self.e.ready()
        self.gate = LiveEntryFilter("acctA", self.e.clock)
        self.e.reg.entry_filter = self.gate

    def tearDown(self):
        self.e.cleanup()

    def test_revert_block_is_account_scoped(self):
        out = self.e.reg.dispatch(self.e.sig())
        self.assertEqual(out['acctA'], 'FILTERED:revert_entries_disabled')
        self.assertTrue(out['acctB'].startswith('ACKNOWLEDGED'))
        self.assertEqual(self.e.mocks['acctA'].place_calls, 0)

    def test_confirmed_setup_uses_normal_engine_and_submits_only_once(self):
        market = 'BTCUSDT-PERP'
        for interval in INTERVALS:
            self.gate.seed(market, interval, history(self.e.clock(), interval))
        out = self.e.reg.dispatch(self.e.sig(strategy='WY-PULLBACK'))
        self.assertEqual(out['acctA'], 'FILTERED:waiting_for_recovery_close')
        self.assertEqual(self.e.mocks['acctA'].place_calls, 0)
        previous = self.gate.bars[(market,'5m')][-1]
        self.e.clock.advance(300)
        row = [previous[0]+300_000, previous[4], previous[4]+11, previous[4]-1, previous[4]+10]
        self.gate.on_bar(market, '5m', row)
        if not self.gate._current(market,'1h'):
            old = self.gate.bars[(market,'1h')][-1]
            self.gate.on_bar(market,'1h',[old[0]+3_600_000,old[4],old[4]+1,old[4]-1,old[4]])
        self.e.feed(market,str(row[4]))
        self.assertEqual(self.e.reg.dispatch_confirmed(), {market:'ACKNOWLEDGED'})
        self.assertEqual(self.e.reg.dispatch_confirmed(), {})
        self.assertEqual(self.e.mocks['acctA'].place_calls,1)
        orders = self.e.reg.tenants['acctA'].ledger.orders_where(
            [__import__('execution.types',fromlist=['OrderState']).OrderState.ACKNOWLEDGED],purpose='ENTRY')
        self.assertEqual(len(orders),1)
        self.assertIn('live_entry_filter',orders[0]['meta'])

    def test_existing_revert_protection_and_timeout_still_work(self):
        self.e.reg.entry_filter = None
        self.e.reg.dispatch(self.e.sig()); self.e.fill_entries()
        self.e.reg.entry_filter = self.gate
        self.e.reg.manage_all()
        pos = self.e.reg.tenants['acctA'].open_positions()[0]
        self.assertEqual(pos['state'], 'PROTECTED')
        out = self.e.reg.dispatch_close(CloseSignal('BTCUSDT-PERP','long','TIMEOUT',self.e.clock()))
        self.assertEqual(out['acctA'],'CLOSE_SUBMITTED')

    def test_paused_account_cancels_queued_entry(self):
        self.gate.pending['BTCUSDT-PERP'] = self.e.sig(strategy='WY-PULLBACK')
        self.e.accounts.set(ctx('acctA', 100, paused=True));self.e.reg.refresh()
        self.assertEqual(self.e.reg.dispatch_confirmed(), {})
        self.assertFalse(self.gate.pending)

    def test_source_target_cancels_pending_without_live_close(self):
        self.gate.pending['BTCUSDT-PERP'] = self.e.sig(strategy='WY-PULLBACK')
        result = ExecutionRouter(self.e.reg).dispatch_close(CloseSignal('BTCUSDT-PERP','long','TARGET',self.e.clock()))
        self.assertEqual(result['yellow'],'VENUE_MANAGED')
        self.assertFalse(self.gate.pending)

    def test_non_yellow_context_is_not_filtered(self):
        self.assertFalse(self.gate.applies(replace(self.e.accounts.ctx['acctA'], venue='weex')))

    def test_feed_completes_bars_only_on_rollover_and_ignores_old(self):
        seen = []
        sink = SimpleNamespace(on_candle=lambda *args:seen.append(args))
        feed = YellowMarketFeed(['BTC'], FreshnessMonitor(clock=self.e.clock),sink,clock=self.e.clock)
        def push(interval, ts):
            feed._handle({'push': {'channel':f'public.kline.BTC.{interval}', 'pub':{'data':{'kline':[ts,100,101,99,100]}}}})
        push('1h',0);push('1h',0)
        self.assertEqual(seen,[])
        push('1h',3_600_000);push('1h',0);push('1h',7_200_000)
        self.assertEqual([x[2][0] for x in seen],[0,3_600_000])
        self.assertIsNone(feed.curbar['BTC'])
        push('5m',0);push('5m',300_000)
        self.assertEqual(feed.fresh.markets['BTC'].last_bar_open_ms,0)


if __name__ == '__main__':
    unittest.main()
