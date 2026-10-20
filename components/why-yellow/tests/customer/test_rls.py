import unittest
import psycopg
from .harness import FakeYellow, db, make_service, new_user, reset_db


class TestRowLevelSecurity(unittest.TestCase):
    """Two real identities A and B; every read/write of the other's rows must fail."""

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.A, cls.B = new_user("a@example.test"), new_user("b@example.test")
        svc, _ = make_service("ok_scopes")
        cls.svc = svc
        cls.a = svc.connect(cls.A, FakeYellow.KEY_OK + "A", FakeYellow.SECRET_OK + "A")
        cls.b = svc.connect(cls.B, FakeYellow.KEY_OK + "B", FakeYellow.SECRET_OK + "B")
        cls.aid, cls.bid = cls.a["account"]["id"], cls.b["account"]["id"]
        # give both a pending size + an event so every table has rows for both
        svc.set_position_size(cls.A, 200); svc.set_position_size(cls.B, 300)

    def _rows(self, ident, sql, args=()):
        with db().as_user(ident.user_id, ident.email) as c:
            return c.execute(sql, args).fetchall()

    def test_rls_enabled_on_every_customer_table(self):
        with db().service() as c:
            rows = c.execute("select tablename, rowsecurity from pg_tables where schemaname='public' and tablename like 'yellow_%'").fetchall()
        self.assertEqual(sorted(r["tablename"] for r in rows), ["yellow_account_events", "yellow_accounts", "yellow_bot_configs", "yellow_credentials"])
        self.assertTrue(all(r["rowsecurity"] for r in rows))

    def test_a_reads_only_own_account(self):
        rows = self._rows(self.A, "select id from public.yellow_accounts")
        self.assertEqual([str(r["id"]) for r in rows], [self.aid])
        self.assertEqual(self._rows(self.A, "select id from public.yellow_accounts where id = %s", (self.bid,)), [])
        rows = self._rows(self.B, "select id from public.yellow_accounts")
        self.assertEqual([str(r["id"]) for r in rows], [self.bid])

    def test_a_cannot_read_b_config_or_events(self):
        self.assertEqual(self._rows(self.A, "select account_id from public.yellow_bot_configs where account_id = %s", (self.bid,)), [])
        self.assertEqual([str(r["account_id"]) for r in self._rows(self.A, "select account_id from public.yellow_bot_configs")], [self.aid])
        self.assertEqual(self._rows(self.A, "select id from public.yellow_account_events where account_id = %s", (self.bid,)), [])
        self.assertTrue(all(str(r["account_id"]) == self.aid for r in self._rows(self.A, "select account_id from public.yellow_account_events")))

    def test_credentials_table_unreadable_by_any_user(self):
        for ident in (self.A, self.B):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self._rows(ident, "select * from public.yellow_credentials")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self._rows(ident, "select vault_api_key_id from public.yellow_credentials where account_id = %s", (self.aid,))

    def test_credential_reference_column_hidden(self):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self._rows(self.A, "select credential_ref from public.yellow_accounts")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self._rows(self.A, "select api_key_fingerprint from public.yellow_accounts")

    def test_vault_inaccessible_to_users(self):
        for sql in ("select * from vault.decrypted_secrets", "select * from vault.secrets", "select vault.create_secret('x','y')"):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self._rows(self.A, sql)

    def test_no_writes_from_users(self):
        for sql, args in (("update public.yellow_accounts set state='ACTIVE' where id=%s", (self.aid,)),
                          ("update public.yellow_accounts set state='ACTIVE' where id=%s", (self.bid,)),
                          ("update public.yellow_bot_configs set position_size_usd=2000 where account_id=%s", (self.bid,)),
                          ("update public.yellow_bot_configs set entries_paused=false where account_id=%s", (self.aid,)),
                          ("delete from public.yellow_accounts where id=%s", (self.bid,)),
                          ("insert into public.yellow_accounts(user_id) values (%s)", (self.B.user_id,)),
                          ("insert into public.yellow_credentials(account_id,user_id,vault_api_key_id,vault_api_secret_id) values (%s,%s,gen_random_uuid(),gen_random_uuid())", (self.bid, self.A.user_id))):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=sql):
                self._rows(self.A, sql, args)
        with db().service() as c:
            self.assertEqual(c.execute("select state from public.yellow_accounts where id=%s", (self.bid,)).fetchone()["state"], "VERIFIED")
            self.assertEqual(c.execute("select position_size_usd from public.yellow_bot_configs where account_id=%s", (self.bid,)).fetchone()["position_size_usd"], 300)

    def test_anon_sees_nothing(self):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with db().as_role("anon") as c:
                c.execute("select id from public.yellow_accounts").fetchall()

    def test_forged_claims_cannot_widen(self):
        # a JWT for A whose claims also carry B's id in other fields changes nothing: only sub matters
        with db().service() as c:
            pass
        with db()._acquire() as conn:
            pass
        import json
        conn = db()._acquire()
        try:
            with conn.transaction():
                conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": self.A.user_id, "role": "authenticated", "user_id": self.B.user_id, "account_id": self.bid}),))
                conn.execute("set local role authenticated")
                rows = conn.execute("select id from public.yellow_accounts").fetchall()
                self.assertEqual([str(r["id"]) for r in rows], [self.aid])
        finally:
            db()._release(conn)
