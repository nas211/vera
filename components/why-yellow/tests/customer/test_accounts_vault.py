import json, unittest
from decimal import Decimal
from .harness import FakeYellow, RuntimeStub, db, make_service, new_user, reset_db, scan_public_tables_for
from customer.accounts import AccountError, mask_key
from customer.context import CustomerExecutionContext, LazyYellowClient, VaultCredentialResolver
from customer.vault import Vault

K, S = FakeYellow.KEY_OK, FakeYellow.SECRET_OK


class TestConnectAndVault(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.rt = RuntimeStub()
        self.svc, self.fake = make_service("ok_scopes", self.rt)
        self.u = new_user()

    def test_connect_stores_only_in_vault_and_masks(self):
        out = self.svc.connect(self.u, K, S)
        acct = out["account"]
        self.assertEqual(acct["state"], "VERIFIED")
        self.assertEqual(acct["verification_state"], "VERIFIED")
        self.assertEqual(acct["permission_source"], "yellow_api")
        self.assertEqual(acct["api_key_masked"], mask_key(K))
        self.assertTrue(acct["api_key_masked"].startswith(K[:4]) and acct["api_key_masked"].endswith(K[-4:]))
        self.assertNotIn(K, acct["api_key_masked"])
        self.assertEqual(acct["balance_equity"], "1234.56000000")
        # response payload never contains secrets
        s = json.dumps(out)
        self.assertNotIn(S, s); self.assertNotIn(K, s)
        # plaintext absent from every application table and from the raw ciphertext
        self.assertEqual(scan_public_tables_for(S), []); self.assertEqual(scan_public_tables_for(K), [])
        # vault holds exactly two secrets for this account, readable server-side only
        with db().service() as c:
            cred = c.execute("select * from public.yellow_credentials").fetchone()
            self.assertEqual(str(cred["user_id"]), self.u.user_id)
            self.assertEqual(Vault.read(c, str(cred["vault_api_key_id"])).reveal(), K)
            self.assertEqual(Vault.read(c, str(cred["vault_api_secret_id"])).reveal(), S)
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 2)
            row = c.execute("select credential_ref from public.yellow_accounts").fetchone()
            self.assertEqual(str(row["credential_ref"]), str(cred["id"]))
        self.assertEqual(repr(Vault.read.__call__), repr(Vault.read.__call__))  # no-op sanity
        self.assertEqual(self.fake.calls, [(K, S)])

    def test_secretbox_repr_is_opaque(self):
        with db().service() as c:
            sid = Vault.create(c, "why-test-secretbox", "super-secret-value-123456")
            box = Vault.read(c, sid)
            Vault.delete(c, sid)
        self.assertNotIn("super-secret", repr(box)); self.assertNotIn("super-secret", str(box))
        self.assertEqual(box.reveal(), "super-secret-value-123456")

    def test_invalid_credential_format_rejected_before_any_storage(self):
        for k, s in (("short", S), (K, "short"), ("has space in it 123456", S), (K, K), (None, S), (123, S)):
            with self.assertRaises(AccountError) as cm:
                self.svc.connect(self.u, k, s)
            self.assertEqual(cm.exception.status, 422)
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 0)
        self.assertEqual(self.fake.calls, [])

    def test_invalid_credential_at_yellow_purges_and_disconnects(self):
        self.fake.mode = "invalid"
        out = self.svc.connect(self.u, K, S)
        self.assertEqual(out["account"]["state"], "DISCONNECTED")
        self.assertEqual(out["account"]["verification_state"], "FAILED")
        self.assertEqual(out["account"]["state_reason"], "invalid_credential")
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 0)
            self.assertEqual(c.execute("select count(*) as n from public.yellow_credentials").fetchone()["n"], 0)
        self.assertEqual(scan_public_tables_for(S), [])

    def test_yellow_unavailable_sets_pending_not_verified(self):
        self.fake.mode = "unreachable"
        out = self.svc.connect(self.u, K, S)
        self.assertEqual(out["account"]["state"], "PENDING_YELLOW_VERIFICATION")
        self.assertEqual(out["account"]["verification_state"], "PENDING")
        self.assertTrue(out["account"]["verification_reason"].startswith("yellow_unreachable"))
        self.assertIsNone(out["account"]["verified_at"])
        with self.assertRaises(AccountError) as cm:
            self.svc.activate(self.u)
        self.assertEqual(cm.exception.status, 409)
        # secrets kept so the customer can retry verification later
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 2)
        # later re-verify from Vault (server-side) succeeds without the browser resending anything
        self.fake.mode = "ok_scopes"
        out = self.svc.verify(self.u)
        self.assertEqual(out["account"]["state"], "VERIFIED")
        self.assertEqual(self.fake.calls[-1], (K, S))

    def test_permission_metadata_unavailable_stays_pending(self):
        self.fake.mode = "ok_no_scopes"
        out = self.svc.connect(self.u, K, S)
        a = out["account"]
        self.assertEqual(a["state"], "PENDING_YELLOW_VERIFICATION")
        self.assertEqual(a["verification_state"], "PENDING")
        self.assertEqual(a["verification_reason"], "permission_metadata_unavailable")
        self.assertEqual(a["permission_source"], "unavailable")
        self.assertEqual(a["balance_equity"], "1234.56000000")   # credential worked; permissions unknown
        with self.assertRaises(AccountError):
            self.svc.activate(self.u)

    def test_forbidden_permission_rejected_no_override(self):
        self.fake.mode = "forbidden"
        out = self.svc.connect(self.u, K, S)
        a = out["account"]
        self.assertEqual(a["state"], "DISCONNECTED")
        self.assertEqual(a["verification_state"], "FORBIDDEN_PERMISSION")
        self.assertEqual(a["state_reason"], "forbidden_permission")
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 0)
        # owner attestation cannot whitelist a withdrawal scope either
        self.fake.mode = "ok_no_scopes"
        self.svc.connect(self.u, K, S)
        aid = self.svc.get_me(self.u)["account"]["id"]
        with self.assertRaises(AccountError) as cm:
            self.svc.attest_permissions(aid, ["read:futures", "trade:futures", "withdraw:spot"], "owner checked UI 2026-09-23", "owner")
        self.assertEqual(cm.exception.code, "forbidden_permission")
        me = self.svc.get_me(self.u)
        self.assertEqual((me["account"]["state"], me["account"]["verification_state"], me["account"]["state_reason"]), ("DISCONNECTED", "FORBIDDEN_PERMISSION", "forbidden_permission"))
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 0)
        self.assertEqual(me["events"][0]["kind"], "owner_permission_rejection")
        with self.assertRaises(AccountError):
            self.svc.activate(self.u)

    def test_missing_trade_scope_fails(self):
        self.fake.mode = "missing_trade"
        out = self.svc.connect(self.u, K, S)
        self.assertEqual(out["account"]["verification_reason"], "missing_required_scope")
        self.assertEqual(out["account"]["state"], "DISCONNECTED")

    def test_owner_attestation_path(self):
        self.fake.mode = "ok_no_scopes"
        self.svc.connect(self.u, K, S)
        aid = self.svc.get_me(self.u)["account"]["id"]
        with self.assertRaises(AccountError):
            self.svc.attest_permissions(aid, ["read:futures", "trade:futures"], "", "owner")
        r = self.svc.attest_permissions(aid, ["read:futures", "trade:futures"], "owner saw scopes in Yellow UI, ticket #1", "owner-cli")
        self.assertTrue(r["verified"])
        me = self.svc.get_me(self.u)
        self.assertEqual(me["account"]["state"], "VERIFIED")
        self.assertEqual(me["account"]["permission_source"], "owner_attestation")
        self.assertEqual(me["events"][0]["kind"], "owner_permission_attestation")

    def test_owner_attestation_requires_read_and_trade_on_futures(self):
        self.fake.mode = "ok_no_scopes"
        self.svc.connect(self.u, K, S)
        aid = self.svc.get_me(self.u)["account"]["id"]
        for scopes in ([], ["read:futures"], ["trade:futures"],
                       ["read:spot", "trade:spot"], ["unreadable:futures", "trade:futures"]):
            with self.subTest(scopes=scopes):
                with self.assertRaises(AccountError) as cm:
                    self.svc.attest_permissions(aid, scopes, "owner checked Yellow UI", "owner-cli")
                self.assertEqual((cm.exception.code, cm.exception.status), ("missing_required_scope", 422))
                self.assertEqual(self.svc.get_me(self.u)["account"]["state"], "PENDING_YELLOW_VERIFICATION")
                with self.assertRaises(AccountError):
                    self.svc.activate(self.u)

    def test_reverify_does_not_accept_incomplete_stored_attestation(self):
        self.fake.mode = "ok_no_scopes"
        self.svc.connect(self.u, K, S)
        aid = self.svc.get_me(self.u)["account"]["id"]
        with db().service() as c:
            c.execute("update public.yellow_accounts set permission_source='owner_attestation', permission_scopes=%s where id=%s",
                      (["read:futures"], aid))
        me = self.svc.verify(self.u)
        self.assertEqual(me["account"]["state"], "PENDING_YELLOW_VERIFICATION")
        self.assertEqual(me["account"]["verification_reason"], "permission_metadata_unavailable")

    def test_duplicate_binding_across_users_rejected(self):
        self.svc.connect(self.u, K, S)
        other = new_user()
        with self.assertRaises(AccountError) as cm:
            self.svc.connect(other, K, S + "x")
        self.assertEqual((cm.exception.code, cm.exception.status), ("credential_already_bound", 409))
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 2)
        self.assertEqual(self.svc.get_me(other)["account"]["state"], "DISCONNECTED")

    def test_reconnect_rotates_in_place(self):
        self.svc.connect(self.u, K, S)
        with db().service() as c:
            before = c.execute("select vault_api_key_id, vault_api_secret_id from public.yellow_credentials").fetchone()
        K2, S2 = K[:-4] + "ZZZZ", S[:-4] + "QQQQ"
        out = self.svc.connect(self.u, K2, S2)
        self.assertEqual(out["account"]["api_key_masked"], mask_key(K2))
        with db().service() as c:
            after = c.execute("select vault_api_key_id, vault_api_secret_id from public.yellow_credentials").fetchone()
            self.assertEqual(before, after)
            self.assertEqual(Vault.read(c, str(after["vault_api_key_id"])).reveal(), K2)
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 2)
        self.assertEqual(scan_public_tables_for(S), []); self.assertEqual(scan_public_tables_for(S2), [])

    def test_reconnect_blocked_while_active(self):
        self.svc.connect(self.u, K, S); self.svc.activate(self.u)
        with self.assertRaises(AccountError) as cm:
            self.svc.connect(self.u, K, S)
        self.assertEqual(cm.exception.code, "pause_before_reconnect")

    def test_disconnect_purges_and_respects_open_positions(self):
        self.svc.connect(self.u, K, S)
        aid = self.svc.get_me(self.u)["account"]["id"]
        self.rt.open[aid] = 2
        with self.assertRaises(AccountError) as cm:
            self.svc.disconnect(self.u)
        self.assertEqual((cm.exception.code, cm.exception.detail["open_positions"]), ("open_positions", 2))
        out = self.svc.disconnect(self.u, confirm_unmanaged=True)
        self.assertEqual(out["account"]["state"], "DISCONNECTED")
        self.assertIsNone(out["account"]["api_key_masked"])
        with db().service() as c:
            self.assertEqual(c.execute("select count(*) as n from vault.secrets where name like 'yellow:%'").fetchone()["n"], 0)
            self.assertIsNone(c.execute("select credential_ref from public.yellow_accounts").fetchone()["credential_ref"])

    def test_verifier_exception_is_contained_and_pending(self):
        self.fake.mode = "raise"
        out = self.svc.connect(self.u, K, S)
        self.assertEqual(out["account"]["state"], "PENDING_YELLOW_VERIFICATION")
        self.assertIn("verifier_error", out["account"]["verification_reason"])


