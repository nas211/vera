import json
import os
import unittest
from dataclasses import replace
from decimal import Decimal as D

from .harness import Env, ACCT
from .test_tenant_fanout import Env as TenantEnv, ctx
from execution.engine import EngineConfig
from execution.geometry import protection_prices
from execution.metadata import MarketFilters
from execution.sizing import loss_budget_notional, SizingError, entry_loss_limit, HARD_LOSS_LIMIT_ACCOUNT
from execution.types import Capabilities, OrderState as O


SOL = MarketFilters('SOLUSDT-PERP', D('.01'), D('.01'), D('.01'), D(5), D(20), 'TRADING')
XRP = MarketFilters('XRPUSDT-PERP', D('.0001'), D('.001'), D('.001'), D(5), D(20), 'TRADING')


class LossBudgetMathTests(unittest.TestCase):
    def capped(self, f=SOL, px='115.86', side='long', requested='611.74', budget='2', fee='.0004'):
        return loss_budget_notional(D(requested), D(px), D(2), D('.8'), side, f, D(budget), D(fee))

    def test_incident_sizes_are_reduced_to_two_dollars_including_reserves(self):
        for f, px, side, previous_qty in [(SOL, '115.86', 'long', D('5.28')),
                                          (XRP, '1.3563', 'short', D('362.979'))]:
            with self.subTest(market=f.symbol):
                notional, detail = self.capped(f, px, side)
                qty = notional / D(px)
                self.assertLess(qty, previous_qty)
                self.assertLessEqual(D(detail['planned_loss_with_reserve_usd']), D(2))
                self.assertGreater(D(detail['fee_reserve_usd']), 0)
                self.assertGreater(D(detail['exit_slippage_reserve_usd']), 0)
                self.assertTrue(f.on_step(qty))

    def test_stop_fills_across_entry_cushion_with_fees_and_slippage(self):
        for f, px in [(SOL, D('115.86')), (XRP, D('1.3563'))]:
            for side in ('long', 'short'):
                notional, _ = self.capped(f, str(px), side)
                qty = notional / px
                for factor in (D('.991'), D(1), D('1.005'), D('1.01')):
                    entry = px * factor
                    _, stop = protection_prices('WY-PULLBACK', side, entry)
                    stop = f.round_price(stop, up=side == 'short')
                    slip = max(entry, stop) * D('.01')
                    exit_price = stop - slip if side == 'long' else stop + slip
                    loss = qty * abs(exit_price-entry) + qty * (entry+exit_price) * D('.0004')
                    self.assertLessEqual(loss, D(2), (f.symbol, side, factor, loss))

    def test_small_requested_trade_is_never_increased(self):
        notional, _ = self.capped(requested='20')
        self.assertLessEqual(notional, D(20))

    def test_fee_floor_and_higher_fee(self):
        low, _ = self.capped(fee='0')
        normal, _ = self.capped()
        high, _ = self.capped(fee='.003')
        self.assertEqual(low, normal)
        self.assertLess(high, normal)

    def test_invalid_budgets_rejected(self):
        for budget in ('0', '-2', 'NaN', 'Infinity'):
            with self.subTest(budget=budget), self.assertRaises(SizingError):
                self.capped(budget=budget)

    def test_minimum_lot_never_rounds_risk_up(self):
        f = replace(SOL, step=D(1), min_qty=D(1))
        with self.assertRaisesRegex(SizingError, 'loss_budget_below_coin_minimum'):
            self.capped(f)

    def test_contract_multiplier_is_included(self):
        f = replace(SOL, contract_multiplier=D(10), step=D('.001'), min_qty=D('.001'))
        notional, detail = self.capped(f)
        self.assertLessEqual(D(detail['planned_loss_with_reserve_usd']), D(2))
        self.assertTrue(f.on_step(notional / D('115.86') / 10))

    def test_short_target_minimum_is_checked(self):
        f = replace(SOL, min_notional=D(65))
        with self.assertRaisesRegex(SizingError, 'loss_budget_below_coin_minimum'):
            loss_budget_notional(D(1000), D('115.86'), D(2), D(90), 'short', f, D(2), D('.0004'))


