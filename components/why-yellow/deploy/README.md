# deploy/ — WHY × Yellow customer platform

| File | Install to |
|---|---|
| `why-yellow-customer.service` | `/etc/systemd/system/` then `systemctl daemon-reload && systemctl enable --now why-yellow-customer` |
| `nginx-yellow-customer.conf` | paste inside the `server { }` block of `/etc/nginx/sites-available/whytradingbot` **above** the existing `location ^~ /yellow/` block, then `nginx -t && systemctl reload nginx` |
| `customer.env.example` | copy to `/opt/why-yellow/secrets/customer.env`, fill in, `chmod 0600` |

Order of operations for the owner:

1. Create the Supabase project; enable Vault (Database → Extensions → `supabase_vault`); enable Email auth.
2. Apply the migrations in timestamp order through `supabase/migrations/20261001000000_public_saas.sql` (additive; see `supabase/README.md`).
3. Run `bash /opt/why-yellow/deploy/configure-customer-env.sh` (prompts without echo, writes `secrets/customer.env` 0600, checks DB + Auth reachability); then `systemctl restart why-yellow-customer`.
4. `curl -s 127.0.0.1:8104/yellow/api/health` → `SUPABASE_HEALTHY` and `VAULT_HEALTHY` must be true.
5. Open https://whytradingbot.com/profile — sign up without payment and verify the Free Paper account.

Paid checkout requires the Stripe variables in `customer.env.example` and a webhook to
`https://whytradingbot.com/yellow/api/billing/webhook`. WEEX intentionally remains
`INTEGRATING` until broker onboarding, production credentials, IP allowlisting, and the
production-order certification pass; only then set `WEEX_LIVE_ENABLED=1`.

Nothing above arms trading. Arming needs `YELLOW_ARMED=1` in the service environment and
`allow_new_entries=true` in `state/exec_switches.json`, both explicit owner actions.
