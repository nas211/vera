-- WHY public SaaS product model: FREE/CORE/PRO/PRIME, per-user Paper ledger,
-- normalized risk preferences, and a second (WEEX) credential boundary.
-- Additive migration. Raw exchange credentials remain in Supabase Vault only.
begin;

-- ---------------------------------------------------------------- subscriptions
-- The first SaaS draft only stored paid plans. Public signup is now free-first.
alter table public.why_subscriptions drop constraint if exists why_subscriptions_plan_check;
alter table public.why_subscriptions drop constraint if exists why_subscriptions_status_check;
alter table public.why_subscriptions drop constraint if exists why_subscriptions_activated_via_check;
alter table public.why_subscriptions alter column plan set default 'free';
alter table public.why_subscriptions alter column billing drop not null;
alter table public.why_subscriptions alter column billing drop default;
alter table public.why_subscriptions alter column activated_via set default 'signup';
update public.why_subscriptions set activated_via='migration' where activated_via='whitelist';
alter table public.why_subscriptions
  add constraint why_subscriptions_plan_check check (plan in ('free','core','pro','prime'));
alter table public.why_subscriptions
  add constraint why_subscriptions_status_check check (status in ('active','past_due','cancelled','expired'));
alter table public.why_subscriptions
  add constraint why_subscriptions_activated_via_check check (activated_via in ('signup','billing','owner','migration'));
alter table public.why_subscriptions add column if not exists stripe_customer_id text;
alter table public.why_subscriptions add column if not exists stripe_subscription_id text;
alter table public.why_subscriptions add column if not exists current_period_end timestamptz;
create unique index if not exists why_subscriptions_stripe_customer_uq
  on public.why_subscriptions(stripe_customer_id) where stripe_customer_id is not null;
create unique index if not exists why_subscriptions_stripe_subscription_uq
  on public.why_subscriptions(stripe_subscription_id) where stripe_subscription_id is not null;
create table if not exists public.why_billing_events (
  event_id       text primary key,
  event_type     text not null,
  processed_at   timestamptz not null default now()
);
revoke all on public.why_billing_events from public, anon, authenticated;
grant all on public.why_billing_events to service_role;

-- Browser grants intentionally omit Stripe identifiers.
revoke all on public.why_subscriptions from public, anon, authenticated;
grant select (user_id, plan, billing, status, activated_via, started_at, updated_at, current_period_end)
  on public.why_subscriptions to authenticated;

-- ---------------------------------------------------------------- Paper accounts/sessions
create table if not exists public.why_paper_sessions (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,
  plan              text not null check (plan in ('free','core','pro','prime')),
  starting_balance  numeric(20,8) not null check (starting_balance > 0),
  ending_equity     numeric(20,8),
  archived_reason   text,
  started_at        timestamptz not null default now(),
  archived_at       timestamptz
);
create index if not exists why_paper_sessions_user_idx on public.why_paper_sessions(user_id, started_at desc);