class LossBudgetEngineTests(unittest.TestCase):
    def make(self, margin=None):
        e = Env(config=EngineConfig(max_planned_loss_usd=D(2), trade_margin_pct=margin),
                capabilities=Capabilities(venue_stop_orders=True, conditional_take_profit=True))
        self.addCleanup(e.ledger.close)
        e.meta._f[SOL.symbol] = SOL
        e.ready()
        e.feed(SOL.symbol, '115.86')
        e.mock.set_account(ACCT, '66', '66')
        return e

    def test_fixed_and_percentage_sizing_keep_entries_enabled_and_place_stops(self):
        for margin in (None, 99):
            with self.subTest(margin=margin):
                e = self.make(margin)
                coid, _ = e.open_and_fill(market=SOL.symbol, strategy='WY-PULLBACK', price='115.86',
                                         notional='5' if margin else '611.74')
                order = e.order(coid)
                detail = json.loads(order['meta'])['detail']['loss_budget']
                self.assertEqual(detail['max_planned_loss_usd'], '2')
                self.assertLessEqual(D(detail['planned_loss_with_reserve_usd']), D(2))
                self.assertTrue(e.ledger.get_switches().allow_new_entries)
                prot = e.ledger.get_protection(coid)
                self.assertEqual(prot['state'], 'ACTIVE')
                self.assertEqual(D(prot['stop_price']), D('113.55'))  # inward tick rounding
                self.assertEqual(e.order(prot['stop_coid'])['qty'], order['qty'])
                e.mock.fill(prot['stop_coid'], order['qty'], prot['stop_price'])
                self.assertTrue(e.restart().ok)
                self.assertEqual(e.pos(coid)['state'], 'CLOSED')
                self.assertTrue(e.ledger.get_switches().allow_new_entries)

    def test_bad_budget_makes_no_venue_order_without_pausing_account(self):
        e = self.make()
        e.eng.cfg.max_planned_loss_usd = D('NaN')
        r = e.eng.submit_entry(e.signal(market=SOL.symbol, price='115.86'))
        self.assertEqual(r.reason, 'sizing:invalid_loss_budget_inputs')
        self.assertEqual(e.mock.place_calls, 0)
        self.assertTrue(e.ledger.get_switches().allow_new_entries)


class PermanentAccountLimitTests(unittest.TestCase):
    def test_hardcoded_two_dollars_cannot_be_raised_or_unset(self):
        for configured in (None, D(10), D(1000)):
            self.assertEqual(entry_loss_limit(HARD_LOSS_LIMIT_ACCOUNT, configured), D(2))
        self.assertEqual(entry_loss_limit(HARD_LOSS_LIMIT_ACCOUNT, D(1)), D(1))
        self.assertIsNone(entry_loss_limit('yellow:another-account'))

    def test_actual_account_enforced_without_optional_config_and_after_restart(self):
        e = Env(config=EngineConfig(trade_margin_pct=99))
        self.addCleanup(e.ledger.close)
        e.meta._f[SOL.symbol] = SOL
        e.mock.set_account(HARD_LOSS_LIMIT_ACCOUNT, '1000', '1000')
        e.ready()
        e.feed(SOL.symbol, '115.86')
        for i, configured in enumerate((None, D(1000))):
            e.eng.cfg.max_planned_loss_usd = configured
            e.eng.cfg.leverage = 10 if i == 0 else 20
            sig = replace(e.signal(sid=f'hardcap-{i}', market=SOL.symbol, price='115.86',
                                   strategy='WY-PULLBACK', notional='5'), account_id=HARD_LOSS_LIMIT_ACCOUNT)
            result = e.eng.submit_entry(sig)
            self.assertEqual(result.status, 'ACKNOWLEDGED', result)
            order = e.order(result.client_order_id)
            risk = json.loads(order['meta'])['detail']['loss_budget']
            self.assertEqual(D(risk['max_planned_loss_usd']), D(2))
            self.assertLessEqual(D(risk['planned_loss_with_reserve_usd']), D(2))
            self.assertTrue(e.ledger.get_switches().allow_new_entries)
            self.assertTrue(e.restart().ok)
            e.feed(SOL.symbol, '115.86')

    def test_pre_cap_validated_intent_cannot_bypass_new_quantity(self):
        e = Env(config=EngineConfig(max_planned_loss_usd=D(2)))
        self.addCleanup(e.ledger.close)
        e.meta._f[SOL.symbol] = SOL
        e.ready(); e.feed(SOL.symbol, '115.86')
        sig = e.signal(market=SOL.symbol, price='115.86', strategy='WY-PULLBACK', notional='600')
        row, _ = e.ledger.create_intent(signal_id=sig.signal_id, account_id=ACCT, market=SOL.symbol,
            side='buy', direction='long', order_type='market', purpose='ENTRY', strategy=sig.strategy,
            qty=D(5), leverage=10, reduce_only=False, tif='ioc')
        e.ledger.transition_order(row['client_order_id'], O.VALIDATED, 'pre-cap-intent')
        result = e.eng.submit_entry(sig)
        self.assertEqual(result.reason, 'sizing:existing_intent_requires_new_signal')
        self.assertEqual(e.mock.place_calls, 0)

    def test_confirmed_higher_fees_reduce_quantity(self):
        quantities = []
        for fee in (D('.00015'), D('.003')):
            e = Env(config=EngineConfig(max_planned_loss_usd=D(2), confirmed_taker_fee=fee))
            self.addCleanup(e.ledger.close)
            e.meta._f[SOL.symbol] = SOL
            e.ready(); e.feed(SOL.symbol, '115.86')
            result = e.eng.submit_entry(e.signal(market=SOL.symbol, price='115.86',
                                                strategy='WY-PULLBACK', notional='600'))
            self.assertEqual(result.status, 'ACKNOWLEDGED', result)
            quantities.append(D(e.order(result.client_order_id)['qty']))
        self.assertLess(quantities[1], quantities[0])
