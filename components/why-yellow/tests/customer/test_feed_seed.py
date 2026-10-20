"""The live feed should gate entries on recent candle continuity."""
import unittest

from customer.feed import YellowMarketFeed
from execution.market_data import FreshnessMonitor


class FakeClient:
    def __init__(self, opens):
        self.opens = opens

    def klines(self, market, interval, limit):
        return [[x] for x in self.opens]


class FeedSeedTests(unittest.TestCase):
    def test_old_history_gap_does_not_block_recent_continuity(self):
        step = 300_000
        # Last value is the forming bar and is excluded from the seed.
        opens = [0, step, 10 * step, 11 * step, 12 * step, 13 * step]
        fresh = FreshnessMonitor(clock=lambda: 14 * step / 1000)
        feed = YellowMarketFeed(["BTC"], fresh, None,
                                client_factory=lambda: FakeClient(opens), clock=lambda: 14 * step / 1000)
        feed._seed()
        self.assertTrue(feed.seeded["BTC"])
        self.assertTrue(fresh.snapshot("BTC")["continuity_ok"])
        self.assertEqual(fresh._m("BTC").last_bar_open_ms, 12 * step)

    def test_recent_gap_still_blocks_entries(self):
        step = 300_000
        opens = [0, step, 10 * step, 12 * step, 13 * step]
        fresh = FreshnessMonitor(clock=lambda: 14 * step / 1000)
        feed = YellowMarketFeed(["BTC"], fresh, None,
                                client_factory=lambda: FakeClient(opens), clock=lambda: 14 * step / 1000)
        feed._seed()
        self.assertFalse(feed.seeded["BTC"])
        self.assertFalse(fresh.snapshot("BTC")["continuity_ok"])

    def test_stale_history_cannot_seed_current_feed(self):
        step = 300_000
        opens = [0, step, 2 * step, 3 * step]
        fresh = FreshnessMonitor(clock=lambda: 20 * step / 1000)
        feed = YellowMarketFeed(["BTC"], fresh, None,
                                client_factory=lambda: FakeClient(opens), clock=lambda: 20 * step / 1000)
        feed._seed()
        self.assertFalse(feed.seeded["BTC"])


if __name__ == "__main__":
    unittest.main()
