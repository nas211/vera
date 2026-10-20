# customer/ — WHY × Yellow live customer platform (2026-09-23)

Connects the audited execution core (`execution/`, unchanged) to real customers:

```
Supabase Auth (browser, anon key)            WHY backend (this package, loopback :8104)
  signUp / signInWithPassword  ──JWT──▶  auth.Verifier: signature (JWKS or HS256), iss, aud, exp, role
                                         identity = JWT.sub ONLY (client identity fields => 400)
  Connect Yellow (key + secret) ─HTTPS─▶  accounts.connect: validate → Vault.create ×2 → yellow_credentials row
                                         → yellow_accounts {masked key, sha256 fingerprint, PENDING_YELLOW_VERIFICATION}
                                         → yellow_probe (READ ONLY: accounts, balance, scopes if exposed)
  ◀── success/failure + masked id only    → VERIFIED | PENDING_YELLOW_VERIFICATION | DISCONNECTED(rejected)
                                         tenants.TenantRegistry: one Ledger/Engine/VenueMonitor/transport per account
                                         context.LazyYellowClient: Vault → YellowClient only when a venue call needs it
                                         fanout.JournalSignalSource: paper book OPEN → CanonicalSignal → every ACTIVE tenant
```

| Module | Role |
|---|---|
| `config.py` | env → `Config`; refuses `SUPABASE_SERVICE_ROLE_KEY`; `public_view()` is all the browser gets |
| `log.py` | redacting logger: registered secrets, Bearer/JWT/API-key patterns, DSN passwords, sanitized exceptions |
| `auth.py` | Supabase JWT verification (ES256/RS256 via JWKS, HS256 legacy); `Identity(user_id)` |
| `db.py` | psycopg pool; `service()` (privileged) and `as_user(uid)` (`SET LOCAL ROLE authenticated` + claims ⇒ RLS) |
| `vault.py` | `vault.create_secret / update_secret / decrypted_secrets / delete`; `SecretBox` opaque holder |
| `yellow_probe.py` | credential verification, read-only; forbidden scope markers `withdraw`, `transfer` |
| `accounts.py` | the only writer of `yellow_*` tables + Vault; account state machine; sizing transitions; owner attestation |
| `context.py` | `CustomerExecutionContext` (no secrets), `VaultCredentialResolver`, `LazyYellowClient`, `TenantYellowTransport` |
| `tenants.py` | `TenantEngine` (isolated ledger per account) + `TenantRegistry` (fan-out, drains, per-tenant error isolation) |
| `fanout.py` | tails `state/journal.jsonl`; deterministic `signal_id`; skips history and stale signals |
| `feed.py` | public Yellow websocket → shared `FreshnessMonitor` + software stops for every tenant |
| `readiness.py` | APP / SUPABASE / VAULT / YELLOW_CONNECTED / YELLOW_VERIFIED / EXECUTION_READY |
| `api.py` | JSON API; `service.py` entry point; `admin.py` owner CLI |

## Account states

`DISCONNECTED → PENDING_YELLOW_VERIFICATION → VERIFIED → ACTIVE ⇄ PAUSED`, `ACTIVE → DRAINING → ACTIVE`
(size change with open positions: entries paused, positions managed to completion, then the new size
applies), `ERROR` (definitive credential failure while live; entries paused, credential purged).

**VERIFIED requires a real Yellow check.** Today Yellow answers `403 forbidden_auth_method` when an
API key asks for its own scopes (audit Q-02), so a working credential lands in
`PENDING_YELLOW_VERIFICATION / permission_metadata_unavailable` and cannot be activated. Two ways out,
neither automatic: Yellow exposes a key-info endpoint (the probe already reads `scopes` from
`GET /accounts/api-keys` if it ever answers), or the owner records an out-of-band check with
`python3 -m customer.admin attest <account_id> --scopes read:futures,trade:futures --evidence "..."`.
A scope containing `withdraw` or `transfer` is rejected on every path; there is no override.

## Sizing

Live entries use 10× leverage and each coin's smallest valid Yellow lot. The
exchange's current available balance must cover the required margin for each
entry. At most eight positions can be open. The old stored position size is
retained for legacy account records but does not cap orders or total exposure;
the position-size change endpoint is disabled. Existing positions are managed
without resizing them.

Old private `size_overrides.json` files are ignored. Exchange minimums are read from
current Yellow instrument metadata for every account.

## Idempotency / namespacing

Engine account id = `yellow:<account_id>`; `client_order_id = why-sha256(account|signal|purpose|market|direction)[:28]`,
so the same canonical signal yields one distinct, deterministic order per tenant and a replay is a
no-op everywhere. Each tenant's SQLite ledger lives in `state/customer/tenants/<account_id>/`.

## What can trade

Only when ALL hold: `YELLOW_ARMED=1` in the service environment, global `state/exec_switches.json`
`allow_new_entries`, account `ACTIVE` with `verification_state=VERIFIED`, venue `READY` after a
successful reconciliation, fresh market data. None of these is set by installation.

## Tests

```
python3 -m unittest discover -s tests/execution -t .     # core + tenant fan-out, socket-denying hook
python3 -m unittest discover -s tests/customer  -t .     # Auth/RLS/Vault/API on local Postgres (see tests/customer/README.md)
```

## Live Yellow probe, 2026-09-23 (read-only, owner key from `secrets/api_key.json`)

| Call | Result |
|---|---|
| public `GET /perpetual/exchangeInfo`, `POST /auth/challenge` | 200 — Yellow ONLINE |
| `GET /perpetual/accounts`, `/perpetual/account?app_session_id`, `/perpetual/positions` (HMAC) | **403 access_denied** on all — the key minted 2026-09-13 no longer works (worked at mint time; same whitelisted IP) |
| `GET /accounts/api-keys` (HMAC) | 403 `forbidden_auth_method` "API keys can only be managed with JWT authentication" — scopes are NOT exposed to API-key auth |

Consequences: the owner must re-mint a key (`yellow_exec.py setup --i-mean-it`, owner "go" only) before any
account-read test; and every customer credential will sit in `PENDING_YELLOW_VERIFICATION /
permission_metadata_unavailable` until Yellow provides a key-info endpoint or the owner attests.
Real Yellow orders placed by this phase: 0.

## Partner attribution + affiliate CRM (2026-09-30)

Owner reverted the Yellow "AI agent" pairing pivot (parked on branch `mcp-pairing-parked`);
the customer flow is again: WHY sign-up/login (Supabase) → paste a trade-only Yellow API key +
secret → verify → activate. New: a partner link `/r/<code>` (nginx → `/yellow/app/?ref=<code>`)
is kept by the web app (localStorage, first touch) and sent as Supabase signUp user metadata;
`Identity.partner` reads it from the JWT and `_ensure` writes it once to
`yellow_accounts.partner_code`. The team dashboard in `affiliate/` (why-affiliate.service,
:8109, affiliate.whytradingbot.com / whytradingbot.com/affiliate/) counts signups, Yellow
connections and activations per partner. Migration `supabase/migrations/20260930000000_affiliate.sql`
(additive); deploy `deploy/affiliate_deploy.sh`; tests `tests/affiliate/` (14).
