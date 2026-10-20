import copy
import json
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from tests.execution.harness import NETWORK_ATTEMPTS
from customer.strategy_profiles import strategy_profile
from customer.scalp.strategy import CONFIG, RULES, MODULES
from customer.accounts import AccountService, AccountError, ACCOUNT_PUBLIC_COLS
from execution.sizing import HARD_LOSS_LIMIT_ACCOUNT, entry_loss_limit
from execution.types import D


class StrategyProfiles(unittest.TestCase):
    def setUp(self):
        self.config = dict(trading_style='scalp', leverage=10, max_open_positions=3,
                           trade_margin_pct=25, entries_paused=True, position_size_usd=20,
                           pending_position_size_usd=None, activated_at=None)

    def test_public_profile_matches_execution_and_preserves_swing_settings(self):
        before = copy.deepcopy(self.config)
        profile = strategy_profile('test-account', self.config,1000)
        self.assertIsNone(profile['entry_margin_usd'])
        self.assertEqual(profile['sizing_mode'],'exchange_minimum')
        self.assertEqual(profile['leverage'], CONFIG['leverage'])
        self.assertEqual(profile['target_pct'], CONFIG['target_bps'] / 100)
        self.assertEqual(profile['quote_refresh_seconds'], CONFIG['refresh_seconds'])
        self.assertEqual(profile['passive_exit_after_seconds'], RULES['max_hold_seconds'])
        self.assertEqual(profile['max_open_positions'], 100)
        self.assertEqual(profile['strategy_entry_slots'], CONFIG['max_entry_slots'])
        self.assertEqual(profile['gross_notional_cap_usd'], CONFIG['gross_notional_cap'])
        self.assertEqual(profile['per_market_notional_cap_usd'], CONFIG['per_market_cap'])
        self.assertEqual(profile['per_side_notional_cap_usd'], RULES['side_notional_limit'])
        self.assertIsNone(profile['daily_trade_target'])
        self.assertIsNone(profile['activation_blocked_reason'])
        self.assertEqual(self.config, before)
        self.assertEqual(json.loads(json.dumps(profile)), profile)

    def test_returned_markets_cannot_mutate_execution_configuration(self):
        profile = strategy_profile('test-account', self.config,1000)
        profile['active_markets'].clear()
        self.assertTrue(MODULES['v2'].ACTIVE_MARKETS)
        self.assertEqual(strategy_profile('test-account', self.config,1000)['exit_only_markets'],
                         sorted(MODULES['v2'].EXCLUDED_ENTRIES))

    def test_approved_software_policy_is_specific_to_account_and_scalp(self):
        protected = HARD_LOSS_LIMIT_ACCOUNT.split(':', 1)[1]
        profile = strategy_profile(protected, self.config)
        self.assertIsNone(profile['activation_blocked_reason'])
        self.assertEqual((profile['emergency_stop_usd'],profile['emergency_stop_mode']),(1,'bot_managed'))
        self.assertIsNone(strategy_profile('another-account', self.config)['emergency_stop_usd'])
        self.assertEqual(entry_loss_limit(HARD_LOSS_LIMIT_ACCOUNT),D('2'))
        self.config['trading_style']='swing'
        self.assertNotIn('emergency_stop_usd',strategy_profile(protected,self.config))

    def test_unapproved_loss_policy_account_stays_blocked(self):
        with patch('customer.scalp.policy.entry_loss_limit',return_value=D('2')):
            profile=strategy_profile('another-account',self.config)
        self.assertIn('mandatory planned-loss limit',profile['activation_blocked_reason'])
        self.assertIsNone(profile['emergency_stop_usd'])

    def test_activation_accepts_approved_account_and_rejects_unapproved_policy(self):
        for approved in (True,False):
            with self.subTest(approved=approved):
                aid=HARD_LOSS_LIMIT_ACCOUNT.split(':',1)[1] if approved else 'another-account'
                service=AccountService.__new__(AccountService)
                service.db=MagicMock()
                conn=service.db.service.return_value.__enter__.return_value
                conn.execute.return_value.fetchone.return_value=dict(self.config)
                service._get_owned=lambda *args:dict(id=aid,state='PAUSED',verification_state='VERIFIED')
                service._event=MagicMock()
                service.get_me=lambda ident: 'activated'
                with patch('customer.scalp.policy.entry_loss_limit',return_value=D('2')):
                    if approved:
                        self.assertEqual(service.activate(SimpleNamespace(user_id='owner'),'scalp'),'activated')
                        service._event.assert_called_once_with(conn,aid,'owner','activated')
                    else:
                        with self.assertRaisesRegex(AccountError,'scalp_account_loss_policy'):
                            service.activate(SimpleNamespace(user_id='owner'),'scalp')
                        service._event.assert_not_called()

    def test_swing_switch_back_keeps_account_settings(self):
        self.config['trading_style'] = 'swing'
        profile = strategy_profile('test-account', self.config,1000)
        self.assertEqual(profile, dict(id='swing', name='AI Agents — Swing', leverage=10, max_open_positions=3))
        self.assertEqual(self.config['trade_margin_pct'], 25)

    def test_account_response_contains_selected_profile(self):
        service = AccountService.__new__(AccountService)
        service.runtime_info = lambda _: {}
        account = dict.fromkeys(ACCOUNT_PUBLIC_COLS)
        account.update(id='test-account', state='PAUSED', last_verify_attempt_at=None, disconnected_at=None,
                       balance_equity='250')
        for style in ('swing', 'scalp'):
            self.config['trading_style'] = style
            result = service._public(account, self.config)
            self.assertEqual(result['config']['strategy_profile']['id'], style)
            self.assertTrue(result['config']['entries_paused'])
        self.assertEqual(NETWORK_ATTEMPTS['count'], 0)
