# tests/customer — Supabase Auth / RLS / Vault / API / tenant suite

Runs against a LOCAL Postgres that the harness dresses up as a Supabase project
(`supabase_local_shim.sql`: roles, `auth.uid()`, Vault API stand-in) and then applies the
real production migration. One-time setup (as postgres):

```bash
sudo -u postgres psql -c "create role why_yellow_test login password 'why_yellow_test_pw' createrole bypassrls;"
sudo -u postgres createdb -O why_yellow_test why_yellow_test
sudo -u postgres psql -d why_yellow_test -c "create extension if not exists pgcrypto;"
sudo -u postgres psql -c "alter database why_yellow_test set why.vault_shim_key = 'local-test-shim-key-not-production';"
```

Run (separate process from tests/execution, whose audit hook denies sockets):

```bash
cd /opt/why-yellow && python3 -m unittest discover -s tests/customer -t . -v
```

Yellow is never contacted: the verifier is `FakeYellow`. `YELLOW_ARMED` must not be set.

## Hosted mode (real Supabase project)

```bash
cd /opt/why-yellow && systemctl stop why-yellow-customer        # never run this while the runner uses the same DB
WY_TEST_HOSTED=1 WY_TEST_DB_URL="$(grep '^SUPABASE_DB_URL=' secrets/customer.env | cut -d= -f2-)" \
  python3 -m unittest discover -s tests/customer -t .
rm -rf state/customer/tenants && systemctl start why-yellow-customer
```

Hosted mode skips the shim (it would overwrite `auth.uid()` and `vault.*`), assumes the migration is
applied, inserts throwaway rows into `auth.users` tagged `why_test_user`, and deletes exactly those
users (and their Vault secrets) at exit. Verified 2026-09-23 against project `iyifnaefqjofkezpknvq`: 49/49.
