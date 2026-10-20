import os, unittest
from .harness import NETWORK_ATTEMPTS, Env


class TestZeroRealOrders(unittest.TestCase):
    def test_20_real_yellow_orders_placed_is_zero(self):
        self.assertEqual(NETWORK_ATTEMPTS["count"], 0, NETWORK_ATTEMPTS.get("events"))
        self.assertNotIn("YELLOW_ARMED", os.environ)
        e = Env()
        self.assertEqual(e.mock.real_orders_placed, 0)
        self.assertIsNone(e.mock.REAL_ORDER_ENDPOINT)
