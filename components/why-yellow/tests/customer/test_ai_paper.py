"""Local Postgres only; simulated accounts, no exchange transports."""
import time
import unittest
from decimal import Decimal as D
from customer.paper import PaperService
from customer.subscriptions import SubscriptionService
from customer.tenants import CanonicalSignal, CloseSignal
from customer.router import ExecutionRouter
from .harness import db, reset_db, new_user

AI = 'AI-' + 'a' * 64 + '-30-100-2'

class TestAIPaper(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.now = time.time()
        self.paper = PaperService(db(), SubscriptionService(db()), clock=lambda: self.now)
        self.users = [new_user(f'ai-paper-{i}@example.test') for i in range(3)]
        for user in self.users:
            self.paper.get(user)
        for user in self.users[:2]:
            self.paper.set_running(user, True)
        for m in ['BTCUSDT-PERP','ETHUSDT-PERP','SOLUSDT-PERP']:
            self.paper.on_mark(m, D(100), self.now)

    def sig(self, market='BTCUSDT-PERP', strategy=AI, side='long', ident='first'):
        return CanonicalSignal(ident, market, side, strategy, D(100), self.now, {})

    def test_all_running_users_caps_and_paused_account(self):
        out = self.paper.dispatch(self.sig())
        self.assertEqual(set(out), {u.user_id for u in self.users[:2]})
        self.assertEqual(set(out.values()), {'OPENED'})
        out = self.paper.dispatch(self.sig(market='ETHUSDT-PERP', ident='second'))
        self.assertEqual(set(out.values()), {'OPENED'})
        out = self.paper.dispatch(self.sig(market='SOLUSDT-PERP', ident='third'))
        self.assertEqual(set(out.values()), {'BLOCKED:ai_two_position_limit'})
        for user in self.users[:2]:
            a = self.paper.get(user)
            self.assertEqual(len(a['paper_open_positions']), 2)
            self.assertLess(sum(float(p['notional']) for p in a['paper_open_positions']), a['paper_equity'] * .1)
        self.assertFalse(self.paper.get(self.users[2])['paper_open_positions'])

    def test_core_first_owns_coin_in_either_direction(self):
        self.paper.dispatch(self.sig(strategy='WY-PULLBACK'))
        out = self.paper.dispatch(self.sig(side='short', ident='ai-second'))
        self.assertEqual(set(out.values()), {'BLOCKED:market_owned'})

    def test_filled_ai_owns_coin_and_core_close_cannot_close_it(self):
        self.paper.dispatch(self.sig())
        out = self.paper.dispatch(self.sig(strategy='WY-REVERT', side='short', ident='core-second'))
        self.assertEqual(set(out.values()), {'BLOCKED:market_owned'})
        self.assertEqual(self.paper.dispatch_close(CloseSignal('BTCUSDT-PERP','long','TIMEOUT',self.now)), {})
        self.assertEqual(len(self.paper.get(self.users[0])['paper_open_positions']), 1)

    def test_target_survives_restart_and_costs_reconcile(self):
        self.paper.dispatch(self.sig())
        self.paper = PaperService(db(), SubscriptionService(db()), clock=lambda: self.now)
        self.paper.on_mark('BTCUSDT-PERP', D('100.5'), self.now)
        for user in self.users[:2]:
            a = self.paper.get(user)
            self.assertFalse(a['paper_open_positions'])
            trade = a['paper_trade_history'][0]
            self.assertEqual(trade['reason'], 'AI_TARGET')
            self.assertAlmostEqual(a['paper_equity'] - 25000, float(trade['net_pnl']), places=6)
            self.assertAlmostEqual(a['paper_total_fees'], float(trade['fees']), places=6)
        self.paper.on_mark('BTCUSDT-PERP', D('101'), self.now)
        self.assertEqual(len(self.paper.get(self.users[0])['paper_trade_history']), 1)

    def test_stop_closes_even_if_customer_pauses(self):
        self.paper.dispatch(self.sig(side='short'))
        self.paper.set_running(self.users[0], False)
        self.paper.on_mark('BTCUSDT-PERP', D('102'), self.now)
        self.assertEqual(self.paper.get(self.users[0])['paper_trade_history'][0]['reason'], 'AI_STOP')

    def test_timeout_and_funding(self):
        self.paper.dispatch(self.sig())
        self.now += 601
        self.paper.on_mark('BTCUSDT-PERP', D('100'), self.now)
        a = self.paper.get(self.users[0]); trade = a['paper_trade_history'][0]
        self.assertEqual(trade['reason'], 'AI_TIMEOUT')
        self.assertGreater(float(trade['funding']), 0)
        self.assertAlmostEqual(a['paper_equity'] - 25000, float(trade['net_pnl']), places=6)

    def test_stale_marks_block_entries_and_do_not_trigger_exits(self):
        self.now += 31
        self.assertEqual(set(self.paper.dispatch(self.sig()).values()), {'BLOCKED:ai_stale_mark'})
        self.paper.on_mark('BTCUSDT-PERP', D(100), self.now)
        self.paper.dispatch(self.sig())
        self.paper.on_mark('BTCUSDT-PERP', D(90), self.now-31)
        self.assertEqual(len(self.paper.get(self.users[0])['paper_open_positions']), 1)

    def test_manual_close_remains_available(self):
        self.paper.dispatch(self.sig())
        a = self.paper.get(self.users[0])
        self.assertEqual(self.paper.close_position(self.users[0], a['paper_open_positions'][0]['id']), 'CLOSED')
        self.assertEqual(len(self.paper.get(self.users[1])['paper_open_positions']), 1)

    def test_per_account_failure_does_not_rollback_other_users(self):
        original = self.paper._open
        def fail_one(c, a, sig):
            if str(a['user_id']) == self.users[0].user_id:
                c.execute('select 1/0')
            return original(c,a,sig)
        self.paper._open = fail_one
        out = self.paper.dispatch(self.sig())
        self.assertEqual(out[self.users[0].user_id], 'ERROR')
        self.assertEqual(out[self.users[1].user_id], 'OPENED')

    def test_concurrent_duplicate_signals_cannot_double_open(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.paper.dispatch(self.sig()), range(2)))
        self.assertEqual(len(self.paper.get(self.users[0])['paper_open_positions']), 1)

class TestAIRouting(unittest.TestCase):
    def test_paper_and_live_switches_are_independent_and_weex_excluded(self):
        class Sink:
            def __init__(self): self.calls=[]
            def dispatch(self,s): self.calls.append(s); return 'OK'
        y,p,w = Sink(),Sink(),Sink(); r=ExecutionRouter(y,p,w)
        self.assertEqual(r.dispatch_ai('x',live_enabled=False,paper_enabled=True), {'paper':'OK'})
        self.assertEqual(y.calls,[]);self.assertEqual(w.calls,[])
        self.assertEqual(r.dispatch_ai('y',live_enabled=True,paper_enabled=False), {'yellow':'OK'})
        self.assertEqual(p.calls,['x'])

    def test_live_failure_does_not_prevent_paper_dispatch(self):
        class Bad:
            def dispatch(self,s): raise RuntimeError('private error detail')
        class Good:
            def dispatch(self,s): return 'OK'
        self.assertEqual(ExecutionRouter(Bad(),Good()).dispatch_ai('x',paper_enabled=True),
                         {'yellow':'ERROR:RuntimeError','paper':'OK'})
