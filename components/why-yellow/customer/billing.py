"""Stripe Checkout boundary for WHY subscriptions.

Paid access is activated only by a verified Stripe webhook (or the owner CLI), never
by a browser-selected plan. The browser receives only the hosted Checkout URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .accounts import AccountError
from .subscriptions import BILLING, PLANS


class BillingService:
    API = "https://api.stripe.com/v1"

    def __init__(self, db, cfg, clock=time.time, opener=urllib.request.urlopen):
        self.db, self.cfg, self.clock, self.opener = db, cfg, clock, opener

    def _price(self, plan: str, billing: str) -> str:
        return str(getattr(self.cfg, f"stripe_price_{plan}_{billing}", "") or "")

    def checkout(self, ident, plan, billing="monthly") -> dict:
        plan, billing = str(plan or "").lower(), str(billing or "monthly").lower()
        if plan not in PLANS or plan == "free":
            raise AccountError("plan_not_allowed", 422)
        if billing not in BILLING:
            raise AccountError("billing_not_allowed", 422)
        price = self._price(plan, billing)
        if not self.cfg.billing_configured or not price:
            raise AccountError("billing_not_configured", 503)
        form = urllib.parse.urlencode({
            "mode": "subscription",
            "line_items[0][price]": price,
            "line_items[0][quantity]": "1",
            "client_reference_id": ident.user_id,
            "customer_email": ident.email or "",
            "metadata[user_id]": ident.user_id,
            "metadata[plan]": plan,
            "metadata[billing]": billing,
            "subscription_data[metadata][user_id]": ident.user_id,
            "subscription_data[metadata][plan]": plan,
            "subscription_data[metadata][billing]": billing,
            "success_url": self.cfg.site_url + "/profile?checkout=success",
            "cancel_url": self.cfg.site_url + f"/profile?plan={plan}&billing={billing}&checkout=cancelled",
            "allow_promotion_codes": "true",
        }).encode()
        req = urllib.request.Request(self.API + "/checkout/sessions", data=form, method="POST", headers={
            "Authorization": "Bearer " + self.cfg.stripe_secret_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "Idempotency-Key": f"why-{ident.user_id}-{plan}-{billing}-{int(self.clock() // 300)}",
        })
        try:
            with self.opener(req, timeout=20) as r:
                out = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise AccountError("checkout_unavailable", 502, {"provider_status": e.code}) from e
        except Exception as e:
            raise AccountError("checkout_unavailable", 502) from e
        url = out.get("url")
        if not isinstance(url, str) or not url.startswith("https://checkout.stripe.com/"):
            raise AccountError("checkout_unavailable", 502)
        return {"checkout_url": url}

    def verify_webhook(self, body: bytes, signature: str) -> dict:
        if not self.cfg.stripe_webhook_secret:
            raise AccountError("billing_not_configured", 503)
        fields = {}
        for item in str(signature or "").split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                fields.setdefault(k, []).append(v)
        try:
            ts = int(fields["t"][0])
        except Exception as e:
            raise AccountError("invalid_webhook_signature", 400) from e
        if abs(self.clock() - ts) > 300:
            raise AccountError("invalid_webhook_signature", 400)
        signed = str(ts).encode() + b"." + body
        expected = hmac.new(self.cfg.stripe_webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, v) for v in fields.get("v1", [])):
            raise AccountError("invalid_webhook_signature", 400)
        try:
            event = json.loads(body.decode())
        except Exception as e:
            raise AccountError("bad_request", 400) from e
        if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
            raise AccountError("bad_request", 400)
        return event

    def webhook(self, body: bytes, signature: str) -> dict:
        event = self.verify_webhook(body, signature)
        eid, typ = str(event["id"]), str(event["type"])
        obj = ((event.get("data") or {}).get("object") or {})
        with self.db.service() as c:
            if c.execute("select 1 from public.why_billing_events where event_id=%s", (eid,)).fetchone():
                return {"received": True, "duplicate": True}
            if typ == "checkout.session.completed" and obj.get("mode") == "subscription":
                meta = obj.get("metadata") or {}
                self._activate(c, meta.get("user_id") or obj.get("client_reference_id"), meta.get("plan"), meta.get("billing"),
                               obj.get("customer"), obj.get("subscription"), "active", None)
            elif typ in ("customer.subscription.updated", "customer.subscription.deleted"):
                meta = obj.get("metadata") or {}
                raw_status = str(obj.get("status") or "")
                status = "cancelled" if typ.endswith("deleted") or raw_status in ("canceled", "unpaid", "incomplete_expired") else (
                    "past_due" if raw_status in ("past_due", "incomplete") else "active")
                self._activate(c, meta.get("user_id"), meta.get("plan"), meta.get("billing"), obj.get("customer"), obj.get("id"),
                               status, obj.get("current_period_end"))
            c.execute("insert into public.why_billing_events(event_id,event_type) values (%s,%s)", (eid, typ))
        return {"received": True}

    @staticmethod
    def _activate(c, user_id, plan, billing, customer, subscription, status, period_end):
        if not user_id or plan not in PLANS or plan == "free" or billing not in BILLING:
            return
        c.execute("""insert into public.why_subscriptions
                     (user_id,plan,billing,status,activated_via,stripe_customer_id,stripe_subscription_id,current_period_end)
                     values (%s,%s,%s,%s,'billing',%s,%s,to_timestamp(%s))
                     on conflict(user_id) do update set plan=excluded.plan,billing=excluded.billing,status=excluded.status,
                       activated_via='billing',stripe_customer_id=excluded.stripe_customer_id,
                       stripe_subscription_id=excluded.stripe_subscription_id,current_period_end=excluded.current_period_end,updated_at=now()""",
                  (user_id, plan, billing, status, customer, subscription, period_end))
