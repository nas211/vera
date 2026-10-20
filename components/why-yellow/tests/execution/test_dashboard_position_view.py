"""The dashboard position view exposes the ledger's protection levels read-only."""
import unittest
from decimal import Decimal as D
from .test_tenant_fanout import Env, ctx


class DashboardPositionViewTests(unittest.TestCase):
    def setUp(self):
        self.e = Env(); self.addCleanup(self.e.cleanup)
        self.e.accounts.set(ctx("acctA", 100)); self.e.ready()
        self.t = self.e.reg.tenants["acctA"]

    def open(self, sid, direction, market="BTCUSDT-PERP", price="77000"):
        self.assertTrue(self.e.reg.dispatch(self.e.sig(sid, market=market, direction=direction, price=price))["acctA"].startswith("ACKNOWLEDGED"))
        self.e.fill_entries(price)
        self.t.eng.check_protection()

    def test_long_and_short_levels_match_ledger_protection(self):
        self.open("long-1", "long")
        self.open("short-1", "short", market="ETHUSDT-PERP", price="2500")
        views = {p["direction"]: p for p in self.t.runtime_info()["positions"]}
        self.assertEqual(set(views), {"long", "short"})
        for side, v in views.items():
            prot = self.t.ledger.get_protection(v["position_id"])
            self.assertIsNotNone(prot)
            self.assertEqual(v["stop_loss"], prot["stop_price"])
            self.assertEqual(v["take_profit"], prot["tp_price"])
            entry = D(v["entry_price"])
            if side == "long":
                self.assertLess(D(v["stop_loss"]), entry); self.assertGreater(D(v["take_profit"]), entry)
            else:
                self.assertGreater(D(v["stop_loss"]), entry); self.assertLess(D(v["take_profit"]), entry)
            self.assertIsNotNone(v["leverage"])

    def test_view_is_read_only(self):
        self.open("long-2", "long")
        before = [dict(r) for r in self.t.ledger.db.execute("SELECT * FROM protection").fetchall()]
        calls = self.e.mocks["acctA"].place_calls
        for _ in range(3): self.t.runtime_info()
        after = [dict(r) for r in self.t.ledger.db.execute("SELECT * FROM protection").fetchall()]
        self.assertEqual(before, after)
        self.assertEqual(self.e.mocks["acctA"].place_calls, calls)

    def test_position_without_protection_reports_none(self):
        self.assertTrue(self.e.reg.dispatch(self.e.sig("pending", direction="long"))["acctA"].startswith("ACKNOWLEDGED"))
        self.e.fill_entries()
        for p in self.t.runtime_info()["positions"]:
            if self.t.ledger.get_protection(p["position_id"]) is None:
                self.assertIsNone(p["stop_loss"]); self.assertIsNone(p["take_profit"])
