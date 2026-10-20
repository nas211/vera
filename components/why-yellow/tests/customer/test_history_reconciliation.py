import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from affiliate.pnl import position_results
from customer.result_history import HistorySync, match_history, same_entry_price
from tests.affiliate.test_affiliate_v2 import make_ledger


class Client:
    asid = 'test-account'

    def __init__(self, history, trades):
        self.history, self.fills = history, trades
        self.calls = []

    def _req(self, method, path, params, **kw):
        self.calls.append((method, path, params))
        since = datetime.fromisoformat(params['opened_from']).timestamp()
        return dict(positions=[h for h in self.history if datetime.fromisoformat(h['opened_at']).timestamp() >= since], has_more=False)

    def trades(self, market):
        return self.fills


class HistoryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'ledger.sqlite')
        make_ledger(self.path, [('long', '1', '100', '102', '.1', '.1')])
        with sqlite3.connect(self.path) as db:
            db.execute("update positions set opened_ts=1000,state_ts=2000")
            db.execute("update orders set created_ts=90,venue_order_id='venue-entry' where purpose='ENTRY'")
        iso = lambda sec: datetime.fromtimestamp(sec, timezone.utc).isoformat()
        self.h = dict(id='venue-position', market='BTCUSDT-PERP', direction='long', entry_price='100',
                      max_held='1', opened_at=iso(100), closed_at=iso(200), realized_pnl='2',
                      total_trading_fee='.2', net_funding_fee='-.3', exit_price='102')
        self.trade = dict(market='BTCUSDT-PERP', client_order_id='why-e0', order_uuid='venue-entry', executed_at=iso(100))

    def test_delayed_ledger_time_uses_owned_exchange_fill_and_earlier_query_start(self):
        client = Client([self.h], [self.trade])
        sync = HistorySync(lambda aid: client)
        sync.sync('account', self.path)
        row = position_results(self.path)[0]
        self.assertEqual(row['pnl_source'], 'yellow_history')
        self.assertEqual(row['pnl'], 1.5)
        self.assertEqual(row['closed_ts'], 200)
        self.assertEqual(client.calls[0][0], 'GET')
        count = len(client.calls)
        sync.sync('account', self.path)
        self.assertEqual(len(client.calls), count)

    def test_foreign_order_or_market_cannot_supply_timestamp(self):
        for update in ({'client_order_id':'other'}, {'order_uuid':'other'}, {'market':'OTHER'}):
            with self.subTest(update=update):
                HistorySync(lambda aid: Client([self.h], [dict(self.trade, **update)])).sync('account', self.path)
                self.assertFalse(os.path.exists(self.path+'.history.json'))

    def test_ledger_entry_must_belong_to_same_account(self):
        with sqlite3.connect(self.path) as db:
            db.execute("update orders set account_id='foreign' where purpose='ENTRY'")
        HistorySync(lambda aid: Client([self.h], [self.trade])).sync('account', self.path)
        self.assertFalse(os.path.exists(self.path+'.history.json'))

    def test_price_serialization_tolerance_is_tiny(self):
        self.assertTrue(same_entry_price('273.5977066228790366721401204', '273.5977066228790367'))
        self.assertFalse(same_entry_price('273.5977066228790366721401204', '273.597707'))
        self.assertFalse(same_entry_price('0', '0'))

    def test_same_market_does_not_override_quantity_direction_or_unique_identity(self):
        row = dict(position_results(self.path)[0], opened_ts=100)
        self.assertEqual(match_history([row], [self.h]), {'p0': self.h})
        for update in ({'max_held':'2'}, {'direction':'short'}, {'entry_price':'100.001'}):
            self.assertEqual(match_history([row], [dict(self.h, **update)]), {})
        self.assertEqual(match_history([row, dict(row, position_id='other')], [self.h]), {})
        self.assertEqual(match_history([row], [self.h, dict(self.h, id='other')]), {})
