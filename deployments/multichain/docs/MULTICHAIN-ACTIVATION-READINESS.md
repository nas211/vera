# Multichain activation readiness — 22 October 2026

**Historical checkpoint, superseded by [final activation report](MULTICHAIN-FINAL-ACTIVATION.md).** The subsequent user instruction authorized a candidate build and deployment subject to verification gates. The report below preserves the earlier state and authorization boundary.

**Prepared in the existing worktree; not ready to declare fully activated or production-ready.**
No existing application bundle was rebuilt, no service was restarted, and no
production routing, configuration, database or contract deployment was changed.
The source fixes below are not in the currently served frontend bundle.

## Exact resume point

- Worktree: `/root/VellaMultichain`, branch `feature/vella-multichain`.
- Starting implementation: `9cfdd7339e15de95278adc2bde6e326efe58e144`.
- Preserved DogeOS baseline: `1d82c1cbb45104a9e2c0b94d8c826949e9e55747`,
  tag `dogeos-competition-baseline`; `/root/VeraProtocol` remains on `main`.
  That checkout has pre-existing changes to `snapshot/vella-release.json` and
  untracked homepage assets/verification reports dated before this resume. They
  were left untouched; this report does not claim that checkout is clean.
- Multichain activation was recorded on 22 October at 13:16:59 UTC, PID 1560417,
  `http://127.0.0.1:8803`. Its saved HTTP, assets, private-account and WebSocket
  checks passed. The activation record points to `9cfdd73`; its frontend manifest
  hash still matches the existing build, created at 13:02:06 UTC.
- The startup check reported `freshLighter: false` and `modelConfigured: false`.
  Later read-only observations of the multichain database show fresh BTC, ETH and
  SOL prices from `Lighter public`, with advancing timestamps. This supports
  ongoing runtime activity; it is not a new HTTP/WebSocket verification.
- Direct current host HTTP verification and Chromium launch are blocked in this
  execution sandbox by `Operation not permitted`. A saved PID is not proof of
  current process identity. The previous activation record is preserved.
- Public Nginx configuration for `defivela.xyz` still points to the older Vela
  service on **8787**. Neither the independent DogeOS preview on **8802** nor the
  multichain preview on **8803** has been made the public upstream here.

## Existing capabilities

| Network | Configured network ID | Wallet adapter | Collateral and vault path |
|---|---|---|---|
| DogeOS Chikyū | 6281971 | MyDoge Sandbox SDK; injected EVM fallback | Existing testnet DOGE vault path; requires separate deployment/configuration |
| Solana | solana:mainnet-beta | Phantom / compatible `window.solana`, Ed25519 message signing | Simulated SOL vault |
| Base | 8453 | Injected EVM, EIP-191 message signing | Simulated ETH / WETH vault |
| Ethereum | 1 | Injected EVM, EIP-191 message signing | Simulated ETH / WETH vault |
| BNB Smart Chain | 56 | Injected EVM, EIP-191 message signing | Simulated BNB / WBNB vault |
| Arbitrum | 42161 | Injected EVM, EIP-191 message signing | Simulated ETH / WETH vault |
| Robinhood Chain Testnet | 46630 | Injected EVM, EIP-191 message signing | Simulated ETH / WETH vault |

These are the implementation's configured IDs, not a new external RPC audit.
WalletConnect is an optional configuration within the DogeOS SDK, not a separately
verified adapter for every network. No genuine wallet approval is claimed.

The selector persists the chosen network and clears the authenticated view on
switch. Each network/wallet has isolated collateral, debt, paper balances and
repayment state. All seven reuse the existing paper engine and Lighter BTC/ETH/SOL
market feed. DOGE market execution remains unsupported. Collateral prices are
illustrative, separate from Lighter trading prices. The six added chains have no
token transfer, real vault contract, blockchain receipt or cross-chain settlement
path. All lending is DEMO and execution is PAPER; real funding, real stablecoins,
real borrowing and live orders remain disabled.

## What prevented activation and what was fixed

1. The public site still serves the old application. Changing that requires a
   later authorized deployment; no production change was made.
2. A failed network-config request cleared the selector and left an incomplete
   wallet adapter and a failed selection in storage. The source now restores the
   previous complete adapter, keeps the selector available, rejects mismatched
   responses, and persists a selection only after success. Unit regressions pass;
   an outage/retry journey was added to the browser suite.
