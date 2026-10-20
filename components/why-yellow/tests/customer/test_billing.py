import hashlib
import hmac
import json
import time
import unittest

from customer.accounts import AccountError
from customer.billing import BillingService
from customer.config import Config
from .harness import db, new_user, reset_db


class Reply:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps({"url": "https://checkout.stripe.com/c/pay_test"}).encode()


class TestBilling(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.user = new_user("billing@example.test")
        self.requests = []
        cfg = Config(stripe_secret_key="sk_test_server", stripe_webhook_secret="whsec_test",
                     stripe_price_core_monthly="pcm", stripe_price_core_annual="pca",
                     stripe_price_pro_monthly="ppm", stripe_price_pro_annual="ppa",
                     stripe_price_prime_monthly="prm", stripe_price_prime_annual="pra")
        self.billing = BillingService(db(), cfg, clock=lambda: 1700000000,
                                      opener=lambda req, timeout: self.requests.append(req) or Reply())

    def test_checkout_preserves_plan_in_server_created_session(self):
        out = self.billing.checkout(self.user, "pro", "annual")
        self.assertTrue(out["checkout_url"].startswith("https://checkout.stripe.com/"))
        body = self.requests[0].data.decode()
        self.assertIn("metadata%5Bplan%5D=pro", body)
        self.assertIn("metadata%5Bbilling%5D=annual", body)
        self.assertNotIn("sk_test_server", body)
        with self.assertRaises(AccountError): self.billing.checkout(self.user, "free", "monthly")

    def test_signed_webhook_activates_once_and_cancellation_updates_plan(self):
        event = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
            "mode": "subscription", "client_reference_id": self.user.user_id,
            "customer": "cus_1", "subscription": "sub_1",
            "metadata": {"user_id": self.user.user_id, "plan": "core", "billing": "monthly"},
        }}}
        body = json.dumps(event, separators=(",", ":")).encode()
        digest = hmac.new(b"whsec_test", b"1700000000." + body, hashlib.sha256).hexdigest()
        self.assertEqual(self.billing.webhook(body, "t=1700000000,v1=" + digest), {"received": True})
        self.assertTrue(self.billing.webhook(body, "t=1700000000,v1=" + digest)["duplicate"])
        with db().service() as c:
            row = c.execute("select plan,status,activated_via from public.why_subscriptions where user_id=%s", (self.user.user_id,)).fetchone()
        self.assertEqual((row["plan"], row["status"], row["activated_via"]), ("core", "active", "billing"))
        deleted = {"id": "evt_2", "type": "customer.subscription.deleted", "data": {"object": {
            "id": "sub_1", "customer": "cus_1", "metadata": {"user_id": self.user.user_id, "plan": "core", "billing": "monthly"},
        }}}
        raw = json.dumps(deleted, separators=(",", ":")).encode()
        sig = hmac.new(b"whsec_test", b"1700000000." + raw, hashlib.sha256).hexdigest()
        self.billing.webhook(raw, "t=1700000000,v1=" + sig)
        with db().service() as c:
            status = c.execute("select status from public.why_subscriptions where user_id=%s", (self.user.user_id,)).fetchone()["status"]
        self.assertEqual(status, "cancelled")


if __name__ == "__main__":
    unittest.main()
