import unittest
from pathlib import Path
from .harness import ApiServer, db, make_service, new_user, reset_db, token

class PositionLimitTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        migration = Path(__file__).parents[2] / "supabase/migrations/20261006000000_yellow_position_limit.sql"
        with db().service() as c:
            c.execute(migration.read_text())
        self.svc, self.fake = make_service()
        self.a, self.b = new_user(), new_user()
        self.before_a = self.svc.get_me(self.a)
        self.before_b = self.svc.get_me(self.b)
        self.server = ApiServer(self.svc)
        self.addCleanup(self.server.close)

    def save(self, body, tok=True):
        return self.server.call("POST", "/yellow/api/me/bot/position-limit",
            tok=token(self.a.user_id, self.a.email) if tok else None, body=body)

    def test_save_owned_limit_only_and_audit(self):
        status, result, _, _ = self.save({"max_open_positions": 1})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["max_open_positions"], 1)
        for field in ["leverage", "position_size_usd", "entries_paused"]:
            self.assertEqual(result["config"][field], self.before_a["config"][field])
        self.assertEqual(result["account"]["state"], self.before_a["account"]["state"])
        self.assertEqual(self.svc.get_me(self.b)["config"]["max_open_positions"], 8)
        self.assertTrue(any(e["kind"] == "position_limit_changed" for e in result["events"]))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.save({"max_open_positions": 8})[0], 200)

    def test_reject_invalid_limits(self):
        for value in [0, 9, -1, True, False, 1.5, "1", None, [], {}]:
            status, result, _, _ = self.save({"max_open_positions": value})
            self.assertEqual((status, result["error"]), (422, "position_limit_not_allowed"), value)
        self.assertEqual(self.svc.get_me(self.a)["config"]["max_open_positions"], 8)

    def test_authentication_and_identity_override(self):
        self.assertEqual(self.save({"max_open_positions": 1}, tok=False)[0], 401)
        for identity in ["user_id", "account_id", "tenant"]:
            self.assertEqual(self.save({"max_open_positions": 1, identity: self.b.user_id})[0], 400)
        self.assertEqual(self.svc.get_me(self.b)["config"]["max_open_positions"], 8)

    def test_database_rejects_out_of_range(self):
        import psycopg
        for value in [0, 9]:
            with self.assertRaises(psycopg.errors.CheckViolation):
                with db().service() as c:
                    c.execute("update public.yellow_bot_configs set max_open_positions=%s where user_id=%s", (value, self.a.user_id))
