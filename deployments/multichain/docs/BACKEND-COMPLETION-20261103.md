# Vela backend build — 3 November 2026

Backend changes are prepared in `codex/vela-completion-20261103`, in the isolated worktree `/root/vela-completion-20261103/multichain`. The active application checkouts, services, database contents, domain routing, contracts and frontend have not been changed. All 121 tracked UI files match the original newer checkout. This release contains no UI/UX edits.

This is a tested backend release candidate, not a claim that the entire platform is production-ready. Deployment and current provider/wallet acceptance remain blocked. Real trading, real lending, funded staking and ZK privacy have not been activated. No blockchain transaction or real exchange order was submitted. The $2 or stricter fee-inclusive stop-trigger requirement remains separate from realized losses under slippage, funding, gaps or outages. No live protection was changed or verified.

## Authoritative sources and preservation

The original `/root/Vera` is at `ad8f98948de4ddcd93150939a8efe57423bcbd63`. GitHub `nas211/vera` main was observed at `eb7462023558cc4882ad25de62b61c95f9de0d42`; all 733 tracked paths, modes and blob hashes match. The two pre-existing empty untracked files, `curl` and `systemctl`, remain untouched. Issue #1 and the requested legacy deployment/component documentation were inspected.

The newer domain application is `/root/VellaMultichain`, base `dff777b`, branch `feature/vella-multichain`. It has no configured Git remote. Its existing Nginx configuration points to the November 1 static UI at `/var/www/defivela-ui/simple-tabs-20261101T085948Z`, a read-only gateway on 8805, and the newer backend on 8804. These are filesystem configuration observations, not confirmation that the services are currently healthy. The existing Sites project reports active custom-domain/TLS metadata, but its saved version 10 predates the newer VPS delivery history. It was not republished.

Legacy configuration already integrates the paper portal with `vela.api` on 8787. The original 8791 split is not an instruction to create another server. The newer multichain ledger and the legacy ledger remain separate existing products; no balances, sessions or databases were merged. Route compatibility and eventual consolidation require a reachable host and an explicit ledger migration design.

Seventeen reused modules were compared with the legacy source. Sixteen match exactly. The existing multichain adaptation in `backend/vela/paper.py`, introduced in `9cfdd73`, preserves case-sensitive Solana identities. The old preservation script incorrectly rejected it. The repaired verifier checks the original legacy hash and the separately recorded newer adaptation hash; neither module was edited by this release. WHY production source, accounts, credentials and services were not modified.

## Backend changes

- Acquire the existing per-database process lock **before** SQLite initialization. A second engine fails before opening or initializing the ledger. Failed initialization releases ownership.
- Drain model worker threads before releasing database ownership on shutdown. Bound concurrent model workers to two, including canceled requests whose threads still run. Supervise background loops with bounded restart backoff. Add `/api/ready` and task/freshness state to the existing health API.
- Revalidate the wallet session, permission revision and trading generation after model connection checks. A concurrent stop, logout, expiry or permission change cannot be undone by a delayed response. Reject concurrent starts for the same account. Persist the session binding for autonomous entries; protective reductions remain available after entries stop or authentication expires.
- Reconcile tightened wallet/loan stop budgets onto existing positions, preserving the stricter budget across restarts and later permission changes. Regression tests cover long/short positions, inward price rounding, fees and partial exits.
- Persist model usage and reservations in the existing ledger. Defaults are 200 calls per UTC day, a 2,000,000-token reservation budget, 64,000 serialized input bytes and 3,000 maximum output tokens. The existing rolling call limiter remains. Unknown timeout usage retains its reservation. Observed provider counts replace estimates when available. Monetary estimates require explicit prices; no invoice amount is invented. Reservations are a conservative local estimate, not a provider billing guarantee or a quota shared across separate application ledgers.
- Enforce actual request-body limits, including chunked requests, before parsing JSON. Reject malformed objects, non-finite numbers, invalid nonce shapes and oversized length headers. Bound authentication attempts to 120 per minute per actual ASGI peer, with at most 4,096 peer records. Client-provided forwarding headers do not bypass the limit. Behind the existing local proxy this can be an aggregate ceiling; trusted proxy/IP configuration and load acceptance remain host checks.
- Add authenticated, idempotent simulated position reductions and order cancellation. Add bounded cursor pagination for trade, order and transaction history. Persist fresh equity observations at most once per account/session per minute; stale open-position periods remain gaps. These APIs do not change the frontend.
- Add **disabled-by-default demo staking accounting** in the existing ledger. It supports deposit, principal withdrawal, reward accrual and claiming with idempotency and transaction rollback. The illustrative simple annual rate is 5%, backed only by a shared 100,000 simulated-unit accounting pool, with a 10,000-unit principal limit. No token, funded reward source, financial contract or real yield exists. Claims and capital movements are separated from trading P&L. New deposits require stopped trading, no pending exposure and no outstanding loan. Pausing deposits cannot trap principal. Existing production handoff leaves this feature disabled.
- Add a private WAL-aware backup/restore-verification tool, read-only host acceptance checks, and a backend-only release builder with source checksums and code rollback. A pending service drop-in proposes restart-on-failure without changing the existing command, port, database or credentials. Nothing has installed that drop-in.

