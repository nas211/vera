# Backend deployment accepted — 3 November 2026

The existing Vela backend and read-only gateway are deployed and healthy at `https://defivela.xyz`. Release `77ad84168da9e95101696313f4ba86ca650db929` was installed through the guarded deployment tool. The active VPS repository records the identical source tree as `8220665934814d8fe08ab68349770095f32b424d`. The deployed backend fingerprint is `b4b1f2248aa688381a4ecdaa3f51a805cf3f3deabb8fc7560a84ece17de52538`.

This supersedes earlier reports that host access prevented deployment. No live exchange execution or real-money transfer was enabled. The application remains PAPER/DEMO; DogeOS customer transactions remain disabled. ZK/privacy products are excluded from the project by the owner.

## Verified on the VPS and public domain

- Existing candidate and gateway identities, source and idle ledger passed preflight. Fresh WAL-aware backups passed integrity and restoration checks.
- Both existing services stopped cleanly and restarted successfully. The same database was retained, with account/session identity and ledger continuity verified.
- The backend, gateway and public HTTPS endpoint return the expected source fingerprint, fresh market data and healthy execution-loop status. Public WebSocket market streaming passed.
- All nine served frontend files and Nginx routing remained unchanged. All 31 observed WHY services retained their original process IDs and active states.
- The candidate and gateway remain enabled for boot with restart-on-failure. Daily verified backups and periodic health/disk/backup-freshness timers are installed, enabled and have completed successfully. A machine reboot was not performed.
- Unauthorized account/history access returns 401; invalid signatures return 400; cross-origin mutations return 403.
- A disposable QA wallet completed actual signed authentication, secure-cookie session creation, an empty persistent account, a 20-unit simulated allocation, an idempotent retry, AI model preflight/start, stop, simulated withdrawal, logout and reconnection with matching history. The QA account was returned to zero and logged out. No trade was forced and no real funds moved.
- The configured model successfully responded during the real preflight. BTC/ETH/SOL market freshness and current read-only DogeOS chain 6281971 connectivity passed.

This QA test used a programmatically controlled EOA. It does not establish a genuine MetaMask extension/mobile approval or MyDoge approval. Those remain user acceptance checks.

## Test and security evidence

The local backend suite passed 270 tests. [GitHub CI run 41620836655](https://github.com/nas211/vera/actions/runs/41620836655) passed a clean dependency installation, 55 backend tests, dependency consistency checks and the backend-requirements advisory audit. That scanner reported no known vulnerabilities in its resolved backend requirements. It is not an independent security audit and does not clear frontend SDK dependencies or funded financial products.

The machine-readable [acceptance record](../snapshot/deployment-acceptance-20261103.json) distinguishes deployment, fixture tests, live paper checks and unverified real exchange protection. No trading-performance claim is made. The fee-inclusive per-position stop-trigger budget remains $2 or stricter; it is not a guaranteed maximum realized loss.

Private deployment recovery material is retained under `/root/VellaMultichain/.runtime/deployments/20261103T104235Z`, with ongoing private backups under `/root/VellaMultichain/.runtime/backups`. These contain customer data and must not be published. The deployment rollback restores code while retaining the current ledger.

## Remaining boundaries

Real trading, funded lending/staking, contract deployment and money movement retain their security, custody/liquidity and exact owner-authorization gates. MyDoge remains disabled. Legacy Vela services remain separate transient services; this deployment did not restart or migrate them. Genuine device approvals, independent financial-security review and live exchange-side protection have not been verified.

The next user acceptance step is to open `/trading`, connect MetaMask, authenticate and exercise the clearly labeled demo flow with the user's own wallet.