class TestSizingAndStates(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.rt = RuntimeStub()
        self.svc, self.fake = make_service("ok_scopes", self.rt)
        self.u = new_user()
        self.svc.connect(self.u, K, S)
        self.aid = self.svc.get_me(self.u)["account"]["id"]

    def test_allowed_sizes_only(self):
        for bad in (0, 25, 2500, -20, "abc", None, 20.5):
            with self.assertRaises(AccountError):
                self.svc.set_position_size(self.u, bad)
        for ok in (20, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000):
            self.assertEqual(self.svc.set_position_size(self.u, ok)["config"]["position_size_usd"], ok)

    def test_activate_requires_verified(self):
        me = self.svc.activate(self.u)
        self.assertEqual(me["account"]["state"], "ACTIVE"); self.assertFalse(me["config"]["entries_paused"])
        me = self.svc.pause(self.u)
        self.assertEqual(me["account"]["state"], "PAUSED"); self.assertTrue(me["config"]["entries_paused"])
        with self.assertRaises(AccountError):
            self.svc.pause(self.u)
        self.assertEqual(self.svc.activate(self.u)["account"]["state"], "ACTIVE")

    def test_size_change_without_positions_is_immediate(self):
        self.svc.activate(self.u)
        me = self.svc.set_position_size(self.u, 500)
        self.assertEqual((me["account"]["state"], me["config"]["position_size_usd"], me["config"]["pending_position_size_usd"]), ("ACTIVE", 500, None))

    def test_size_change_with_positions_drains(self):
        self.svc.activate(self.u)
        self.rt.open[self.aid] = 3
        me = self.svc.set_position_size(self.u, 1000)
        self.assertEqual(me["account"]["state"], "DRAINING")
        self.assertEqual(me["config"]["position_size_usd"], 100)          # existing size untouched
        self.assertEqual(me["config"]["pending_position_size_usd"], 1000)
        self.assertTrue(me["config"]["entries_paused"])
        ctxs = self.svc.list_execution_contexts()
        self.assertEqual(len(ctxs), 1)
        self.assertEqual((ctxs[0].state, ctxs[0].position_size_usd, ctxs[0].entries_wanted), ("DRAINING", Decimal(100), False))
        # not flat yet: runner must not complete
        self.assertIsNone(self.svc.complete_drain(self.aid) if False else None)
        # book flat -> runner completes
        self.rt.open[self.aid] = 0
        ctx = self.svc.complete_drain(self.aid)
        self.assertEqual((ctx.state, ctx.position_size_usd, ctx.entries_wanted), ("ACTIVE", Decimal(1000), True))
        me = self.svc.get_me(self.u)
        self.assertEqual((me["config"]["position_size_usd"], me["config"]["pending_position_size_usd"]), (1000, None))
        self.assertIn("size_change_applied", [e["kind"] for e in me["events"]])
        self.assertIsNone(self.svc.complete_drain(self.aid))    # idempotent

    def test_drain_cancel_and_revert(self):
        self.svc.activate(self.u); self.rt.open[self.aid] = 1
        self.svc.set_position_size(self.u, 2000)
        me = self.svc.cancel_size_change(self.u)
        self.assertEqual((me["account"]["state"], me["config"]["pending_position_size_usd"], me["config"]["entries_paused"]), ("ACTIVE", None, False))
        self.svc.set_position_size(self.u, 2000)
        me = self.svc.set_position_size(self.u, 100)     # back to current size cancels the drain
        self.assertEqual((me["account"]["state"], me["config"]["pending_position_size_usd"]), ("ACTIVE", None))

    def test_pause_during_drain_keeps_pending(self):
        self.svc.activate(self.u); self.rt.open[self.aid] = 1
        self.svc.set_position_size(self.u, 750)
        me = self.svc.pause(self.u)
        self.assertEqual((me["account"]["state"], me["config"]["pending_position_size_usd"]), ("PAUSED", 750))
        self.rt.open[self.aid] = 0
        me = self.svc.activate(self.u)   # flat now: pending applied on activation
        self.assertEqual((me["account"]["state"], me["config"]["position_size_usd"], me["config"]["pending_position_size_usd"]), ("ACTIVE", 750, None))

    def test_execution_context_shape(self):
        ctx = self.svc.list_execution_contexts()[0]
        self.assertEqual(ctx.namespace, f"yellow:{self.aid}")
        self.assertEqual(ctx.user_id, self.u.user_id)
        self.assertEqual((ctx.leverage, ctx.max_open_positions), (10, 8))
        lim = ctx.limits()
        self.assertEqual((lim.max_notional_per_order, lim.max_total_notional, lim.max_open_positions), (Decimal('Infinity'), Decimal('Infinity'), 8))
        self.assertNotIn("SECRET", repr(ctx).upper().replace("SECRET_REF", ""))  # context carries a ref, never material
        self.assertNotIn(S, repr(ctx)); self.assertNotIn(K, repr(ctx))


class TestCredentialResolution(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.svc, self.fake = make_service("ok_scopes")
        self.A, self.B = new_user(), new_user()
        self.svc.connect(self.A, K + "A", S + "A"); self.svc.connect(self.B, K + "B", S + "B")
        self.ctxA = next(c for c in self.svc.list_execution_contexts() if c.user_id == self.A.user_id)
        self.ctxB = next(c for c in self.svc.list_execution_contexts() if c.user_id == self.B.user_id)

    def test_resolver_returns_own_credentials_only(self):
        res = VaultCredentialResolver(db())
        cred = res.resolve(self.ctxA)
        self.assertEqual((cred.api_key.reveal(), cred.api_secret.reveal()), (K + "A", S + "A"))
        self.assertNotIn(S, repr(cred))
        # a context that names B's credential_ref but A's user id resolves to nothing
        forged = CustomerExecutionContext(user_id=self.A.user_id, account_id=self.ctxB.account_id, credential_ref=self.ctxB.credential_ref,
                                          position_size_usd=Decimal(100), state="ACTIVE")
        self.assertIsNone(res.resolve(forged))
        forged2 = CustomerExecutionContext(user_id=self.A.user_id, account_id=self.ctxA.account_id, credential_ref=self.ctxB.credential_ref,
                                           position_size_usd=Decimal(100), state="ACTIVE")
        self.assertIsNone(res.resolve(forged2))
        self.assertIsNone(res.resolve(CustomerExecutionContext(self.A.user_id, self.ctxA.account_id, None, Decimal(100), "DISCONNECTED")))

    def test_lazy_client_resolves_only_when_needed_and_forgets(self):
        built = []
        def factory(k, s, asid):
            built.append((k, s, asid))
            class C:
                def __init__(self): self.asid = asid
                def armed(self): return False
            return C()
        lc = LazyYellowClient(VaultCredentialResolver(db()), self.ctxA, client_factory=factory)
        self.assertFalse(lc.resolved); self.assertEqual(built, [])
        self.assertEqual(lc.asid, "0xabc0000000000000000000000000000000000001")
        self.assertEqual(built[0][:2], (K + "A", S + "A"))
        lc.ensure(); self.assertEqual(lc.resolutions, 1)      # cached within ttl
        lc.forget(); self.assertFalse(lc.resolved)
        self.assertNotIn(S, repr(lc))
        # after disconnect the same client can no longer resolve anything
        self.svc.disconnect(self.A)
        from customer.context import CredentialsUnavailable
        with self.assertRaises(CredentialsUnavailable):
            lc.ensure()
