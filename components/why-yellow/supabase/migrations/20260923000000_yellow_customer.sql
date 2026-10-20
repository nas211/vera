-- =============================================================================
-- WHY × Yellow — customer platform schema (2026-09-23). ADDITIVE ONLY.
--
-- Creates four tables under public.*, RLS policies, column-level grants and two
-- helper functions. It touches no pre-existing object, alters no auth table and
-- deletes nothing, so it is safe on a project that has none of these objects.
-- Re-running is idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS).
--
-- SECRETS: the Yellow API key and API secret are NEVER written to these tables.
-- They live in Supabase Vault (vault.secrets, encrypted at rest, readable only
-- through vault.decrypted_secrets by privileged server-side roles). The only
-- link is public.yellow_credentials.vault_*_id, and that table has NO grant to
-- anon or authenticated, so the browser/PostgREST path cannot even see the
-- reference. Application tables hold only: masked key, SHA-256 fingerprint
-- (for duplicate-binding detection), verification state, timestamps, ownership.
-- =============================================================================
begin;

create extension if not exists pgcrypto;

-- Supabase Vault is pre-installed on hosted projects. Enable it if the platform
-- offers it and it is not yet enabled; otherwise say so and continue (the
-- application reports VAULT_HEALTHY=false until it exists).
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'supabase_vault')
     and not exists (select 1 from pg_extension where extname = 'supabase_vault') then
    begin
      execute 'create extension supabase_vault cascade';
    exception when others then
      raise notice 'supabase_vault could not be enabled here (%); enable it in the Supabase dashboard', sqlerrm;
    end;
  end if;
end $$;

-- ---------------------------------------------------------------- helpers
create or replace function public.yellow_allowed_sizes() returns integer[]
language sql immutable parallel safe as $$
  select array[20, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000]
$$;

create or replace function public.yellow_set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ---------------------------------------------------------------- yellow_accounts
-- One row per WHY user (unique user_id): the Yellow account binding + its state.
create table if not exists public.yellow_accounts (
  id                      uuid primary key default gen_random_uuid(),
  user_id                 uuid not null references auth.users(id) on delete cascade,
  state                   text not null default 'DISCONNECTED'
                          check (state in ('DISCONNECTED','PENDING_YELLOW_VERIFICATION','VERIFIED','ACTIVE','PAUSED','DRAINING','ERROR')),
  state_reason            text,
  api_key_masked          text,             -- e.g. "ed8c••••••••a9c1" — never the full key
  api_key_fingerprint     text,             -- sha256(api_key) hex; not reversible; duplicate-binding guard
  app_session_id          text,             -- Yellow perpetual account id (public wallet address); not a secret
  credential_ref          uuid,             -- -> yellow_credentials.id (opaque; column not granted to clients)
  verification_state      text not null default 'NONE'
                          check (verification_state in ('NONE','PENDING','VERIFIED','FAILED','FORBIDDEN_PERMISSION')),
  verification_reason     text,
  permission_source       text check (permission_source is null or permission_source in ('yellow_api','owner_attestation','unavailable')),
  permission_scopes       text[],
  verified_at             timestamptz,
  last_verify_attempt_at  timestamptz,
  balance_equity          numeric(20,8),
  balance_available       numeric(20,8),
  balance_at              timestamptz,
  connected_at            timestamptz,
  disconnected_at         timestamptz,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  constraint yellow_accounts_one_per_user unique (user_id)
);
create unique index if not exists yellow_accounts_fingerprint_uq
  on public.yellow_accounts (api_key_fingerprint) where api_key_fingerprint is not null;
drop trigger if exists yellow_accounts_updated_at on public.yellow_accounts;
create trigger yellow_accounts_updated_at before update on public.yellow_accounts
  for each row execute function public.yellow_set_updated_at();

-- ---------------------------------------------------------------- yellow_credentials
-- SERVER-ONLY. Holds Vault secret ids, never secret material. No client grants.
create table if not exists public.yellow_credentials (
  id                   uuid primary key default gen_random_uuid(),
  account_id           uuid not null unique references public.yellow_accounts(id) on delete cascade,
  user_id              uuid not null references auth.users(id) on delete cascade,
  vault_api_key_id     uuid not null,
  vault_api_secret_id  uuid not null,
  created_at           timestamptz not null default now(),
  rotated_at           timestamptz
);

