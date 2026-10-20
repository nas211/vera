import unittest
from dataclasses import replace
from decimal import Decimal as D

from .harness import Env, ACCT
from execution.entry_brake import closed_net, key, COOLDOWN_SECONDS
from execution.metadata import MarketFilters
from execution.types import Capabilities, OrderState as O


class EntryBrakeTests(unittest.TestCase):
    def make(self):
        e = Env(capabilities=Capabilities(venue_stop_orders=True, conditional_take_profit=True))
        self.addCleanup(lambda: e.ledger.close())
        for market in ('A', 'B', 'C', 'D'):
            e.meta._f[market] = MarketFilters(market, D('.01'), D('.01'), D('.01'), D(5), D(20), 'TRADING')
        e.ready()
        return e

    def signal(self, e, market='A', side='long', account=ACCT):
        e.feed(market, '100')
        return replace(e.signal(str(e.clock()) + market, market=market, direction=side,
                                strategy='WY-PULLBACK', notional='100', price='100'), account_id=account)

    def opened(self, e, market='A', side='long', account=ACCT):
        r = e.eng.submit_entry(self.signal(e, market, side, account))
        self.assertEqual(r.status, 'ACKNOWLEDGED', r)
        o = e.order(r.client_order_id)
        e.eng.on_fill(e.mock.fill(r.client_order_id, o['qty'], '100', fee='.04'))
        return r.client_order_id

    def close(self, e, pid, price, partial=False):
        prot = e.ledger.get_protection(pid)
        coid = prot['stop_coid']
        qty = D(e.pos(pid)['qty'])
        if partial:
            e.eng.on_fill(e.mock.fill(coid, qty / 2, price, fee='.02'))
            qty /= 2
        e.clock.advance(1)
        e.eng.on_fill(e.mock.fill(coid, qty, price, fee='.02' if partial else '.04'))
        self.assertEqual(e.pos(pid)['state'], 'CLOSED')

    def test_two_losses_block_long_and_short_then_automatically_resume(self):
        for side, price in [('long', '99'), ('short', '101')]:
            with self.subTest(side=side):
                e = self.make()
                self.close(e, self.opened(e, side=side), price)
                self.close(e, self.opened(e, market='B', side=side), price)
                calls = e.mock.place_calls
                result = e.eng.submit_entry(self.signal(e, 'C', side))
                self.assertEqual(result.reason, 'entry_risk:consecutive_losses_cooldown')
                self.assertEqual(e.mock.place_calls, calls)
                e.clock.advance(COOLDOWN_SECONDS - 1)
                self.assertEqual(e.eng.submit_entry(self.signal(e, 'C', side)).status, 'BLOCKED')
                e.clock.advance(1)
                self.assertEqual(e.eng.submit_entry(self.signal(e, 'C', side)).status, 'ACKNOWLEDGED')

    def test_restart_and_switch_refresh_do_not_clear_cooldown(self):
        e = self.make()
        for market in ('A', 'B'):
            self.close(e, self.opened(e, market), '99')
        before = e.eng.entry_risk_status(ACCT)
        e.clock.advance(300)
        self.assertTrue(e.restart().ok)
        e.eng.set_switches(allow_new_entries=True, manage_existing_positions=True)
        after = e.eng.entry_risk_status(ACCT)
        self.assertEqual(after['until'], before['until'])
        self.assertEqual(after['seconds_remaining'], 1500)
        self.assertEqual(e.eng.submit_entry(self.signal(e, 'C')).status, 'BLOCKED')

    def test_partial_exits_count_one_trade_and_fees_determine_loss(self):
        for side, price in [('long', '100.05'), ('short', '99.95')]:
            with self.subTest(side=side):
                e = self.make(); pid = self.opened(e, side=side)
                self.close(e, pid, price, partial=True)
                self.assertEqual(closed_net(e.eng, e.pos(pid)), D('-.03'))
                self.assertEqual(e.eng.entry_risk_status(ACCT)['consecutive_losses'], 1)
                self.close(e, self.opened(e, 'B', side), price, partial=True)
                self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])

    def test_single_large_loss_does_not_replace_requested_two_loss_rule(self):
        e = self.make(); self.close(e, self.opened(e), '97')
        risk = e.eng.entry_risk_status(ACCT)
        self.assertFalse(risk['blocked']); self.assertEqual(risk['consecutive_losses'], 1)

    def test_win_resets_streak(self):
        e = self.make()
        for price in ('99', '102', '99'):
            self.close(e, self.opened(e), price)
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.assertEqual(e.eng.entry_risk_status(ACCT)['consecutive_losses'], 1)

    def test_other_account_losses_do_not_count_toward_this_account(self):
        e = self.make(); other = 'yellow:future-account'
        self.close(e, self.opened(e), '99')
        self.close(e, self.opened(e, 'B', account=other), '99')
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.close(e, self.opened(e, 'C'), '99')
        self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])
        self.assertEqual(e.eng.submit_entry(self.signal(e, 'D', account=other)).status, 'ACKNOWLEDGED')

    def test_cooldown_does_not_disable_management_and_win_cannot_end_it_early(self):
        e = self.make()
        a = self.opened(e, 'A'); b = self.opened(e, 'B'); c = self.opened(e, 'C')
        self.close(e, a, '99'); self.close(e, b, '99')
        until = e.eng.entry_risk_status(ACCT)['until']
        self.assertTrue(e.eng.manage_existing()['managed'])
        self.assertTrue(e.ledger.get_switches().manage_existing_positions)
        self.close(e, c, '102')
        self.assertEqual(e.eng.entry_risk_status(ACCT)['until'], until)
        self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])

    def test_incomplete_history_blocks_without_claiming_zero_loss(self):
        e = self.make(); pid = self.opened(e)
        self.close(e, pid, '99')
        with e.ledger._tx():
            e.ledger.db.execute("update orders set filled_qty='0' where position_id=? and purpose='STOP'", (pid,))
        self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'entry_risk_history_unconfirmed')

    def test_reconciliation_discovers_second_loss_before_next_entry(self):
        e = self.make(); self.close(e, self.opened(e), '99')
        pid = self.opened(e, 'B'); coid = e.ledger.get_protection(pid)['stop_coid']
        e.clock.advance(1)
        e.mock.fill(coid, e.pos(pid)['qty'], '99', fee='.04')
        self.assertTrue(e.eng.reconcile().ok)
        self.assertEqual(e.eng.submit_entry(self.signal(e, 'C')).reason,
                         'entry_risk:consecutive_losses_cooldown')

    def test_next_cycle_requires_two_new_losses_after_wait(self):
        e = self.make()
        for market in ('A', 'B'):
            self.close(e, self.opened(e, market), '99')
        until = e.eng.entry_risk_status(ACCT)['until']
        e.clock.advance(COOLDOWN_SECONDS)
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.close(e, self.opened(e, 'C'), '99')
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.close(e, self.opened(e, 'D'), '99')
        risk = e.eng.entry_risk_status(ACCT)
        self.assertTrue(risk['blocked']); self.assertGreater(risk['until'], until)

    def test_losses_during_wait_do_not_create_a_permanent_halt(self):
        e = self.make()
        ids = [self.opened(e, m) for m in ('A', 'B', 'C', 'D')]
        self.close(e, ids[0], '99'); self.close(e, ids[1], '99')
        until = e.eng.entry_risk_status(ACCT)['until']
        self.close(e, ids[2], '99'); self.close(e, ids[3], '99')
        e.clock.advance(until - e.clock())
        self.assertFalse(e.eng.entry_risk_status(ACCT)['blocked'])
        self.assertEqual(e.eng.entry_risk_status(ACCT)['consecutive_losses'], 0)

    def test_expiry_does_not_override_user_pause(self):
        e = self.make()
        for market in ('A', 'B'):
            self.close(e, self.opened(e, market), '99')
        self.assertTrue(e.eng.entry_risk_status(ACCT)['blocked'])
        e.eng.set_switches(allow_new_entries=False, manage_existing_positions=True)
        e.clock.advance(COOLDOWN_SECONDS)
        self.assertEqual(e.eng.submit_entry(self.signal(e, 'C')).reason, 'entries_disabled')

    def test_matched_exchange_history_includes_funding(self):
        import json
        from pathlib import Path
        e = self.make(); pid = self.opened(e); self.close(e, pid, '100.1')
        history = {pid: dict(id='venue-close', market='A', direction='long', entry_price='100',
                            max_held='1', realized_pnl='.1', total_trading_fee='.08',
                            net_funding_fee='-.03', closed_at='2026-10-17T20:00:00Z')}
        Path(e.ledger.path + '.history.json').write_text(json.dumps(history))
        self.assertEqual(e.eng.entry_risk_status(ACCT)['consecutive_losses'], 1)
        history[pid]['market'] = 'FOREIGN'
        Path(e.ledger.path + '.history.json').write_text(json.dumps(history))
        self.assertEqual(e.eng.entry_risk_status(ACCT)['reason'], 'entry_risk_history_unconfirmed')

    def test_tenant_context_refresh_preserves_cooldown_and_reports_it(self):
        from .test_tenant_fanout import Env as TenantEnv, ctx
        e = TenantEnv(); self.addCleanup(e.cleanup)
        e.accounts.set(ctx('acctA', 100)); e.ready()
        rt = e.reg.tenants['acctA']; until = e.clock() + COOLDOWN_SECONDS
        rt.ledger.kv_set(key(rt.ctx.namespace), dict(until=until, trigger_position_id='closed-2'))
        e.reg.refresh()
        view = rt.runtime_info()
        self.assertFalse(view['entries_allowed'])
        self.assertEqual(view['entry_risk']['until'], until)
        self.assertTrue(rt.ledger.get_switches().manage_existing_positions)
        self.assertEqual(e.reg.status()['by_state']['acctA']['entry_risk']['until'], until)

    def test_resting_entry_is_cancelled_but_live_position_keeps_protection(self):
        e = self.make()
        a = self.opened(e, 'A'); b = self.opened(e, 'B'); c = self.opened(e, 'C')
        waiting = e.eng.submit_entry(self.signal(e, 'D'))
        self.close(e, a, '99'); self.close(e, b, '99')
        self.assertTrue(e.eng.manage_existing()['managed'])
        self.assertEqual(e.order(waiting.client_order_id)['state'], 'CANCELLED')
        prot = e.ledger.get_protection(c)
        self.assertEqual(e.order(prot['stop_coid'])['state'], 'ACKNOWLEDGED')
        self.assertEqual(e.pos(c)['state'], 'PROTECTED')

    def test_last_moment_cooldown_blocks_validated_entry_before_send(self):
        from unittest.mock import patch
        e = self.make()
        original = e.eng._submit
        def cooldown_then_submit(coid, req, filters):
            e.ledger.kv_set(key(req.account_id), dict(until=e.clock()+COOLDOWN_SECONDS,
                                                    trigger_position_id='other-close'))
            return original(coid, req, filters)
        with patch.object(e.eng, '_submit', side_effect=cooldown_then_submit):
            result = e.eng.submit_entry(self.signal(e))
        self.assertEqual(result.reason, 'entry_risk:consecutive_losses_cooldown')
        self.assertEqual(e.mock.place_calls, 0)
        self.assertEqual(e.order(result.client_order_id)['state'], 'REJECTED')
        # A request stopped before submission must not reserve capacity forever.
        e.config.limits = replace(e.config.limits, max_open_positions=1)
        e.clock.advance(COOLDOWN_SECONDS)
        self.assertEqual(e.eng.submit_entry(self.signal(e, 'B')).status, 'ACKNOWLEDGED')