## Added API surface

Existing cookie, origin, network-selection and authentication behavior remains in use. Every mutation requires the existing same-origin checks. Idempotent mutations require an `Idempotency-Key` header of 8–120 characters.

| Route | Behavior |
| --- | --- |
| `GET /api/ready` | 503 unless the execution loop is healthy, supervised tasks are alive and market data is fresh |
| `GET /api/history/trades` | Wallet/network-scoped, all-time simulated trade records |
| `GET /api/history/orders` | Wallet/network-scoped simulated orders |
| `GET /api/history/transactions` | Wallet/network-scoped loan/capital events |
| `POST /api/trading/positions/{id}/close` | `{"confirm":true,"fraction":"0.5"}` queues a reduce-only simulated order |
| `POST /api/trading/orders/{id}/cancel` | Cancels an owned cancelable order; protective-order rules still apply |
| `GET /api/staking` | Demo-only balances/disclosures; 503 when disabled |
| `POST /api/staking/{deposit,withdraw,claim}` | Confirmed, idempotent demo accounting; 503 when disabled |

History accepts `limit=1..100` and an optional positive `before` cursor. Its counts are all-time; they must not be presented as today's statistics. Closing a position queues execution against fresh simulated depth; the API does not invent an immediate fill. Demo staking can be enabled explicitly through `Config.demo_staking` or `VERA_PROTOCOL_DEMO_STAKING_ENABLED=1` when using `Config.from_env`; the existing handoff constructs its own configuration and keeps it off.

## Executed validation and limits

The machine-readable validation record is `snapshot/backend-validation-20261103.json`. Raw test logs and JUnit reports are under `/root/vela-completion-20261103/`. The final backend suite passed 259 tests. The release-guard suite passed six tests after its final manifest-validation change; five overlap the full suite, for 260 distinct backend tests. Source verification, compile checks and whitespace checks passed. Legacy Python tests passed (140; local-EVM transport tests excluded), legacy execution/risk regression passed (41), original vault Foundry tests passed (26), and newer collateral-vault Foundry tests passed (21), including the available fuzz tests. Installed Python dependency consistency passed. The staged-source pattern scan reported zero findings across 346 files; this is not a comprehensive secrets or vulnerability audit.

The restricted test runner adds an event-loop heartbeat and waits for executor completion because this environment denies the selector's wakeup sockets. It does not replace assertions, bypass failures or simulate live provider success. API tests use an in-process ASGI transport. Market books, models, EVM wallets and chain observations in these tests are fixtures. Existing cached build outputs and installed dependencies were reused. No frontend rebuild or publish is part of this release.

The environment denies systemd bus, process/network inspection and loopback sockets; DNS resolution also fails. Consequently no current HTTPS, DNS, RPC, Lighter, AI provider, browser, genuine wallet approval, service restart/reboot or post-deployment ledger continuity test passed here. Local EVM RPC integration remains unexecuted; Foundry contract tests ran in-process. Automated pattern scanning is limited source inspection, not an independent security audit. Current package advisories and clean online dependency installation remain unverified. The `websockets.legacy` deprecation warning remains; dependencies were not upgraded speculatively.

The original `site/README.md` records 52 SDK/transitive advisories on October 20 (9 low, 21 moderate, 22 high), with a `protobufjs` override addressing the previously reported critical item. These are historical figures for that dependency tree, not a current scan of the newer deployment. The online registry audit failed DNS resolution; an offline zero-advisory response is not accepted as clearance. These unresolved dependency checks continue to block funded activation. No wallet SDK or UI dependency was changed in this backend build.

## Recovery and deployment handoff

Private recovery material is in `/root/vela-completion-20261103/recovery` (0700; files 0600). It contains verified Git bundles, tracked working-tree archives, the preserved served frontend, Vela-only deployment configuration and six consistent SQLite backups. Backup copies passed integrity checks and restoration into disposable databases with schema and table-count comparisons. Production engines were never started to obtain these copies, and no live lock was forcibly removed.

