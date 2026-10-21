# Existing Vela application integration

Prepared application changes; no integrated code deployment has occurred.
Do not interpret a build or in-process test as a
production deployment, browser verification, wallet-extension approval or live
stop protection. The existing domain is **https://defivela.xyz**; project identity
remains `appgprj_6ad757d1f93c8191bc736d87ada530be`. No new project/service/domain is needed.

## Runtime and preserved state

`vela.api:app` retains the original API routes and attaches the existing paper
routes before the static-file mount. Its lifespan owns exactly one paper runtime,
including Lighter refresh/stream, existing AI loop and existing execution loop.
`vela.lighter.app:create_app` remains a compatibility factory for older tests.
The local launcher now starts `vela.api:app`, too.

`vela-testnet-api.service` stays on `127.0.0.1:8787`. The existing
`vela-testnet-worker.service` and `vela-testnet-tunnel.service` are retained. The
worker remains responsible for its current testnet DB; no operator key is loaded
into the API or Sites. `VELA_DB_PATH` is unchanged. `VELA_PAPER_DB` remains an
independent file beneath `.runtime/lighter-showcase/`; using the same file for
both databases is rejected. Do not migrate, reset, truncate or overwrite either DB.

The homepage, branding, strategy, execution, risk and testnet-worker source are
unchanged by this integration. `/mydoge.html` remains available; the main journey
uses `/trade`. Risk controls retain the fee-inclusive $2 maximum stop-trigger
budget and stricter limits. This is not a guarantee against slippage or funding.

## Confirmed restart constraint

The owner verified that port 8787 still serves the original API (`/trade` is 404),
while the paper process on 8791 serves `/trade` with HTTP 200. The visible transient
API unit has no `VELA_PAPER_DB`, model environment file or proxy secret configured.
Its existing tunnel still targets port 8787.

A read-only ownership check found that
`.runtime/lighter-showcase/competition/paper.sqlite3` has an active engine lock.
The recorded portal PID is outside this executor's process namespace, so the
lock's owner must be confirmed on the host. The API's default
`.runtime/lighter-showcase/paper.sqlite3` does not exist. **A blind restart either
creates a separate empty paper ledger, or fails if pointed at the owned ledger.**
Do not copy the live paper DB into a second engine, remove its lock, or kill the
8791 process to work around this. The existing runtime must release ownership
through a separately agreed controlled handoff before the integrated API can own
the same balances. Until then, leave the API, worker, tunnel and portal running.

Run the read-only gate before any service change:

```sh
.venv/bin/python tools/check_integrated_restart.py \
  --paper-db /root/Vera/.runtime/lighter-showcase/competition/paper.sqlite3
```

The gate does not create/open SQLite databases, mutate secrets, start an engine
or control services. A passing result alone is not deployment approval or proof
of host readiness; the runtime still takes the atomic lock at startup.

`ops/vela-integrated-api.override.conf` is a prepared **additive override**, not
an installed change. It preserves the actual transient unit's ExecStart and other
settings, explicitly selects the existing ledger and requires both environment
files. Before installing it, inspect the effective unit and all existing drop-ins
on the host; preserve any additional settings. Do not replace the transient unit
with the repository service template. Run the gate before stopping/restarting any
service, not merely as an ExecStartPre after the old API has already stopped.

## Existing Sites routing

The existing project's source manifest now selects a Worker build instead of
static-only hosting. `site/worker/index.js` serves compressed copies of the same
built frontend assets and maps `/trade` to `portal.html`, `/earn` to `earn.html`.
The build embeds assets, so it needs no new storage/asset domain or infrastructure.
The original homepage's bytes are preserved. API/health paths and the paper
WebSocket are forwarded through the existing Vela tunnel. Upgrade responses are
returned unchanged; normal API responses are never cached. Upstream redirects
are not followed, preventing the proxy token from leaking to another origin.
The legacy wallet's `backend-config.js` now selects same-origin APIs as well;
its existing endpoint paths and testnet behavior are retained.

The current existing backend origin is
`https://plot-adds-vertical-workforce.trycloudflare.com`. If that existing tunnel
has changed, set server-only `VELA_BACKEND_ORIGIN` to its verified current HTTPS
origin. Never use a new public domain as a workaround. The generated Sites URL
redirects to the canonical `https://defivela.xyz` origin.

## Configuration required before activation

The checked-in `ops/vela-testnet-api.service` is a **template**, not an installed
unit change. Preserve the actual service's other options/environment. Required
API values:

