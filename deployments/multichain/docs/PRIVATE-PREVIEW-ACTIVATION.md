# VELLA private modular activation

The application remains the DogeOS build at `f45b4c0`. The activation tooling uses the existing `ops/preview.json`, dedicated preview environment, TLS certificate, database, Uvicorn application and loopback port. It does not create a service, change public routing, deploy contracts, enable real orders or lending, or write to `/root/Vera`.

Run this exact command in the authorized Contabo terminal:

```sh
cd /root/VeraProtocol && .venv/bin/python tools/activate_preview.py --activate-modular
```

The command requires `main` and verifies that the application still matches `f45b4c0`; a multichain checkout or modified application is rejected. It checks all 57 modular release hashes, preserves the previous source and frontend, copies the dedicated configuration privately, and creates a coherent SQLite backup including WAL transactions. It tests modular initialization and previous-version reopening on a disposable copy. No migration is currently necessary. Account data and schema must remain identical.

Before stopping anything, the command requires a successful private TLS safety check, the exact previous homepage bytes, matching process identity and ownership of port 8802. An inaccessible control plane aborts without signalling the process. The existing TLS hostname remains `defivela.xyz`; the actual connection goes to `127.0.0.1:8802`, with certificate verification enabled. Public DNS is not used for this check.

After those checks, the explicit activation operation gracefully stops the recorded process through a PID-bound descriptor. A 20-second shutdown deadline is followed by termination of that same process if necessary. It waits for exit, captures the final database state, and calls the existing launcher. Verification requires exact modular HTML on both routes, every prepared asset, modular API version 1, DogeOS testnet chain 6281971, PAPER/DEMO restrictions, unauthenticated account rejection, and the public market WebSocket. It reports Lighter freshness separately from serving status.

If the new child fails verification, the launcher stops only that child and attempts to restart the preserved previous backend through the same launcher. It then restores the prepared source on disk, as it was before activation. The latest database is retained: rollback never restores an older balance or repayment ledger. Rollback success or failure is explicitly recorded. Process lifecycle and rollback tests use fixtures; an actual host rollback has not been exercised.

Backups and detailed results are private under `.runtime/private-preview/backups/`. The latest attempted activation result is `.runtime/private-preview/modular-activation.json`. Adding `--check` performs preservation and database compatibility checks without sockets or process changes.

## Verified activation result

After execution in the authorized Contabo terminal on 2026-10-22, the modular build is **running and verified on 127.0.0.1:8802**, under PID **1513295**. A subsequent independent HTTPS check matched the exact prepared homepage and every prepared asset, confirmed modular API version 1 and DogeOS testnet chain 6281971, confirmed PAPER/DEMO enforcement and unauthenticated account rejection, and passed the public WebSocket check. Lighter returned fresh BTC/USD, ETH/USD and SOL/USD data. Public routing remains unchanged.

An actual Chromium check of the private preview passed at 390 and 1440 pixels: short homepage, all five destination links, Launch VELLA, MyDoge wallet-first onboarding, no horizontal overflow, and no persistent simulation banner on those public screens. There were no page errors. This check used verified TLS and the existing private listener, without API interception, a wallet mock, session creation or financial actions. The authenticated repayment, automation and tutorial journeys retain their passing local-fixture results; genuine wallet approval and chain transactions remain unverified.

The successful activation backup is `.runtime/private-preview/backups/20261022T104353Z-54ea6007`. Its database compatibility checks passed without a migration or ledger rewind. See [the updated activation report](../snapshot/modular-activation-report.json) and [the actual browser results](../snapshot/activated-private-browser.json).

## Earlier preparation and local regression evidence

The earlier Codex activation attempt stopped before restart because the private TLS health request timed out. At that point PID **1326894** still owned the listener and no termination signal had been sent. This earlier blocked attempt was superseded by the verified host activation described above.

The complete backup for that attempt is `.runtime/private-preview/backups/20261022T100605Z-a9fb3d2c`. Database integrity and compatibility passed: 23 existing tables, no schema or data changes, and successful previous-initializer reopening. The current database has no wallet accounts or loans; populated financial-ledger compatibility was tested with synthetic accounts separately.

Final local checks passed: **118 distinct backend tests**, **43 frontend unit tests**, frontend rendering/build-preservation checks, **21 contract tests**, and Chromium fixture journeys at 390 and 1440 pixels. The backend total combines the 94-case existing regression run with the 40-case activation run; 16 cases occur in both. Final failures: **0**. Two initial activation-copy tests caught a directory-isolation error in the new helper; it was corrected before the activation attempt, without changing the engine's isolation guard.

The local tests cover the short homepage, five application sections, partial repayment, Profit Sweep, schedules, tutorials, wallet-first onboarding, notices and transactional disclosures. They do not establish genuine MyDoge signatures or DogeOS receipts. Serving and Lighter checks were subsequently verified separately, as described above. The maximum $2 fee-inclusive stop-trigger limit and stricter existing limits passed the existing long/short, partial-exit and reconciliation tests. No live position protection is claimed; stop triggers do not guarantee losses against slippage or funding.

The preview configuration still lacks the dedicated AI model, DogeOS factory and demo attestor. MyDoge has a configured client ID, but its registered origin and genuine wallet approval remain unverified. Those settings and a genuine testnet lifecycle remain competition blockers after host activation.

The machine-readable evidence is [modular-activation-report.json](../snapshot/modular-activation-report.json). No further restart is needed for this activation.
