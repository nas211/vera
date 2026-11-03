# VELA multichain configuration handoff — 22 October 2026

Prepared code: **PASS**. Targeted tests: **PASS** (68). Production activation:
**BLOCKED**. Live position protection: **NOT TESTED**.

The infrastructure audit is authoritative: existing Vela model credentials,
MyDoge client configuration, Lighter data and legacy DogeOS deployment exist.
Earlier findings that these were absent applied only to the separate multichain
preview environment. No new API key or client registration was requested.

## Implemented handoff

`tools/multichain_handoff.py` provides an independent ASGI factory and a read-only
configuration check:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/multichain_handoff.py --check
```

- Loads `/root/Vera/.runtime/lighter-showcase/model.env` through its existing
  `tools/run_lighter_paper.py` secure loader. It validates file ownership, mode
  0600, non-symlink location and bounded size. Existing permissions remain unchanged.
  The key remains in model memory; it is not copied to a file, process environment,
  command argument, report or repository. Only model name/key/endpoint are accepted.
- Reads only origin, testnet RPC, MyDoge client ID and optional WalletConnect project
  ID from `/root/VeraProtocol/.runtime/preview.env`. The origin must match
  `https://defivela.xyz` and the RPC must match the preserved chain-6281971 registry.
  This verifies configuration compatibility, not the provider's remote registration
  state or a physical wallet approval.
- Allows `ProtocolModel` to accept an explicit secure configuration mapping. An
  explicit empty mapping cannot fall back to inherited credentials. Existing
  callers retain their environment-based behavior.
- Selects `site/candidate` explicitly and verifies its release manifest, HTML and
  all 18 asset hashes before creating the application. The 27 frontend inputs
  are unchanged; no frontend rebuild or design change occurred.
- Uses the existing `LighterFeed` implementation and enables the existing runtime's
  network-read/model tasks. The feed source is identical to the working Vela feed.
  The risk governor, execution engine, fee settings and stop calculations are unchanged.
- Uses `.runtime/lighter-showcase/multichain-candidate.sqlite3`, separate from the
  working public, DogeOS-preview and multichain-preview ledgers. The check does not
  create this database or start tasks. No balances, sessions or history are migrated.
  Any later production data handoff must explicitly account for this separation.
- Keeps PAPER/DEMO execution and no configured testnet factory or attestor. It does
  not import the legacy worker's operator key, proxy token, database, risk overrides
  or chain-write settings.

`ops/vela-multichain-candidate.service.pending` is a separately named, uninstalled
unit for loopback port 8804. It selects the new factory, one worker, a restrictive
umask and read-only filesystem access except this checkout's runtime directory.
It passed `systemd-analyze verify`. It has not been installed, enabled or started.
The 8787 public service and previews on 8802/8803 remain untouched. Port availability
and an actual startup still require host verification. No Nginx handoff was applied.

## Verification

| Category | Status | Evidence / boundary |
|---|---|---|
| Existing secure configuration through new handoff | PASS | Actual files loaded successfully; model/wallet configured; public origins match; both files retain mode 0600 |
| Secure compatibility and integration tests | PASS | 47 tests: unsafe/missing/redirected files, redacted errors, ambient-secret isolation, origin/RPC mismatch rejection, existing-ledger rejection, candidate hashes, seven-network HTTPS sessions and existing API/feed regressions |
| Model response → risk → paper execution → logging | PASS | Uses the actual model parser with deterministic transport responses and fixture credentials; no genuine external model response claimed |
| Fee-inclusive risk regressions | PASS | 21 tests: long/short, partial exits, existing-position reconciliation and stricter permissions across networks |
| Original frontend/contract regressions | PASS | Reused 51 frontend and 21 contract results; those sources are unchanged |
| Existing Lighter runtime observation | PASS | BTC/ETH/SOL rows in the existing paper ledger remained fresh at 19:29:30 UTC, approximately 1.29 seconds old |
| Existing model history | PASS | Dedicated model file exists; public paper DB records successful `gpt-4.1-mini` use at 01:27:55 UTC today; this is historical, not fresh candidate connectivity |
| Direct candidate AI/Lighter integration | BLOCKED | AF_INET socket capability probe returns EPERM; no provider request was attempted after that gate failed |
| Browser/mobile/genuine wallet acceptance | BLOCKED | Existing Chromium socket restriction and no physical wallet approval channel; no repeated browser launch |
| Candidate unit syntax | PASS | Offline systemd verification; no installation or service restart |
| API/worker/systemd live status | BLOCKED | Bus access returns EPERM; host PIDs are unavailable in this executor namespace |
| Public routing/live HTTP/HTTPS/WebSocket health | BLOCKED | Nginx still routes to 8787; socket restriction prevents live probes |
| Preservation | PASS | All 161 captured served/routing files and 27 candidate frontend inputs match saved hashes; candidate ledger still absent |
| Controlled activation readiness | BLOCKED | Host startup/route/rollback, live connectivity and browser acceptance gates remain open |
| Live protection | NOT TESTED | No live stop was changed or verified |

The candidate keeps DogeOS, Solana, Base, Ethereum, BNB Chain, Arbitrum and Robinhood
Chain Testnet. Unsupported financial functions remain demo/simulated. The maximum
stop-trigger budget remains the stricter existing limit or $2 per position for
every account, including fees and inward rounding. Tests cover partial exits and
reconciliation. This is not a guarantee against slippage or funding.

## DogeOS deployment finding

The legacy configuration records a deployed `VelaVault` and operator on chain
6281971. The legacy worker has an operator key and explicitly disables chain writes;
its intentional deposit pause is not evidence of a missing key.

The legacy vault ABI lacks `vaultOf`, `createVault`, `predict`, `settlementSigner`
and `liquidationTreasury`, so its address cannot be used as the new `VaultFactory`.
The new release's deployment manifest has `broadcast=false`, no deployed
`VaultFactory`/`DogeCollateralVault` addresses and no settlement signer binding.
No broadcast records for those contracts were found in the inspected repositories.
RPC access is blocked, so a chain-wide assertion that they do not exist is not made.

This specific operation remains **BLOCKED** pending either verified existing new
contract addresses and the matching authorized attestor, or a separately authorized
testnet deployment. The legacy operator key was not repurposed and no signer was
generated. The rest of the configuration handoff and isolated tests were completed.

## Existing services and rollback

The actual transient API unit loads the existing `model.env` and `site-proxy.env`
and selects `/root/Vera/.runtime/lighter-showcase/competition/paper.sqlite3`.
The worker retains its separate model/operator configuration. Nginx continues
proxying to `http://127.0.0.1:8787`. None of these files or services was changed.

The preserved frontend release remains `.runtime/releases/candidate-f9b20d84ab40`;
its application source commit precedes this backend handoff change. The current
Git commit containing this report identifies the handoff implementation and tests.
No merge or production activation occurred. Original rollback reference:
`/root/VellaMultichain/.runtime/releases/pre-final-20261022T135152Z`.

Evidence: [configuration](../snapshot/configuration-handoff/configuration.json),
[readiness and preservation](../snapshot/configuration-handoff/readiness.json),
[integration tests](../snapshot/configuration-handoff/tests.xml),
[risk tests](../snapshot/configuration-handoff/risk-tests.xml),
[host socket result](../snapshot/configuration-handoff/host-socket.json).
