-- =============================================================================
-- Affiliate CRM v2 (2026-09-30): revenue share per partner, partner-scoped logins,
-- password-reset tokens. ADDITIVE, idempotent. Server-only tables (RLS on, no client grants).
-- =============================================================================
begin;

-- WHY receives a share of the trading fees its users generate (Yellow deal: 25 %).
alter table public.affiliate_partners add column if not exists rev_share_pct numeric(5,2) not null default 25.00;

-- Partner logins: role 'partner' sees only its own partner_code (masked emails, no team/partner admin).
alter table public.affiliate_team_users add column if not exists partner_code text references public.affiliate_partners(code) on delete set null;
alter table public.affiliate_team_users drop constraint if exists affiliate_team_users_role_check;
alter table public.affiliate_team_users add constraint affiliate_team_users_role_check check (role in ('admin','member','partner'));

create table if not exists public.affiliate_password_resets (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references public.affiliate_team_users(id) on delete cascade,
  token_hash  text not null unique,           -- sha256(token); the token itself only travels in the email link
  expires_at  timestamptz not null,
  used_at     timestamptz,
  created_at  timestamptz not null default now()
);
alter table public.affiliate_password_resets enable row level security;
revoke all on public.affiliate_password_resets from public, anon, authenticated;
grant all on public.affiliate_password_resets to service_role;
grant usage, select on all sequences in schema public to service_role;

commit;
