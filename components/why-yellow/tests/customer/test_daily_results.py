import unittest
import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch

from customer.results import ResultsReader, today_results


def ts(value):
    return datetime.fromisoformat(value).timestamp()


def row(net, closed='2026-10-17T09:00:00+02:00', **updates):
    return dict(state='CLOSED', pnl=net, closed_ts=ts(closed), pnl_source='yellow_history',
                fees=.1, volume=100, **updates)


NOW = ts('2026-10-17T10:00:00+02:00')


class TodayResultsTests(unittest.TestCase):
    def test_today_wins_do_not_hide_prior_losses(self):
        records = [row(-6.73, '2026-10-16T23:59:59+02:00'),
                   row(1.37), row(1.34), row(1.44), row(1.52)]
        with patch('customer.results.position_results', return_value=records):
            result = ResultsReader('/unused', clock=lambda: NOW).for_account('account', limit=1)
        self.assertAlmostEqual(result['summary']['realized_pnl'], -1.06)
        self.assertEqual(result['summary']['losses'], 1)
        self.assertEqual(len(result['trades']), 1)
        self.assertEqual(result['today']['closed_trades'], 4)
        self.assertEqual(result['today']['wins'], 4)
        self.assertEqual(result['today']['losses'], 0)
        self.assertEqual(result['today']['lost_usd'], 0)
        self.assertAlmostEqual(result['today']['net_pnl'], 5.67)

    def test_midnight_boundary_and_open_positions(self):
        records = [row(-99, '2026-10-16T23:59:59+02:00'),
                   row(2, '2026-10-17T00:00:00+02:00'),
                   row(-1), row(0), row(999, '2026-10-18T00:00:00+02:00'),
                   dict(row(999), state='PROTECTED')]
        result = today_results(records, NOW)
        self.assertEqual((result['won_usd'], result['lost_usd'], result['net_pnl']), (2, 1, 1))
        self.assertEqual((result['closed_trades'], result['wins'], result['losses'], result['breakeven']), (3, 1, 1, 1))

    def test_pending_pnl_and_funding_are_not_invented(self):
        result = today_results([row(None), dict(row(-.2), pnl_source='fills')], NOW)
        self.assertEqual(result['pending_pnl'], 1)
        self.assertEqual(result['funding_pending'], 1)
        self.assertEqual((result['wins'], result['losses'], result['net_pnl']), (0, 1, -.2))

    def test_empty_day_is_zero_but_failed_read_is_unavailable(self):
        self.assertEqual(today_results([], NOW)['net_pnl'], 0)
        with patch('customer.results.position_results', side_effect=OSError('unavailable')):
            result = ResultsReader('/unused', clock=lambda: NOW).for_account('account')
        self.assertFalse(result['today']['available'])
        self.assertIsNone(result['today']['net_pnl'])
        self.assertIsNone(result['summary']['realized_pnl'])

    def test_all_rows_before_pagination_and_account_isolation(self):
        records = [row(1) for _ in range(35)] + [row(-2)]
        with patch('customer.results.position_results', return_value=records) as read:
            result = ResultsReader('/tenants', clock=lambda: NOW).for_account('owned-account', limit=20)
        read.assert_called_once_with('/tenants/owned-account/ledger.sqlite')
        self.assertEqual(len(result['trades']), 20)
        self.assertEqual(result['today']['closed_trades'], 36)
        self.assertEqual(result['today']['net_pnl'], 33)

    def test_calendar_days_handle_dst_and_midnight_without_stale_cache(self):
        for date, hours in [('2026-03-29T12:00:00+02:00', 23), ('2026-10-25T12:00:00+01:00', 25)]:
            result = today_results([], ts(date))
            self.assertEqual((result['end_at']-result['start_at'])/3600, hours)
        clock = [ts('2026-10-17T23:59:59+02:00')]
        with patch('customer.results.position_results', return_value=[row(5)]):
            reader = ResultsReader('/unused', clock=lambda: clock[0])
            self.assertEqual(reader.for_account('account')['today']['net_pnl'], 5)
            clock[0] = ts('2026-10-18T00:00:00+02:00')
            self.assertEqual(reader.for_account('account')['today']['net_pnl'], 0)

    def test_exchange_close_time_controls_day_and_fees_and_funding_are_included(self):
        from tests.affiliate.test_affiliate_v2 import make_ledger
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'account', 'ledger.sqlite')
            make_ledger(path, [('long', '1', '100', '102', '.1', '.1')])
            with open(path + '.history.json', 'w') as handle:
                json.dump({'p0': dict(realized_pnl='2', total_trading_fee='.2', net_funding_fee='-.3',
                                     exit_price='102', closed_at='2026-10-16T22:00:00Z')}, handle)
            result = ResultsReader(directory, clock=lambda: NOW).for_account('account')
            self.assertEqual(result['today']['closed_trades'], 1)
            self.assertEqual(result['today']['net_pnl'], 1.5)
            self.assertEqual(result['today']['funding_pending'], 0)
