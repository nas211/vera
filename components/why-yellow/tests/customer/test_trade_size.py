import unittest
from .harness import ApiServer, make_service, new_user, reset_db, token

class TradeSizeTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.svc, self.fake = make_service()
        self.a, self.b = new_user(), new_user()
        self.before = self.svc.get_me(self.a)
        self.svc.get_me(self.b)
        self.server = ApiServer(self.svc)
        self.addCleanup(self.server.close)

    def save(self, body, auth=True):
        return self.server.call("POST", "/yellow/api/me/bot/trade-size",
            tok=token(self.a.user_id, self.a.email) if auth else None, body=body)

    def test_user_save_reset_and_isolation(self):
        self.assertIsNone(self.before["config"]["trade_margin_pct"])
        for pct in [1, 25, 100, None]:
            status, result, _, _ = self.save({"trade_margin_pct": pct})
            self.assertEqual(status, 200, result)
            self.assertEqual(result["config"]["trade_margin_pct"], pct)
            for k in ["leverage", "max_open_positions", "entries_paused", "position_size_usd"]:
                self.assertEqual(result["config"][k], self.before["config"][k])
            self.assertEqual(result["account"]["state"], self.before["account"]["state"])
            self.assertEqual(self.svc.get_me(self.a)["config"]["trade_margin_pct"], pct)
        self.assertIsNone(self.svc.get_me(self.b)["config"]["trade_margin_pct"])
        self.assertTrue(any(e["kind"] == "trade_size_changed" for e in result["events"]))
        self.assertEqual(self.fake.calls, [])

    def test_invalid_values_and_missing_field(self):
        for pct in [0, 101, -1, 1.5, True, False, "100", [], {}]:
            status, result, _, _ = self.save({"trade_margin_pct": pct})
            self.assertEqual((status, result["error"]), (422, "trade_size_not_allowed"))
        self.assertEqual(self.save({})[0], 422)
        self.assertIsNone(self.svc.get_me(self.a)["config"]["trade_margin_pct"])

    def test_auth_and_identity_cannot_be_overridden(self):
        self.assertEqual(self.save({"trade_margin_pct": 100}, auth=False)[0], 401)
        for key in ["user_id", "account_id", "tenant"]:
            self.assertEqual(self.save({"trade_margin_pct": 100, key: self.b.user_id})[0], 400)

    def test_runner_context_receives_saved_percentage(self):
        self.svc.connect(self.a, self.fake.KEY_OK, self.fake.SECRET_OK)
        self.save({"trade_margin_pct": 100})
        ctx = next(c for c in self.svc.list_execution_contexts() if c.user_id == self.a.user_id)
        self.assertEqual(ctx.trade_margin_pct, 100)
