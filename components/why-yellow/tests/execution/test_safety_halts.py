import json
import unittest
from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

from . import test_entry_brake as brake_tests
from . import test_tenant_fanout as tenant_tests
from .harness import ACCT, Env
from execution.entry_brake import day_bounds, protection_key, key
from execution.types import Capabilities


class SafetyHalts(unittest.TestCase):
    make = brake_tests.EntryBrakeTests.make
    signal = brake_tests.EntryBrakeTests.signal
    opened = brake_tests.EntryBrakeTests.opened
    close = brake_tests.EntryBrakeTests.close

    def legacy_upgrade(self, e):
        # Older deployed code never wrote either key. Recreate that state.
        with e.ledger._tx():
            e.ledger.db.execute('DELETE FROM kv WHERE k IN (?, ?)',
                (protection_key(ACCT), 'entry_protection_history_v1:' + ACCT))

    def test_upgrade_latches_recorded_failure_after_position_closed(self):
        for side in ('long', 'short'):
            with self.subTest(side=side):
                e = self.make(); pid = self.opened(e, side=side)
                self.close(e, pid, '101' if side == 'long' else '99')
                e.ledger.add_flag('P0', 'POSITION_WITHOUT_PROTECTION',
                                  pid + ': venue_protection_failed', ACCT, 'A')
                self.legacy_upgrade(e)
                e.clock.advance(90000)
                self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'protection_failure_halt')
                self.assertTrue(e.restart().ok)
                self.assertEqual(e.eng.submit_entry(self.signal(e, 'B')).reason,
                                 'entry_risk:protection_failure_halt')
                self.assertTrue(e.eng.manage_existing()['managed'])

    def test_upgrade_recovers_protective_close_without_flag(self):
        e = self.make(); pid = self.opened(e)
        e.eng.close_position(pid, 'PROTECTION_FAILED')
        close = next(o for o in e.ledger.orders_for_position(pid) if o['purpose'] == 'CLOSE')
        e.eng.on_fill(e.mock.fill(close['client_order_id'], close['qty'], '101', fee='.04'))
        self.legacy_upgrade(e)
        self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'protection_failure_halt')

    def test_upgrade_does_not_halt_other_accounts_or_on_transient_readback(self):
        e = self.make()
        e.ledger.add_flag('P0', 'POSITION_WITHOUT_PROTECTION', 'foreign failure', 'other', 'A')
        e.ledger.add_flag('P0', 'PROTECTION_NOT_CONFIRMED', 'pending readback', ACCT, 'A')
        self.legacy_upgrade(e)
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.assertEqual(e.eng.entry_risk_status('other')['reason'], 'protection_failure_halt')

    def test_history_scan_failure_blocks_entries_and_retries(self):
        from unittest.mock import patch
        e = self.make(); self.legacy_upgrade(e)
        with patch.object(e.ledger, 'flags', side_effect=RuntimeError('read failed')):
            self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'entry_risk_history_unconfirmed')
        self.assertIsNone(e.ledger.kv_get('entry_protection_history_v1:' + ACCT))
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])

    def daily(self):
        e = self.make()
        e.config.loss_halt_timezone = 'Europe/Berlin'
        e.clock.now = datetime(2026, 10, 19, 10, tzinfo=ZoneInfo('Europe/Berlin')).timestamp()
        return e

    def test_day_halt_both_directions_restart_and_management(self):
        for side, price in [('long', '99'), ('short', '101')]:
            with self.subTest(side=side):
                e = self.daily()
                a = self.opened(e, 'A', side); b = self.opened(e, 'B', side)
                c = self.opened(e, 'C', side)
                self.close(e, a, price, partial=True); self.close(e, b, price)
                r = e.eng.entry_risk_status(ACCT)
                self.assertEqual(r['reason'], 'consecutive_losses_day_halt')
                self.assertEqual(r['until'], day_bounds(e.clock(), 'Europe/Berlin')[1])
                e.clock.advance(1801)
                self.assertEqual(e.eng.submit_entry(self.signal(e, 'D', side)).status, 'BLOCKED')
                self.assertTrue(e.restart().ok)
                self.assertTrue(e.eng.manage_existing()['managed'])
                self.assertEqual(e.pos(c)['state'], 'PROTECTED')
                self.close(e, c, '102' if side == 'long' else '98')
                self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])
                e.clock.now = r['until']
                self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])

    def test_upgrade_reconstructs_today_despite_expired_legacy_timer(self):
        e = self.daily(); e.config.loss_halt_timezone = None
        for m in ('A', 'B'):
            self.close(e, self.opened(e, m), '99')
        e.eng.entry_risk_status(ACCT)
        e.clock.advance(1801)
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        e.config.loss_halt_timezone = 'Europe/Berlin'
        self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])

    def test_dst_calendar_days_and_near_midnight_minimum_pause(self):
        for day, hours in [(datetime(2026, 3, 29), 23), (datetime(2026, 10, 25), 25)]:
            start, end = day_bounds(day.replace(tzinfo=ZoneInfo('Europe/Berlin')).timestamp(), 'Europe/Berlin')
            self.assertEqual(end-start, hours*3600)
        e = self.daily()
        e.clock.now = day_bounds(e.clock(), 'Europe/Berlin')[1]-60
        for m in ('A', 'B'):
            self.close(e, self.opened(e, m), '99')
        until = e.eng.entry_risk_status(ACCT)['until']
        self.assertEqual(until, e.clock()+1800)
        e.clock.advance(61)
        self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])

    def test_yesterday_loss_does_not_combine_with_today(self):
        e = self.daily()
        self.close(e, self.opened(e), '99')
        e.clock.now = day_bounds(e.clock(), 'Europe/Berlin')[1]+1
        self.close(e, self.opened(e, 'B'), '99')
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.assertEqual(e.eng.entry_risk_status(ACCT)['consecutive_losses'], 1)

    def test_protection_failure_latches_across_flatten_restart_and_day_change(self):
        for side in ('long', 'short'):
            with self.subTest(side=side):
                e = self.make(); pid = self.opened(e, side=side)
                self.assertEqual(e.eng.close_position(pid, 'PROTECTION_FAILED'), 'CLOSE_SUBMITTED')
                close = next(o for o in e.ledger.orders_for_position(pid) if o['purpose']=='CLOSE')
                e.eng.on_fill(e.mock.fill(close['client_order_id'], close['qty'], '100', fee='.04'))
                self.assertEqual(e.pos(pid)['state'], 'CLOSED')
                e.clock.advance(90000); self.assertTrue(e.restart().ok)
                e.eng.set_switches(allow_new_entries=True, manage_existing_positions=True)
                self.assertEqual(e.eng.submit_entry(self.signal(e, 'B')).reason,
                                 'entry_risk:protection_failure_halt')
                self.assertTrue(e.eng.manage_existing()['managed'])
                self.assertFalse(e.eng.entry_risk_status('yellow:another-account')['blocked'])

    def test_failed_protection_flag_policy_also_blocks_entries(self):
        e = self.make(); pid = self.opened(e)
        e.config.protection_failure_policy = 'flag'
        e.eng._protection_failed(pid, 'readback_failed')
        self.assertIsNotNone(e.ledger.kv_get(protection_key(ACCT)))
        self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'protection_failure_halt')

    def test_paper_hold_cancels_resting_entries_and_keeps_existing_stops(self):
        e = self.make(); pid = self.opened(e)
        pending = e.eng.submit_entry(self.signal(e, 'B'))
        e.config.entry_hold_reason = 'paper_validation_required'
        self.assertTrue(e.eng.manage_existing()['managed'])
        self.assertEqual(e.order(pending.client_order_id)['state'], 'CANCELLED')
        self.assertEqual(e.pos(pid)['state'], 'PROTECTED')

    def test_jp_live_release_survives_context_refresh_with_risk_limits(self):
        for strategy in ('WY-PULLBACK', 'WY-REVERT'):
            with self.subTest(strategy=strategy):
                e = tenant_tests.Env(); self.addCleanup(e.cleanup)
                jp = '04d10bf8-6614-4c11-9ab9-240e26fce66c'
                e.accounts.set(tenant_tests.ctx(jp, 500))
                e.accounts.set(tenant_tests.ctx('other', 100))
                e.ready(); t = e.reg.tenants[jp]
                t.apply_context(t.ctx)
                self.assertEqual(t.eng.cfg.loss_halt_timezone, 'Europe/Berlin')
                self.assertEqual(t.eng.cfg.limits.max_open_positions, 1)
                self.assertTrue(t.runtime_info()['entries_allowed'])
                self.assertTrue(t.ledger.get_switches().manage_existing_positions)
                self.assertFalse(t.eng.entry_risk_status(t.ctx.namespace)['blocked'])
                self.assertEqual(t.submit(e.sig(strategy=strategy)).status, 'ACKNOWLEDGED')
                self.assertIsNone(e.reg.tenants['other'].eng.cfg.entry_hold_reason)

    def test_reviewed_history_release_survives_restart_and_new_failure_relatches(self):
        for side in ('long', 'short'):
            with self.subTest(side=side):
                e = self.make(); pid = self.opened(e, side=side)
                self.close(e, pid, '101' if side == 'long' else '99')
                e.ledger.add_flag('P0', 'POSITION_WITHOUT_PROTECTION',
                                  pid + ': historical incident', ACCT, 'A')
                self.legacy_upgrade(e)
                self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'protection_failure_halt')
                with e.ledger._tx():
                    e.ledger.db.execute('DELETE FROM kv WHERE k=?', (protection_key(ACCT),))
                self.assertTrue(e.ledger.kv_get('entry_protection_history_v1:' + ACCT))
                self.assertTrue(e.restart().ok)
                self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
                fresh = self.opened(e, market='B', side=side)
                e.eng._protection_failed(fresh, 'new_stop_failure')
                self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'protection_failure_halt')
