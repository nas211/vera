-- =============================================================================
-- TEST-ONLY shim that makes a plain local Postgres look like a Supabase project
-- so the production migration and RLS can be exercised faithfully:
--   * roles anon / authenticated / service_role (as Supabase creates them)
--   * schema auth with auth.users and auth.uid()/auth.role() (Supabase definitions)
--   * schema vault with the Supabase Vault API surface:
--       vault.create_secret(new_secret, new_name, new_description, new_key_id) -> uuid
--       vault.update_secret(secret_id, new_secret, new_name, new_description, new_key_id)
--       view vault.decrypted_secrets(id, name, description, secret, decrypted_secret, key_id, nonce, created_at, updated_at)
--     backed by pgcrypto symmetric encryption under a database-level key.
-- NEVER applied to production. The real extension is `supabase_vault`.
-- =============================================================================
create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin noinherit bypassrls; end if;
end $$;
grant usage on schema public to anon, authenticated, service_role;
-- the server connection (test role here, `postgres` on Supabase) must be able to SET ROLE
-- into the client roles so RLS can be exercised server-side (customer.db.as_user)
grant anon, authenticated, service_role to current_user;

create schema if not exists auth;
create table if not exists auth.users (
  id uuid primary key,
  email text,
  created_at timestamptz not null default now()
);
-- columns the affiliate dashboard reads on a real project (test-only shim; harmless if present)
alter table auth.users add column if not exists email_confirmed_at timestamptz;
alter table auth.users add column if not exists last_sign_in_at timestamptz;
alter table auth.users add column if not exists raw_user_meta_data jsonb not null default '{}'::jsonb;
create or replace function auth.uid() returns uuid language sql stable as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;
create or replace function auth.role() returns text language sql stable as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.role', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
  )::text
$$;
grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid(), auth.role() to anon, authenticated, service_role;

-- ---------------------------------------------------------------- vault shim
create schema if not exists vault;
create table if not exists vault.secrets (
  id          uuid primary key default gen_random_uuid(),
  name        text unique,
  description text not null default '',
  secret      bytea not null,
  key_id      uuid,
  nonce       bytea,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create or replace function vault._shim_key() returns text language plpgsql stable as $$
declare k text;
begin
  k := current_setting('why.vault_shim_key', true);
  if k is null or k = '' then raise exception 'vault shim key not configured (why.vault_shim_key)'; end if;
  return k;
end $$;
create or replace function vault.create_secret(new_secret text, new_name text default null, new_description text default '', new_key_id uuid default null)
returns uuid language plpgsql as $$
declare sid uuid;
begin
  insert into vault.secrets(name, description, secret, key_id)
  values (new_name, coalesce(new_description, ''), pgp_sym_encrypt(new_secret, vault._shim_key()), new_key_id)
  returning id into sid;
  return sid;
end $$;
create or replace function vault.update_secret(secret_id uuid, new_secret text default null, new_name text default null, new_description text default null, new_key_id uuid default null)
returns void language plpgsql as $$
begin
  update vault.secrets set
    secret = case when new_secret is null then secret else pgp_sym_encrypt(new_secret, vault._shim_key()) end,
    name = coalesce(new_name, name),
    description = coalesce(new_description, description),
    key_id = coalesce(new_key_id, key_id),
    updated_at = now()
  where id = secret_id;
end $$;
create or replace view vault.decrypted_secrets as
  select id, name, description, secret, pgp_sym_decrypt(secret, vault._shim_key()) as decrypted_secret,
         key_id, nonce, created_at, updated_at
  from vault.secrets;
-- like Supabase: vault is NOT exposed to anon/authenticated
revoke all on schema vault from public, anon, authenticated;
grant usage on schema vault to service_role;
grant all on vault.secrets to service_role;
grant select on vault.decrypted_secrets to service_role;
grant execute on function vault.create_secret(text, text, text, uuid), vault.update_secret(uuid, text, text, text, uuid) to service_role;
