"""Feed grace, reward/risk geometry, slippage-reserved sizing, partial entries and per-strategy entry/exit management."""
import unittest
from discovery import rules
from discovery.engine import Engine, plan_entry, FEED_GRACE_SECONDS, RISK_PER_TRADE
from discovery.store import Store
from test_discovery import Clock, MARKET, LIMITS, SPEC

class Base(unittest.TestCase):
    spec=SPEC;depth=1 # queue ahead of a maker order is consumed before it fills
    def setUp(self):
        self.clock=Clock();self.e=Engine(Store(':memory:'),self.clock);self.e.markets={MARKET:LIMITS};self.seq=0
        self.e.action('alice','start');self.cid=self.e.propose('alice',self.spec,{});self.c=self.e.accounts['alice']['candidates'][self.cid]
        self.book()
    def book(self,bid=100,ask=100.02,depth=None):
        self.seq+=1;t=self.clock.t;depth=self.depth if depth is None else depth
        self.e.books.books[MARKET]=dict(bids={bid:float(depth)},asks={ask:float(depth)},received=t,source=t,seq=self.seq)
        self.e.books.marks[MARKET]=dict(rate=0.,next=t+28800,source=t,received=t)
    def step(self,seconds=.3,**book):
        self.clock.t+=seconds;self.book(**book);self.e.tick()
    def maker_fill(self,qty=100):
        self.e.maker_trade('alice',self.c,dict(price=100,qty=qty,ts=self.clock.t,side='sell',id=str(self.clock.t)),self.clock.t)

class FeedGraceTests(Base):
    def test_short_outage_keeps_trial_valid_and_records_gap(self):
        self.e.tick();self.step();self.maker_fill();p=self.c['position']
        self.e.gap('PUBLIC_FEED_UNAVAILABLE');self.clock.t+=30;self.e.tick()
        self.assertEqual(p['exit_reason'],'');self.assertTrue(p['valid'])
        self.step(.1);self.assertNotIn('feed_gap',p);self.assertAlmostEqual(p['feed_gap_seconds'],30.1,places=3)
        # Price through the stop after recovery is an ordinary, counted stop.
        self.step(1,bid=99.5,ask=99.52);self.assertEqual(p['exit_reason'],'STOP');self.assertTrue(p['valid'])
        self.step(bid=99.5,ask=99.52,depth=100);self.step(bid=99.5,ask=99.52,depth=100);self.assertIsNone(self.c['position'])
        t=self.c['trades'][-1];self.assertTrue(t['valid']);self.assertAlmostEqual(t['feed_gap_seconds'],30.1,places=3)
    def test_long_outage_invalidates_after_grace(self):
        self.e.tick();self.step();self.maker_fill();p=self.c['position']
        self.e.market_gap(MARKET,'PUBLIC_MARKET_STALLED');self.clock.t+=FEED_GRACE_SECONDS-1;self.e.tick()
        self.assertTrue(p['valid'])
        self.clock.t+=1;self.e.tick();self.assertFalse(p['valid']);self.assertEqual(p['exit_reason'],'PUBLIC_MARKET_STALLED')

class GeometryTests(unittest.TestCase):
    def test_reward_risk_and_hold_window_bounds(self):
        ok=dict(SPEC,stop_bps=40,target_bps=60);rules.validate(ok,{MARKET:LIMITS})
        for bad in (dict(ok,target_bps=59),dict(ok,stop_bps=39),dict(ok,mode='scalp',max_hold_seconds=90,stop_bps=20,target_bps=30),
                    dict(ok,max_hold_seconds=7199),dict(ok,max_hold_seconds=21601),dict(ok,stop_bps=100,target_bps=181),dict(ok,stop_bps=121,target_bps=181)):
            with self.assertRaises(ValueError):rules.validate(bad,{MARKET:LIMITS})
        rules.validate(dict(SPEC,max_hold_seconds=21600,stop_bps=120,target_bps=180),{MARKET:LIMITS})
    def test_legacy_versions_retire_when_flat(self):
        clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS};e.action('alice','start')
        cid=e.propose('alice',SPEC,{});c=e.accounts['alice']['candidates'][cid]
        c['spec']=dict(c['spec'],stop_bps=20,target_bps=10) # frozen pre-rule version
        e.tick();self.assertEqual(c['state'],'RETIRED');self.assertEqual(c['retirement']['reason'],'REWARD_RISK_RULE')
        self.assertEqual(c['revision']['status'],'PENDING')
    def test_short_hold_legacy_scalps_retire_when_flat(self):
        for legacy in (dict(mode='scalp',max_hold_seconds=90,stop_bps=10,target_bps=20),dict(max_hold_seconds=3600),dict(max_hold_seconds=43200),dict(stop_bps=20,target_bps=60),dict(stop_bps=120,target_bps=240)):
            with self.subTest(legacy=legacy):
                clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS};e.action('alice','start')
                cid=e.propose('alice',SPEC,{});c=e.accounts['alice']['candidates'][cid]
                c['spec']=dict(c['spec'],**legacy) # frozen pre-rule version
                e.tick();self.assertEqual(c['state'],'RETIRED');self.assertEqual(c['retirement']['reason'],'REWARD_RISK_RULE')
    def test_compliant_long_hold_stays_active(self):
        clock=Clock();e=Engine(Store(':memory:'),clock);e.markets={MARKET:LIMITS};e.action('alice','start')
        cid=e.propose('alice',SPEC,{});c=e.accounts['alice']['candidates'][cid]
        e.tick();self.assertEqual(c['state'],'TESTING')

