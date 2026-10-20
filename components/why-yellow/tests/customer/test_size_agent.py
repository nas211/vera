import json
import os
import tempfile
import unittest
from decimal import Decimal

from .harness import FakeYellow, RuntimeStub, db, new_user, reset_db
from customer.accounts import AccountService
from customer.size_agent import recommend_trade_margin_pct

K, S = FakeYellow.KEY_OK, FakeYellow.SECRET_OK


def make_service(mode: str = "ok_scopes", runtime=None, state_dir=None):
    fake = FakeYellow(mode)
    svc = AccountService(db(), fake, runtime_info=runtime or RuntimeStub(), state_dir=state_dir)
    return svc, fake


class RecommendTradeMarginPctTests(unittest.TestCase):
    """Pure function: every balance tier and edge case, no DB involved."""

    def test_tiers_step_down_as_balance_grows(self):
        cases = [
            ("0", 20), ("1", 20), ("149.99", 20),
            ("150", 15), ("399.99", 15),
            ("400", 10), ("999.99", 10),
            ("1000", 5), ("1234.56", 5), ("2999.99", 5),
            ("3000", 2), ("7999.99", 2),
            ("8000", 1), ("1000000", 1),
        ]
        for balance, expected in cases:
            self.assertEqual(recommend_trade_margin_pct(Decimal(balance)), expected, balance)

    def test_accepts_strings_and_floats(self):
        self.assertEqual(recommend_trade_margin_pct("500"), 10)
        self.assertEqual(recommend_trade_margin_pct(500), 10)

    def test_invalid_or_missing_balance_returns_none(self):
        for bad in (None, "not-a-number", Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), object()):
            self.assertIsNone(recommend_trade_margin_pct(bad))


class SizeAgentActivationTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.rt = RuntimeStub()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.svc, self.fake = make_service("ok_scopes", self.rt, state_dir=self.tmpdir.name)
        self.u = new_user()

    def test_fresh_account_gets_a_default_on_first_activation(self):
        self.svc.connect(self.u, K, S)
        me = self.svc.get_me(self.u)
        self.assertIsNone(me["config"]["trade_margin_pct"])
        self.assertEqual(me["account"]["balance_equity"], "1234.56000000")   # -> 5% tier
        me = self.svc.activate(self.u)
        self.assertEqual(me["config"]["trade_margin_pct"], 5)
        self.assertIn("size_agent_default_applied", [e["kind"] for e in me["events"]])

    def test_manual_choice_before_activation_is_never_overridden(self):
        self.svc.connect(self.u, K, S)
        self.svc.set_trade_size(self.u, {"trade_margin_pct": 100})
        me = self.svc.activate(self.u)
        self.assertEqual(me["config"]["trade_margin_pct"], 100)
        self.assertNotIn("size_agent_default_applied", [e["kind"] for e in me["events"]])

    def test_reactivation_after_pause_never_reapplies(self):
        self.svc.connect(self.u, K, S)
        me = self.svc.activate(self.u)
        self.assertEqual(me["config"]["trade_margin_pct"], 5)
        self.svc.set_trade_size(self.u, {"trade_margin_pct": None})   # user clears it back to None
        self.svc.pause(self.u)
        me = self.svc.activate(self.u)
        # activated_at was already set on first activation, so the agent must not run again
        self.assertIsNone(me["config"]["trade_margin_pct"])
        kinds = [e["kind"] for e in me["events"]]
        self.assertEqual(kinds.count("size_agent_default_applied"), 1)   # only from the first activation

    def test_fixed_entry_override_blocks_the_default(self):
        self.svc.connect(self.u, K, S)
        aid = self.svc.get_me(self.u)["account"]["id"]
        tenant_dir = os.path.join(self.tmpdir.name, "tenants", aid)
        os.makedirs(tenant_dir, exist_ok=True)
        with open(os.path.join(tenant_dir, "fixed_entry_notional.json"), "w") as f:
            json.dump({"account_id": aid, "notional_usd": "50"}, f)
        me = self.svc.activate(self.u)
        self.assertIsNone(me["config"]["trade_margin_pct"])
        self.assertNotIn("size_agent_default_applied", [e["kind"] for e in me["events"]])

    def test_missing_state_dir_is_safe_and_still_applies_default(self):
        svc, fake = make_service("ok_scopes", RuntimeStub(), state_dir=None)
        u = new_user()
        svc.connect(u, K, S)
        me = svc.activate(u)
        self.assertEqual(me["config"]["trade_margin_pct"], 5)
