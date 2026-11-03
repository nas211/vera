# AI decision persistence repair — 2026-11-03

The active paper agent could authenticate and start, but could not persist model decisions. With more than one active account, its unfinished SQLite users cursor retained a WAL snapshot while usage accounting committed through another connection. Subsequent decision and rejection writes failed with SQLITE_BUSY_SNAPSHOT. The old loop retried without preserving a useful account decision.

The repair exhausts the account cursor before model calls. Unexpected model-loop failures now use a 10/20/40/60-second capped retry delay, returning to five seconds after recovery, and log only the exception class. Existing session, permission, market and deterministic risk gates remain enforced. No UI, schema, financial permission, model budget or customer ledger changes were made.

Validation:

- The regression failed on the previous code for both successful and failed provider responses with multiple accounts and actual separate SQLite usage connections.
- All 274 backend tests passed after repair; dependency consistency passed.
- GitHub Actions backend checks and dependency audit passed: https://github.com/nas211/vera/actions/runs/41626187434
- Tests cover model retry recovery, safe logging, health failure detection and deployment acceptance. The new regressions are included in CI.

Deployment and observed operation:

- Source release: 1183c798f1a8eb52c3ff6b161582666ac9c2a96d. Published code: a81dd229e03d2d0e067e7c947b300d72bc55b646.
- Existing Vela backend and gateway were restarted through the guarded deployment tool after a verified, restorable database backup. Only the exact diagnosed model error was permitted during explicit repair preflight; post-deployment acceptance required healthy public HTTPS, API and WebSocket checks.
- Ledger identity and continuity were preserved; the database was never replaced. Existing authentication and Start permission persisted.
- The real configured model completed research, review and direction; all three decisions were saved. One new AI paper order filled through the normal execution pipeline. Its recorded fee-inclusive stop-trigger risk was within the $2 limit. This verifies paper accounting/protection, not exchange-side live stops or a guaranteed realized loss limit.
- Runtime errors cleared. The enabled two-minute health timer passed. Verified daily backups remain enabled. The served UI and all 35 observed WHY service states/PIDs were unchanged.

The machine-readable snapshot records observations at acceptance time, not a perpetual uptime guarantee. Real-money trading and funding remain disabled. No blockchain transaction was submitted by this repair.

Evidence: [acceptance snapshot](../snapshot/ai-persistence-repair-20261103.json).