Use `recovery/manifest.json` to select valid backups. **`vela-paper.sqlite3` is an explicitly invalid initial backup attempt and must not be restored.** The valid large ledger backup is `vela-paper-consistent.sqlite3`. Never distribute the recovery directory: configuration and databases are private. The release archives exclude that directory and all frontend/runtime/database/credential files.

The release builder packages only committed backend/tool/test/documentation changes:

```bash
python3 tools/build_backend_release.py build --base dff777b \
  --destination /root/vela-completion-20261103/release
python3 tools/build_backend_release.py verify \
  --directory /root/vela-completion-20261103/release --target /root/VellaMultichain
```

The verifier reads the target and refuses overwritten/newer files or redirected paths. It does not extract, deploy or restart anything. `backend-overlay.tar.gz` contains changed/new files; `backend-rollback.tar.gz` contains the corresponding original files. The manifest identifies added files and their release hashes for conditional removal on code rollback. A mismatch must trigger reconciliation, never `git reset --hard` or a forced overlay.

On a host executor with functioning service and network access, use this sequence:

1. Re-observe the existing candidate/gateway/legacy service PIDs, working directories, open database paths, unit files, health, market freshness and paper-only guards with `tools/check_backend_host.py` and the host's read-only diagnostics. Reconcile any changes since this build. Do not touch WHY processes.
2. Confirm the target base and complete backend/configuration diff, then verify the release against that target. Confirm the unchanged served UI hashes. Take fresh private database backups using `tools/backup_state.py` and verify their manifests. The snapshots from this work are recovery evidence, not a replacement for fresh deployment backups.
3. Schedule the Vela backend restart only after open simulated exposure and market data can be safely managed. Gracefully stop **only** the existing candidate backend; allow its model worker to drain and verify the owner PID has exited. Do not start a second writer or remove a held lock. Leave database files and sidecars in place.
4. Apply only the verified code overlay to the existing source tree. Keep the same database, environment, port, single worker, frontend and Nginx route. Consider the prepared reliability drop-in only after validating the existing unit and graceful-stop behavior. No schema migration or database replacement is required.
5. Start the existing candidate service and check readiness, authentication, history, existing ledger continuity, WebSocket market freshness and restart recovery. Validate the gateway's unchanged pass-through behavior. No real trade or blockchain transfer is part of acceptance.
6. If acceptance fails, gracefully stop that candidate service, restore only files listed in the rollback archive, and remove added release files only when their current hash still matches the release manifest. Restart with the **current ledger retained**. Restoring an older ledger would lose subsequent activity and requires a separate, explicitly reconciled recovery operation. This release's additional key/value records and events are compatible with the original schema.

Database recovery, if ever required for actual corruption, must be a separate operation: verify the selected snapshot, restore to a new private path, run integrity/schema checks, reconcile any activity after its snapshot, then perform a controlled switch with the sole owner stopped. Do not overwrite an active database or copy an uncheckpointed live file with ordinary `cp`.

## Outstanding scope

The inventory records every remaining area rather than treating test fixtures as production acceptance. Host access is required to deploy, install persistent supervision/backups/monitoring and verify current provider connections. Fresh dependency advisories, a clean installation and full CI on the authoritative newer repository are pending; that repository currently has no remote. Real MetaMask/MyDoge approvals require an authorized wallet/device; EIP-1271 is unsupported. MyDoge remains intentionally disabled in the existing product.

Live Lighter execution lacks verified authenticated funding/custody/reconciliation and owner authorization. There is no invented DogeOS-to-Lighter bridge. DogeOS V1/V2 and the newer collateral contracts were not redeployed or funded; historical addresses and chain 6281971 were inspected, but current chain state/fees/receipts could not be queried. V2 cannot be assumed to recover V1 funds. Real lending/staking still require supported liquidity, rewards/oracles, custody and independent review. ZK privacy has no selected verified circuit, proof system, verifier deployment or relayer and is not implemented. None of these financial/privacy products are represented as complete by this backend build.

Reference checks: [DogeOS developer quickstart](https://docs.dogeos.com/en/developers/developer-quickstart), [Lighter funding documentation](https://docs.lighter.xyz/trading/funding), and [OpenAI token counting](https://developers.openai.com/api/docs/guides/token-counting). Provider documentation is not evidence of this deployment's connectivity, costs or execution.