-- ---------------------------------------------------------------- yellow_bot_configs
create table if not exists public.yellow_bot_configs (
  account_id                 uuid primary key references public.yellow_accounts(id) on delete cascade,
  user_id                    uuid not null references auth.users(id) on delete cascade,
  position_size_usd          integer not null default 100
                             check (position_size_usd = any (public.yellow_allowed_sizes())),
  pending_position_size_usd  integer
                             check (pending_position_size_usd is null or pending_position_size_usd = any (public.yellow_allowed_sizes())),
  leverage                   integer not null default 10 check (leverage = 10),
  max_open_positions         integer not null default 8 check (max_open_positions = 8),
  entries_paused             boolean not null default true,
  activated_at               timestamptz,
  updated_at                 timestamptz not null default now()
);
drop trigger if exists yellow_bot_configs_updated_at on public.yellow_bot_configs;
create trigger yellow_bot_configs_updated_at before update on public.yellow_bot_configs
  for each row execute function public.yellow_set_updated_at();

-- ---------------------------------------------------------------- yellow_account_events
-- Append-only audit trail (state changes, verification attempts, size changes). No secrets.
create table if not exists public.yellow_account_events (
  id          bigint generated always as identity primary key,
  account_id  uuid not null references public.yellow_accounts(id) on delete cascade,
  user_id     uuid not null,
  kind        text not null,
  detail      jsonb not null default '{}'::jsonb,
  actor       text not null default 'system',
  created_at  timestamptz not null default now()
);
create index if not exists yellow_account_events_account_idx on public.yellow_account_events (account_id, id desc);

-- ---------------------------------------------------------------- RLS
alter table public.yellow_accounts        enable row level security;
alter table public.yellow_credentials     enable row level security;
alter table public.yellow_bot_configs     enable row level security;
alter table public.yellow_account_events  enable row level security;

revoke all on public.yellow_accounts, public.yellow_credentials, public.yellow_bot_configs, public.yellow_account_events
  from public, anon, authenticated;

-- Clients may READ their own non-secret columns. No client may write: every
-- mutation goes through the WHY backend, which derives the user from the JWT.
grant select (id, state, state_reason, api_key_masked, app_session_id, verification_state, verification_reason,
              permission_source, verified_at, last_verify_attempt_at, balance_equity, balance_available, balance_at,
              connected_at, disconnected_at, created_at, updated_at)
  on public.yellow_accounts to authenticated;
grant select (account_id, position_size_usd, pending_position_size_usd, leverage, max_open_positions, entries_paused,
              activated_at, updated_at)
  on public.yellow_bot_configs to authenticated;
grant select (id, account_id, kind, detail, actor, created_at)
  on public.yellow_account_events to authenticated;
-- yellow_credentials: intentionally NO grant to anon/authenticated.

grant all on public.yellow_accounts, public.yellow_credentials, public.yellow_bot_configs, public.yellow_account_events
  to service_role;
grant usage, select on all sequences in schema public to service_role;

drop policy if exists yellow_accounts_select_own on public.yellow_accounts;
create policy yellow_accounts_select_own on public.yellow_accounts
  for select to authenticated using (user_id = (select auth.uid()));

drop policy if exists yellow_bot_configs_select_own on public.yellow_bot_configs;
create policy yellow_bot_configs_select_own on public.yellow_bot_configs
  for select to authenticated using (user_id = (select auth.uid()));

drop policy if exists yellow_account_events_select_own on public.yellow_account_events;
create policy yellow_account_events_select_own on public.yellow_account_events
  for select to authenticated using (user_id = (select auth.uid()));

-- No policies on yellow_credentials => RLS denies everything for non-privileged roles.

-- The backend connects as `postgres` (Supabase direct connection) and runs customer reads
-- under `SET LOCAL ROLE authenticated` + request.jwt.claims so RLS applies server-side too.
-- Supabase already grants these memberships to postgres; this only re-asserts them.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'postgres') then
    begin
      execute 'grant anon, authenticated, service_role to postgres';
    exception when others then
      raise notice 'could not grant client roles to postgres (%); customer.db.as_user needs SET ROLE authenticated', sqlerrm;
    end;
  end if;
end $$;

commit;
