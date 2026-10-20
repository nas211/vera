-- =============================================================================
-- WHY affiliate / partner CRM (2026-09-30). ADDITIVE ONLY, idempotent.
--
-- Attribution: a partner link (/r/<code> or /yellow/app/?ref=<code>) puts the partner code into
-- the Supabase signUp user metadata (auth.users.raw_user_meta_data->>'partner'); the customer
-- backend copies it once (first touch) into public.yellow_accounts.partner_code when the account
-- row is created. The team dashboard (affiliate/, affiliate.whytradingbot.com) joins auth.users
-- with yellow_accounts server-side. Nothing here is granted to anon/authenticated: RLS is on
-- with no policies, so the browser/PostgREST path cannot read any of it.
-- =============================================================================
begin;

alter table public.yellow_accounts add column if not exists partner_code text;
create index if not exists yellow_accounts_partner_idx on public.yellow_accounts (partner_code);

-- ---------------------------------------------------------------- partners
create table if not exists public.affiliate_partners (
  code        text primary key check (code ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
  name        text not null,
  active      boolean not null default true,
  notes       text,
  created_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
drop trigger if exists affiliate_partners_updated_at on public.affiliate_partners;
create trigger affiliate_partners_updated_at before update on public.affiliate_partners
  for each row execute function public.yellow_set_updated_at();

-- ---------------------------------------------------------------- team users (dashboard login)
create table if not exists public.affiliate_team_users (
  id             uuid primary key default gen_random_uuid(),
  email          text not null,
  name           text,
  role           text not null default 'member' check (role in ('admin','member')),
  password_hash  text not null,              -- pbkdf2_sha256$iterations$salt$hash (affiliate.auth)
  active         boolean not null default true,
  created_at     timestamptz not null default now(),
  last_login_at  timestamptz
);
create unique index if not exists affiliate_team_users_email_uq on public.affiliate_team_users (lower(email));

-- ---------------------------------------------------------------- audit (who did what in the CRM)
create table if not exists public.affiliate_audit (
  id          bigint generated always as identity primary key,
  actor       text not null,
  action      text not null,
  detail      jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------- lock-down
alter table public.affiliate_partners    enable row level security;
alter table public.affiliate_team_users  enable row level security;
alter table public.affiliate_audit       enable row level security;
revoke all on public.affiliate_partners, public.affiliate_team_users, public.affiliate_audit
  from public, anon, authenticated;
grant all on public.affiliate_partners, public.affiliate_team_users, public.affiliate_audit to service_role;
grant usage, select on all sequences in schema public to service_role;

insert into public.affiliate_partners (code, name, notes, created_by)
  values ('yellow', 'Yellow', 'yellow.pro listing / partner traffic', 'migration')
  on conflict (code) do nothing;

commit;
