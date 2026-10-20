"""Permission evidence must identify the complete key and its futures scopes."""
import unittest

from customer.yellow_probe import YellowVerifier
from yellow_exec import YellowError

KEY = "12345678abcdef0012345678abcdef00"
SECRET = "fake-secret-for-unit-tests-only"


class Client:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = []

    def _req(self, method, path, params=None, tries=1):
        self.calls.append((method, path))
        if (method, path) == ("GET", "/perpetual/accounts"):
            return [{"app_session_id": "test-account"}]
        if (method, path) == ("GET", "/perpetual/account"):
            return {"total_account_equity": "100", "available_balance": "100"}
        if path == '/accounts/api-key/permissions':
            raise YellowError(404, 'not_found', 'legacy venue')
        if (method, path) == ("GET", "/accounts/api-keys"):
            if isinstance(self.metadata, Exception):
                raise self.metadata
            return self.metadata
        raise AssertionError("unexpected venue call")


class TestYellowPermissions(unittest.TestCase):
    def verify(self, metadata):
        client = Client(metadata)
        result = YellowVerifier(lambda key, secret: client).verify(KEY, SECRET)
        self.assertEqual(client.calls, [("GET", "/perpetual/accounts"),
                                       ("GET", "/perpetual/account"),
                                       ("GET", "/accounts/api-key/permissions"),
                                       ("GET", "/accounts/api-keys")])
        return result

    def test_jwt_only_metadata_keeps_permissions_unverified(self):
        result = self.verify(YellowError(403, "forbidden_auth_method", "JWT required"))
        self.assertTrue(result.credential_ok)
        self.assertFalse(result.permissions_verified)
        self.assertEqual(result.detail["permission_probe"], "http_403:forbidden_auth_method")

    def test_exact_key_with_read_and_trade_is_verified(self):
        result = self.verify({"keys": [{"api_key": KEY, "scopes": ["read:futures", "trade:futures"]}]})
        self.assertTrue(result.permissions_verified)

    def test_partial_masked_other_or_duplicate_key_is_not_evidence(self):
        for keys in ([KEY[:8]], [KEY[:8] + "different"], [KEY + "extra"], [KEY, KEY]):
            with self.subTest(keys=keys):
                result = self.verify([{"api_key": key, "scopes": ["read:futures", "trade:futures"]} for key in keys])
                self.assertFalse(result.permissions_verified)
                self.assertEqual(result.permission_source, "unavailable")

    def test_requires_both_futures_scopes(self):
        for scopes in ([], ["read:futures"], ["trade:futures"], ["read:spot", "trade:spot"],
                       ["unreadable:futures", "trade:futures"]):
            with self.subTest(scopes=scopes):
                result = self.verify([{"api_key": KEY, "scopes": scopes}])
                self.assertTrue(result.missing_required)
                self.assertFalse(result.permissions_verified)

    def test_withdrawal_and_transfer_scopes_are_rejected(self):
        for forbidden in ("withdraw:spot", "transfer:futures"):
            with self.subTest(forbidden=forbidden):
                result = self.verify([{"api_key": KEY, "scopes": ["read:futures", "trade:futures", forbidden]}])
                self.assertTrue(result.forbidden)
                self.assertFalse(result.permissions_verified)

class LivePermissions(unittest.TestCase):
    def verify(self, metadata):
        class LiveClient(Client):
            def _req(self, method, path, params=None, tries=1):
                if path == '/accounts/api-key/permissions':
                    if isinstance(metadata, Exception): raise metadata
                    return metadata
                if path == '/accounts/api-keys':
                    raise AssertionError('must not downgrade to legacy metadata')
                return super()._req(method,path,params,tries)
        return YellowVerifier(lambda key,secret:LiveClient(None)).verify(KEY,SECRET)

    def metadata(self, scopes=None, **changes):
        return dict({'active':True,'app_session_id':'test-account','scopes':scopes if scopes is not None else ['read:futures','trade:futures','transfer:spot-perp']},**changes)

    def test_live_futures_and_internal_transfer_are_accepted(self):
        r=self.verify(self.metadata())
        self.assertTrue(r.permissions_verified)
        self.assertFalse(r.forbidden)

    def test_withdrawal_external_transfer_and_wildcards_rejected(self):
        for scope in ('withdraw:spot','transfer:futures','transfer:external','*','trade:*'):
            r=self.verify(self.metadata(['read:futures','trade:futures',scope]))
            self.assertTrue(r.forbidden)
            self.assertFalse(r.permissions_verified)

    def test_inactive_mismatched_and_malformed_metadata_not_verified(self):
        for meta in (self.metadata(active=False),self.metadata(app_session_id='other'),{'active':True},self.metadata(scopes=[{}])):
            self.assertFalse(self.verify(meta).permissions_verified)

    def test_missing_trade_is_not_verified(self):
        r=self.verify(self.metadata(['read:futures']))
        self.assertTrue(r.missing_required)
        self.assertFalse(r.permissions_verified)

    def test_outage_does_not_use_legacy_metadata(self):
        r=self.verify(YellowError(503,'unavailable','outage'))
        self.assertFalse(r.permissions_verified)