- `VELA_EXECUTION_MODE=PAPER`
- `VELA_CHAIN_WRITES=false` (the existing worker must also retain false)
- `VELA_PORTAL_ORIGIN=https://defivela.xyz`
- Existing separate `VELA_PAPER_DB` selection, if already set.
- Dedicated Vela model settings from the existing mode-600
  `.runtime/lighter-showcase/model.env`; never copy the worker's operator key.
- `VELA_SITE_PROXY_TOKEN`: one random 32–256 character URL-safe server-only secret,
  configured identically in the existing API and existing Sites project's secret
  environment. A mode-600 `.runtime/lighter-showcase/site-proxy.env` is supported
  by the template. Never put the value in source, frontend assets, logs or a URL.

The edge replaces visitor-supplied forwarding headers. The API validates the
shared token and pinned public origin before accepting the public Host, scheme
or client IP. Public paper/portal endpoints fail closed if the token is missing.
Wallet messages bind to the public origin and chain 6281971. Sessions retain
HttpOnly, Secure, SameSite=Strict cookies and persistent hashed server tokens.
Local loopback development uses the launcher's exact HTTP origin without an edge
secret. The launcher is a local convenience, not a replacement production unit.

## Verification and rollout order

Current staging state: the shared token is present in the owner-only backend
file and in this existing Sites project's secret environment revision **1**.
The running API does not load the file and no Sites deployment has activated
that revision. Preserve the matching pair; do not generate a second token for
one side. Model configuration validates locally, with no live model call claimed.

Consistent backups of the visible testnet and competition paper ledgers passed
SQLite integrity checks. They, private unit/model-file copies and the retained
pre-integration source archive are in
`.runtime/deployment-backup-20261021T175510Z/` (directory mode 700, files 600).
Its manifest records checksums. These independent DB snapshots do not establish
the unseen host process environment or a completed restore rehearsal. Preserve
the current ledgers during code rollback; use DB backups only for separately
reviewed recovery, never to rewind transactions after a deployment.

1. Verify the existing API, worker, tunnel health and actual service environment
   from an authorized host console. This sandbox cannot perform that step.
2. Back up the current API code, service configuration/environment securely, and
   both databases using SQLite's backup mechanism. Do not copy a live WAL DB as
   a single file. Keep operator/model/proxy secrets out of deployment artifacts.
3. Build with `npm run build --prefix site`. Run backend, portal, auth, paper,
   risk, edge and local-chain tests, then browser/real-wallet verification.
   `pytest backend/tests --in-process-asgi` uses actual ASGI handlers without
   TestClient's blocked cross-thread selector wakeup. It still requires local
   Anvil for chain tests; it does not certify real networking or MetaMask.
4. Resolve the active paper-ledger ownership handoff without killing the current
   portal, and pass the read-only restart gate. Configure the shared token privately
   and apply the integrated code to the
   **existing API service** only, retaining its port and legacy routes. A controlled
   API restart is required. Do not restart/replace the worker or tunnel or edit Nginx.
5. Verify legacy health, account/ledger reads, new paper health, wallet-signature
   cookies and WebSocket upgrades through the existing connection. Verify PAPER,
   `liveEnabled=false`, `realFundingEnabled=false` and disabled chain writes.
6. Synchronize the exact reviewed frontend source to the existing Sites source
   repository, save the matching artifact, then publish to the same project only
   after the rollback gates below pass. Publishing frontend first would expose
   unavailable portal endpoints. Preserve the current audience, domain and SSL.
7. Verify `/`, `/trade`, `/earn`, original wallet compatibility and the complete
   demo journey on `defivela.xyz`. Distinguish mocked/cryptographic wallet fixtures
   from actual MetaMask approval, and simulated model fixtures from live OpenAI.

## Rollback gates

The provider confirms saved **version 10** remains available, with its retained
archive and successful deployment:

- Version: `appgprj_6ad757d1f93c8191bc736d87ada530be~appgver_b3d1b4071b7c8191b9ff43bc136ca9c2`
- Deployment: `appgdep_6ad7b7673b4c8191a2007ee2983858e4`
- Published source: `795e2a9d305b2fbb59bdd3118c9d611988fab0b3`

Frontend rollback means republishing that saved version to this same project,
preserving domain/SSL/audience. Backend rollback restores the pre-integration
API code and previous environment, then restarts only the same API service.
Keep both current databases intact; rolling code back must not discard demo
transactions or rewind the existing testnet ledger. Keep the new paper ledger
even if the old API no longer exposes it.

Local pre-integration code and checksum evidence are retained under
`.runtime/integration-20261021/`. This is not a replacement for a verified host
backup, current service health or a host restore rehearsal. Do not publish while
the host rollback path, source synchronization or acceptance tests are blocked.
