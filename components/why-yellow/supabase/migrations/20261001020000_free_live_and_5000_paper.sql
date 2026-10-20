-- Free-first onboarding: every account receives 5,000 Paper Credits and may
-- connect one supported LIVE exchange without a subscription.
-- Live entitlement is enforced by the server application; this migration only
-- preserves existing Paper P&L while raising legacy FREE sessions to 5,000.
begin;

with legacy as (
  select a.user_id, a.session_id, (5000::numeric - a.starting_balance) delta
  from public.why_paper_accounts a
  join public.why_subscriptions s on s.user_id = a.user_id
  where s.plan = 'free' and a.starting_balance < 5000
)
update public.why_paper_accounts a
set starting_balance = 5000,
    cash_balance = a.cash_balance + legacy.delta,
    equity = a.equity + legacy.delta,
    peak_equity = a.peak_equity + legacy.delta,
    updated_at = now()
from legacy
where a.user_id = legacy.user_id;

update public.why_paper_sessions s
set starting_balance = 5000
from public.why_paper_accounts a, public.why_subscriptions sub
where s.id = a.session_id
  and sub.user_id = a.user_id
  and sub.plan = 'free'
  and s.starting_balance < 5000;

commit;
