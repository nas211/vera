import base64
import hashlib
import hmac
import json
import unittest

from weex.client import WeexClient, WeexError, broker_client_order_id


class Response:
    ok = True
    status_code = 200
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload


class Session:
    def __init__(self, payload=None): self.payload, self.calls = payload or {"orderId": "42"}, []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.payload)


class TestWeexClient(unittest.TestCase):
    def test_official_v3_signature_and_secret_safe_repr(self):
        msg = "1700000000000GET/capi/v3/account/balance"
        expected = base64.b64encode(hmac.new(b"secret-value", msg.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(WeexClient.sign("secret-value", "1700000000000", "GET", "/capi/v3/account/balance"), expected)
        client = WeexClient("public-key", "secret-value", "passphrase-value")
        rendered = repr(client)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("passphrase-value", rendered)

    def test_broker_attribution_respects_futures_endpoint_limit(self):
        value = broker_client_order_id("WHY123", "unique-order-id", 36)
        self.assertTrue(value.startswith("b-WHY123-"))
        self.assertLessEqual(len(value), 36)
        self.assertEqual(value, broker_client_order_id("WHY123", "unique-order-id", 36))

    def test_order_is_signed_attributed_and_leverage_capped(self):
        session = Session()
        client = WeexClient("api-key", "api-secret", "api-pass", broker_id="WHY123", session=session, clock=lambda: 1700000000)
        client.place_order(symbol="BTCUSDT", side="BUY", position_side="LONG", order_type="MARKET",
                           quantity="0.01", client_id="why-internal-id", leverage=100)
        self.assertEqual(len(session.calls), 2)
        leverage_body = json.loads(session.calls[0][2]["data"])
        order_body = json.loads(session.calls[1][2]["data"])
        self.assertEqual(leverage_body["crossLeverage"], "20")
        self.assertTrue(order_body["newClientOrderId"].startswith("b-WHY123-"))
        self.assertLessEqual(len(order_body["newClientOrderId"]), 36)
        headers = session.calls[1][2]["headers"]
        self.assertEqual(headers["ACCESS-KEY"], "api-key")
        self.assertNotEqual(headers["ACCESS-SIGN"], "api-secret")

    def test_order_transport_failure_is_ambiguous(self):
        class Broken:
            calls = 0
            def request(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1: return Response({"success": True})
                from weex import client as module
                raise module.requests.RequestException("contains no credential")
        client = WeexClient("api-key", "api-secret", "api-pass", broker_id="WHY123", session=Broken())
        with self.assertRaises(WeexError) as raised:
            client.place_order(symbol="BTCUSDT", side="BUY", position_side="LONG", order_type="MARKET",
                               quantity="0.01", client_id="id", leverage=10)
        self.assertTrue(raised.exception.ambiguous)


if __name__ == "__main__":
    unittest.main()
