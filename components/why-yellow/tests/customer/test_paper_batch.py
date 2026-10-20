"""Local Postgres: preserve all simulated triggers and accounting during batching."""
import unittest
from decimal import Decimal as D
from customer.paper import PaperService
from customer.subscriptions import SubscriptionService
from .test_ai_paper import TestAIPaper
from .harness import db, reset_db


class TestPaperBatch(unittest.TestCase):
    setUp = TestAIPaper.setUp
    sig = TestAIPaper.sig

    def test_transient_long_and_short_stops_survive_recovery(self):
        for side, stop in [('long', '98'), ('short', '102')]:
            with self.subTest(side=side):
                self.setUp()
                self.paper.dispatch(self.sig(side=side))
                self.paper.on_marks([('BTCUSDT-PERP', D(stop), self.now),
                                     ('BTCUSDT-PERP', D(100), self.now)])
                for user in self.users[:2]:
                    a = self.paper.get(user)
                    self.assertFalse(a['paper_open_positions'])
                    self.assertEqual(len(a['paper_trade_history']), 1)
                    t = a['paper_trade_history'][0]
                    self.assertEqual(t['reason'], 'AI_STOP')
                    expected = D(stop) * (D('0.9998') if side == 'long' else D('1.0002'))
                    self.assertEqual(t['exit_price'], expected)
                    self.assertAlmostEqual(a['paper_equity'] - 25000, float(t['net_pnl']), places=6)
                self.assertFalse(self.paper.get(self.users[2])['paper_trade_history'])

    def test_batch_matches_sequential_multimarket_equity_and_peak(self):
        snapshots = []
        for batch in [False, True]:
            self.setUp()
            self.paper.dispatch(self.sig(strategy='WY-PULLBACK'))
            self.paper.dispatch(self.sig(market='ETHUSDT-PERP',strategy='WY-PULLBACK',side='short',ident='eth'))
            marks = [(m, D(px), self.now) for m, px in [
                ('BTCUSDT-PERP','101'),('ETHUSDT-PERP','99'),('BTCUSDT-PERP','98'),
                ('ETHUSDT-PERP','102'),('BTCUSDT-PERP','100'),('ETHUSDT-PERP','100')]]
            if batch:
                self.paper.on_marks(marks)
            else:
                for mark in marks:
                    self.paper.on_mark(*mark)
            with db().service() as c:
                snapshots.append(c.execute('''select cash_balance,equity,unrealized_pnl,peak_equity,max_drawdown,
                    used_margin,total_fees,total_funding from public.why_paper_accounts
                    where user_id=%s''',(self.users[0].user_id,)).fetchone())
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertGreater(snapshots[0]['max_drawdown'],0)

    def test_stale_tick_order_and_disconnect_invalidate_mark(self):
        self.paper.dispatch(self.sig())
        self.paper.on_marks([('BTCUSDT-PERP',D(100),self.now),('BTCUSDT-PERP',D(90),self.now-31)])
        self.assertEqual(self.paper.mark_times['BTCUSDT-PERP'],0)
        self.assertTrue(self.paper.get(self.users[0])['paper_open_positions'])
        self.paper.on_marks([('BTCUSDT-PERP',D(90),self.now-31),('BTCUSDT-PERP',D(100),self.now)])
        self.assertEqual(self.paper.mark_times['BTCUSDT-PERP'],self.now)
        self.paper.on_disconnect()
        self.assertEqual(self.paper.mark_times,{})

    def test_intermediate_target_and_funding_are_not_overwritten(self):
        self.paper.dispatch(self.sig())
        self.now += 30
        self.paper.on_marks([('BTCUSDT-PERP',D('100.5'),self.now),('BTCUSDT-PERP',D('99'),self.now)])
        a = self.paper.get(self.users[0]); t = a['paper_trade_history'][0]
        self.assertEqual(t['reason'],'AI_TARGET')
        self.assertGreater(t['funding'],0)
        self.assertEqual(t['exit_price'],D('100.5')*D('.9998'))
        self.assertAlmostEqual(a['paper_equity']-25000,float(t['net_pnl']),places=6)

    def test_batch_reduces_database_transactions(self):
        self.paper.dispatch(self.sig(strategy='WY-PULLBACK'))
        original = self.paper.db.service
        calls = []
        def service():
            calls.append(1)
            return original()
        self.paper.db.service = service
        try:
            self.paper.on_marks([('BTCUSDT-PERP',D(100)+D(i)/100,self.now) for i in range(100)])
        finally:
            self.paper.db.service = original
        self.assertEqual(len(calls),1)