3. The host validator automatically rebuilt `site/multichain`, which is also the
   running preview's served directory. Validation now consumes the existing
   artifact, records its hash and source state, and attempts both browser suites
   without rebuilding or restarting anything.
4. A Chromium launch failure occurred outside the old report handler, leaving an
   earlier browser PASS file untouched. The multichain browser suite now records
   a timestamped BLOCKED launch result, separately from a failed journey.
5. `--verify` previously called configuration preparation. It now only probes the
   existing listener, checks every network's config/privacy/safety flags, and
   writes a separate timestamped current result without replacing activation
   evidence. Tests verify that it cannot call preparation or activation.
6. The module API tests could stall in executor cleanup under socket restrictions.
   Test-only cleanup now drains the executor while the existing heartbeat runs;
   no application event-loop behavior was changed.

The saved earlier browser failure was a synthetic-model signal failure. The
starting commit already contained fixture candle/clock refresh handling and a
subsequent six-network 390px PASS report. It was not treated as a current product
failure or counted as a new browser pass in this resume.

## Verification

- **174 backend tests passed**: 163 existing tests, 2 module API tests, 7 new
  network API journeys, and 2 verification safety tests; no overlap in this total.
- The seven new journeys use real ASGI handlers, cryptographic signature checks,
  SQLite ledgers and paper execution. They cover config/selection, wallet auth,
  collateral/vault state, borrowing, model-stage paper fills, partial repayment,
  settlement, withdrawal, cross-network denial, logout and persisted re-login.
  Wallet keys, Lighter frames, model replies and DogeOS observations are synthetic.
  DogeOS settlement deliberately remains locked until its synthetic confirmed
  chain observation; no actual testnet transaction was performed.
- **51 frontend unit tests passed**, plus server-rendering and isolated build
  preservation checks. The latter compile a throwaway fixture, not the served app.
- Both Chromium suites were attempted on the existing artifact and blocked before
  any journey by the sandbox socket restriction. New browser acceptance is
  **not passed**. The existing earlier six-network 390px pass and DogeOS fixture
  passes remain historical evidence only. The requested four-width matrix and
  new selector recovery need a socket-capable browser run against an artifact
  containing the source fix.
- Long and short positions, fee-inclusive inward-rounded stops, partial exits,
  stricter limits, and existing-position reconciliation pass the risk tests.
  Every account retains the stricter of its existing limit and a $2 per-position
  stop-trigger budget. This does not guarantee realized loss against slippage or
  funding. **No live protection change or verification is claimed.**
- All 168 captured served-artifact/routing files remain byte-identical. Homepage
  layout, illustration, styles and brand sources match the DogeOS baseline
  byte-for-byte. No new browser pixel comparison is claimed.

Machine-readable results: [resume-readiness.json](../snapshot/multichain/resume-readiness.json).
Detailed current tests: [backend](../snapshot/multichain/resume-backend.xml),
[module API](../snapshot/multichain/resume-module-api.xml),
[seven-network journeys](../snapshot/multichain/resume-journeys.xml),
[verification guards](../snapshot/multichain/resume-verifier.xml),
[frontend](../snapshot/multichain/resume-frontend.log),
[blocked browser result](../snapshot/multichain/resume-browser.json).

## Remaining activation gates

The code is prepared for review. The served artifact is intentionally unchanged
and does not include the new selector fix. A later explicitly permitted artifact
update and full browser acceptance are required before calling that fix active.
Direct current HTTP/assets/WebSocket verification must also pass on the host.
`tools/multichain_preview.py --verify` performs only those read-only probes;
`tools/validate_multichain_host.py` checks the existing artifact without rebuilding.

The isolated preview has no dedicated AI model, DogeOS factory, demo attestor, or
MyDoge client registration. Autonomous paper trading stays blocked without the
model; genuine DogeOS vault actions stay blocked without the dedicated testnet
setup. Do not reuse credentials from Vela or WHY, fabricate a model connection,
or relax the gates. Genuine EVM, Solana and MyDoge approval flows remain unverified.
Any DogeOS deployment requires separate explicit testnet authorization. Production
activation is not authorized by this task, and no mainnet financial functionality
is part of the prepared change.
