# supabase/ — WHY × Yellow customer platform

`migrations/20260923000000_yellow_customer.sql` is the only migration. It is **additive**:
four new `public.yellow_*` tables, RLS, column grants, two helper functions. It creates
nothing in `auth.*`, drops nothing, and is idempotent.

## Apply (owner)

No Supabase project exists on this host yet. Once the owner has created one:

```bash
# direct connection string from Supabase → Project Settings → Database (session mode)
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/20260923000000_yellow_customer.sql
```

or `supabase db push` from a linked CLI checkout. Confirm Vault is enabled
(Database → Extensions → `supabase_vault`).

## Secrets model

| Where | What |
|---|---|
| `vault.secrets` (Supabase Vault) | Yellow API key, Yellow API secret — encrypted at rest, server-side only |
| `public.yellow_credentials` | Vault secret ids only; **no grant** to `anon`/`authenticated` |
| `public.yellow_accounts` | masked key, SHA-256 fingerprint, state, verification, balances, timestamps |
| `public.yellow_bot_configs` | position size, pending size, leverage 10, max open 8, entries paused |
| `public.yellow_account_events` | audit log (no secrets) |

Clients (browser / PostgREST with the anon key + user JWT) can only `select` their own
non-secret columns. All writes go through the WHY backend, which derives the user from the
verified JWT (`customer/auth.py`) and talks to Postgres directly over `SUPABASE_DB_URL`.
The service-role key is not used anywhere.
