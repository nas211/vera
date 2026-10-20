-- WHY SaaS layer (2026-09-30): the customer's WHY plan, kept SEPARATE from the Yellow
-- account/connection state in yellow_accounts. Additive; nothing here touches the
-- yellow_* tables, the Vault or the execution core.
--
-- No payment provider is connected yet: a plan chosen on the profile page is recorded
-- as status 'active' with activated_via = 'whitelist'. When billing lands, it will set
-- activated_via = 'billing' and drive status; the shape below does not need to change.
begin;

create table if not exists public.why_subscriptions (
  user_id        uuid primary key references auth.users(id) on delete cascade,
  plan           text not null check (plan in ('core', 'pro', 'prime')),
  billing        text not null default 'monthly' check (billing in ('monthly', 'annual')),
  status         text not null default 'active' check (status in ('active', 'cancelled')),
  activated_via  text not null default 'whitelist' check (activated_via in ('whitelist', 'billing', 'owner')),
  started_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

alter table public.why_subscriptions enable row level security;
revoke all on public.why_subscriptions from public, anon, authenticated;
-- Clients may READ their own row; every write goes through the WHY backend (JWT-derived user).
grant select (user_id, plan, billing, status, activated_via, started_at, updated_at) on public.why_subscriptions to authenticated;
grant all on public.why_subscriptions to service_role;

drop policy if exists why_subscriptions_select_own on public.why_subscriptions;
create policy why_subscriptions_select_own on public.why_subscriptions
  for select to authenticated using (user_id = (select auth.uid()));

commit;