class SizingTests(unittest.TestCase):
    def test_slippage_reserve_and_cap(self):
        limits=dict(LIMITS,step=.0001)
        plan=plan_entry(dict(SPEC,stop_bps=100,target_bps=200,mode='swing',max_hold_seconds=600),100,100.02,limits)
        self.assertAlmostEqual(plan['planned_loss'],RISK_PER_TRADE,places=2)
        # 1 USDT stop distance, 20bp execution reserve, and 4bp per fill fees.
        self.assertAlmostEqual(plan['qty'],RISK_PER_TRADE/(1.2+.0796),places=3)
        wide=plan_entry(dict(SPEC,stop_bps=100,target_bps=200,mode='swing',max_hold_seconds=600),100,101,limits)
        self.assertLess(wide['qty'],plan['qty']) # 3x a 100bp spread dominates the reserve
        tight=plan_entry(dict(SPEC,stop_bps=5,target_bps=10),100,100.02,limits)
        self.assertLessEqual(tight['qty']*tight['price'],500+1e-6)
    def test_default_execution_matches_legacy(self):
        explicit=rules.validate(dict(SPEC,entry='maker',breakeven_bps=0,trail_bps=0),{MARKET:LIMITS})
        self.assertEqual(explicit,SPEC);self.assertEqual(rules.executable_identity(explicit),rules.executable_identity(SPEC))
        self.assertTrue(rules.same_settings(explicit,SPEC));self.assertFalse(rules.same_settings(dict(SPEC,entry='taker'),SPEC))
    def test_legacy_spec_identity_unchanged(self):
        self.assertEqual(rules.identity(rules.validate(SPEC,{MARKET:LIMITS})),rules.identity(SPEC))
        self.assertEqual(rules.execution(SPEC),dict(entry='maker',breakeven_bps=0,trail_bps=0))

class PartialEntryTests(Base):
    def test_fragment_fill_is_flattened_uncounted(self):
        self.e.tick();o=self.c['entry'];self.step();self.maker_fill(qty=1+o["qty"]*.2)
        self.assertIsNotNone(self.c['position']);self.step(15)
        p=self.c['position'];self.assertEqual(p['exit_reason'],'PARTIAL_ENTRY');self.assertFalse(p['valid'])
        self.step();self.step();self.assertFalse(self.c['trades'][-1]['valid'])
        self.assertEqual(rules.metrics(self.c['trades'])['closed'],0)
    def test_majority_fill_continues(self):
        self.e.tick();o=self.c['entry'];self.step();self.maker_fill(qty=1+o["qty"]*.6);self.step(15)
        p=self.c['position'];self.assertEqual(p['exit_reason'],'');self.assertTrue(p['valid'])
        self.assertAlmostEqual(p['diagnostics']['entry_fill_ratio'],.6,places=2)

class TakerEntryTests(Base):
    spec=dict(SPEC,entry='taker');depth=1000
    def test_crosses_spread_after_latency(self):
        self.e.tick();o=self.c['entry'];self.assertEqual(o['kind'],'ENTRY_IOC');self.assertGreater(o['price'],100.02)
        self.step();p=self.c['position'];self.assertIsNone(self.c['entry'])
        self.assertEqual(p['entry'],100.02);self.assertEqual(p['qty'],o['qty']);self.assertEqual(p['diagnostics']['entry_style'],'taker')
    def test_price_bound_leaves_unfilled(self):
        self.e.tick();self.step(bid=100.5,ask=100.6);self.assertIsNone(self.c['position']);self.assertIsNone(self.c['entry'])
    def test_thin_book_fragment_is_flattened(self):
        self.e.tick();o=self.c['entry'];self.step(depth=o['qty']*.3)
        self.assertEqual(self.c['position']['exit_reason'],'PARTIAL_ENTRY')

class ManagedExitTests(Base):
    spec=dict(SPEC,stop_bps=40,target_bps=120,breakeven_bps=32,trail_bps=60)
    def test_breakeven_then_trail_only_tighten(self):
        self.e.tick();self.step();self.maker_fill();p=self.c['position'];initial=p['stop']
        self.step(bid=100.2,ask=100.22);self.assertEqual(p['stop'],initial)
        self.step(bid=100.36,ask=100.38);self.assertEqual(p['stop'],100.09)
        self.step(bid=100.8,ask=100.82);trailed=p['stop'];self.assertTrue(100.15<trailed<100.25,trailed)
        self.step(bid=100.4,ask=100.42);self.assertEqual(p['stop'],trailed)
        self.step(bid=trailed-.02,ask=trailed);self.assertEqual(p['exit_reason'],'STOP')
    def test_validation(self):
        for bad in (dict(self.spec,entry='market'),dict(self.spec,trail_bps=3),dict(self.spec,breakeven_bps=120)):
            with self.assertRaises(ValueError):rules.validate(bad,{MARKET:LIMITS})

if __name__=='__main__':unittest.main()