create table if not exists public.why_paper_accounts (
  user_id              uuid primary key references auth.users(id) on delete cascade,
  session_id            uuid not null references public.why_paper_sessions(id),
  enabled               boolean not null default true,
  running               boolean not null default false,
  strategy              text not null default 'WHY Alpha',
  allocation_pct        numeric(5,2) not null default 25 check (allocation_pct >= 1 and allocation_pct <= 100),
  position_size_pct     numeric(5,2) not null default 5 check (position_size_pct in (1,2,5,10,15,20)),
  leverage              integer not null default 10 check (leverage in (2,5,10,15,20)),
  risk_preset           text not null default 'balanced' check (risk_preset in ('conservative','balanced','aggressive','custom')),
  starting_balance      numeric(20,8) not null,
  cash_balance          numeric(20,8) not null,
  equity                numeric(20,8) not null,
  used_margin           numeric(20,8) not null default 0,
  unrealized_pnl        numeric(20,8) not null default 0,
  realized_pnl          numeric(20,8) not null default 0,
  total_fees            numeric(20,8) not null default 0,
  total_funding         numeric(20,8) not null default 0,
  peak_equity           numeric(20,8) not null,
  max_drawdown          numeric(20,8) not null default 0,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create table if not exists public.why_paper_positions (
  id              uuid primary key default gen_random_uuid(),
  session_id      uuid not null references public.why_paper_sessions(id),
  user_id         uuid not null references auth.users(id) on delete cascade,
  signal_id       text not null,
  market          text not null,
  direction       text not null check (direction in ('long','short')),
  strategy        text not null,
  qty             numeric(30,12) not null check (qty > 0),
  entry_price     numeric(30,12) not null check (entry_price > 0),
  mark_price      numeric(30,12) not null check (mark_price > 0),
  notional        numeric(20,8) not null,
  margin          numeric(20,8) not null,
  leverage        integer not null,
  unrealized_pnl  numeric(20,8) not null default 0,
  state           text not null default 'OPEN' check (state in ('OPEN','CLOSED','RESET_CLOSED')),
  opened_at       timestamptz not null default now(),
  closed_at       timestamptz,
  unique(session_id, signal_id)
);
create unique index if not exists why_paper_one_open_direction_uq
  on public.why_paper_positions(session_id, market, direction) where state = 'OPEN';

create table if not exists public.why_paper_trades (
  id              bigint generated always as identity primary key,
  session_id      uuid not null references public.why_paper_sessions(id),
  user_id         uuid not null references auth.users(id) on delete cascade,
  position_id     uuid not null references public.why_paper_positions(id),
  signal_id       text not null,
  market          text not null,
  direction       text not null,
  strategy        text not null,
  qty             numeric(30,12) not null,
  entry_price     numeric(30,12) not null,
  exit_price      numeric(30,12) not null,
  margin          numeric(20,8) not null,
  notional        numeric(20,8) not null,
  gross_pnl       numeric(20,8) not null,
  fees            numeric(20,8) not null default 0,
  funding         numeric(20,8) not null default 0,
  slippage        numeric(20,8) not null default 0,
  net_pnl         numeric(20,8) not null,
  reason          text,
  opened_at       timestamptz not null,
  closed_at       timestamptz not null default now()
);
create index if not exists why_paper_trades_user_idx on public.why_paper_trades(user_id, id desc);

alter table public.why_paper_sessions enable row level security;
alter table public.why_paper_accounts enable row level security;
alter table public.why_paper_positions enable row level security;
alter table public.why_paper_trades enable row level security;
revoke all on public.why_paper_sessions, public.why_paper_accounts, public.why_paper_positions, public.why_paper_trades
  from public, anon, authenticated;
grant select on public.why_paper_sessions, public.why_paper_accounts, public.why_paper_positions, public.why_paper_trades to authenticated;
grant all on public.why_paper_sessions, public.why_paper_accounts, public.why_paper_positions, public.why_paper_trades to service_role;
grant usage, select on all sequences in schema public to service_role;

drop policy if exists why_paper_sessions_select_own on public.why_paper_sessions;
create policy why_paper_sessions_select_own on public.why_paper_sessions for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists why_paper_accounts_select_own on public.why_paper_accounts;
create policy why_paper_accounts_select_own on public.why_paper_accounts for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists why_paper_positions_select_own on public.why_paper_positions;
create policy why_paper_positions_select_own on public.why_paper_positions for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists why_paper_trades_select_own on public.why_paper_trades;
create policy why_paper_trades_select_own on public.why_paper_trades for select to authenticated using (user_id = (select auth.uid()));

-- ---------------------------------------------------------------- WEEX connection boundary
create table if not exists public.weex_accounts (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null unique references auth.users(id) on delete cascade,
  state                 text not null default 'DISCONNECTED'
                        check (state in ('DISCONNECTED','VERIFIED','ACTIVE','PAUSED','ERROR')),
  state_reason          text,
  api_key_masked        text,
  api_key_fingerprint   text,
  credential_ref        uuid,
  balance_equity        numeric(20,8),
  balance_available     numeric(20,8),
  balance_at            timestamptz,
  connected_at          timestamptz,
  allocation_pct        numeric(5,2) not null default 25 check (allocation_pct >= 1 and allocation_pct <= 100),
  position_size_pct     numeric(5,2) not null default 5 check (position_size_pct in (1,2,5,10,15,20)),
  leverage              integer not null default 10 check (leverage in (2,5,10,15,20)),
  risk_preset           text not null default 'balanced' check (risk_preset in ('conservative','balanced','aggressive','custom')),
  strategy              text not null default 'WHY Alpha',
  entries_paused        boolean not null default true,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create unique index if not exists weex_accounts_fingerprint_uq
  on public.weex_accounts(api_key_fingerprint) where api_key_fingerprint is not null;

create table if not exists public.weex_credentials (
  id                    uuid primary key default gen_random_uuid(),
  account_id            uuid not null unique references public.weex_accounts(id) on delete cascade,
  user_id               uuid not null references auth.users(id) on delete cascade,
  vault_api_key_id      uuid not null,
  vault_api_secret_id   uuid not null,
  vault_passphrase_id   uuid not null,
  created_at            timestamptz not null default now(),
  rotated_at            timestamptz
);

alter table public.weex_accounts enable row level security;
alter table public.weex_credentials enable row level security;
revoke all on public.weex_accounts, public.weex_credentials from public, anon, authenticated;
grant select (id, state, state_reason, api_key_masked, balance_equity, balance_available, balance_at,
              connected_at, allocation_pct, position_size_pct, leverage, risk_preset, strategy,
              entries_paused, created_at, updated_at)
  on public.weex_accounts to authenticated;
grant all on public.weex_accounts, public.weex_credentials to service_role;
drop policy if exists weex_accounts_select_own on public.weex_accounts;
create policy weex_accounts_select_own on public.weex_accounts for select to authenticated using (user_id = (select auth.uid()));
-- No policy/grant on weex_credentials for browser roles.

commit;
