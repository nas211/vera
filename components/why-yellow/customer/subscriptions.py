"""WHY SaaS subscription (plan + status) — the ONLY writer of public.why_subscriptions.

Kept deliberately separate from the Yellow account state in accounts.py: a subscription
can be active with Yellow not connected, connected, or trading — three independent
states the profile page shows side by side, never collapsed into one.

Live execution is free when a user connects a verified supported exchange. Paid plans
remain optional upgrades for more exchange slots, Paper equity and product features."""
from __future__ import annotations
import time
from typing import Callable, Optional

from .accounts import AccountError
from .auth import Identity

PLANS = {
    "free":  dict(name="WHY Free",  monthly_usd=0,   annual_usd=0,    exchange_limit=1, paper_starting_balance=25000),
    "core":  dict(name="WHY Core",  monthly_usd=49,  annual_usd=490,  exchange_limit=2, paper_starting_balance=25000),
    "pro":   dict(name="WHY Pro",   monthly_usd=129, annual_usd=1290, exchange_limit=3, paper_starting_balance=25000),
    "prime": dict(name="WHY Prime", monthly_usd=299, annual_usd=2990, exchange_limit=None, paper_starting_balance=25000),   # unlimited
}
BILLING = ("monthly", "annual")
COLS = ("user_id", "plan", "billing", "status", "activated_via", "started_at", "updated_at", "current_period_end")


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def plan_catalog() -> list:
    return [dict(id=k, **v) for k, v in PLANS.items()]


class SubscriptionService:
    def __init__(self, db, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock

    @staticmethod
    def _public(row) -> Optional[dict]:
        if row is None:
            return None
        p = PLANS[row["plan"]]
        return dict(plan=row["plan"], plan_name=p["name"], billing=row["billing"], status=row["status"],
                    activated_via=row["activated_via"], exchange_limit=p["exchange_limit"],
                    paper_starting_balance=p["paper_starting_balance"], live_allowed=True,
                    current_period_end=_iso(row.get("current_period_end")),
                    started_at=_iso(row["started_at"]), updated_at=_iso(row["updated_at"]))

    def ensure(self, ident: Identity) -> dict:
        """First authenticated request creates a no-payment FREE account."""
        with self.db.service() as c:
            c.execute("""insert into public.why_subscriptions(user_id, plan, billing, status, activated_via)
                         values (%s, 'free', null, 'active', 'signup') on conflict (user_id) do nothing""",
                      (ident.user_id,))
        return self.get(ident)

    def get(self, ident: Identity) -> dict:
        with self.db.as_user(ident.user_id, ident.email) as c:
            row = c.execute(f"select {', '.join(COLS)} from public.why_subscriptions").fetchone()
        return dict(subscription=self._public(row), plans=plan_catalog())

    def choose(self, ident: Identity, plan, billing="monthly", *, activated_via="owner") -> dict:
        """Owner/billing-only mutation. The public API must use checkout, never this method."""
        plan = str(plan or "").strip().lower()
        billing = str(billing or "monthly").strip().lower()
        if plan not in PLANS or plan == "free":
            raise AccountError("plan_not_allowed", 422, {"allowed": sorted(PLANS)})
        if billing not in BILLING:
            raise AccountError("billing_not_allowed", 422, {"allowed": list(BILLING)})
        with self.db.service() as c:
            c.execute("""insert into public.why_subscriptions(user_id, plan, billing, status, activated_via)
                         values (%s, %s, %s, 'active', %s)
                         on conflict (user_id) do update set plan = excluded.plan, billing = excluded.billing,
                             status = 'active', activated_via = excluded.activated_via, updated_at = now()""",
                      (ident.user_id, plan, billing, activated_via))
        return self.get(ident)

    def cancel(self, ident: Identity) -> dict:
        with self.db.service() as c:
            c.execute("update public.why_subscriptions set status = 'cancelled', updated_at = now() where user_id = %s", (ident.user_id,))
        return self.get(ident)

    def entitlement(self, user_id: str) -> dict:
        with self.db.service() as c:
            row = c.execute(f"select {', '.join(COLS)} from public.why_subscriptions where user_id = %s", (user_id,)).fetchone()
        if row is None:
            return dict(plan="free", status="active", live_allowed=True, exchange_limit=1, paper_starting_balance=25000)
        p = PLANS[row["plan"]]
        return dict(plan=row["plan"], status=row["status"], live_allowed=True,
                    exchange_limit=p["exchange_limit"], paper_starting_balance=p["paper_starting_balance"])
