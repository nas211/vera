# VELA multichain final activation — 22 October 2026

**Latest update, 19:29 UTC:** the authoritative infrastructure audit found the
existing model and MyDoge configuration. The secure handoff is now implemented;
68 targeted tests pass. The preserved frontend and existing production remain
unchanged. Deployment is **BLOCKED** by host/external acceptance and the new DogeOS
deployment binding; live protection is **NOT TESTED**. See the
[current configuration handoff report](MULTICHAIN-CONFIGURATION-HANDOFF.md).
The older absence statements below describe only the isolated preview at the
time of those checks, not missing credentials in the existing Vela deployment.

**Resume verification, 19:01–19:03 UTC: prepared code PASS; tests PASS within the scopes below; merge and deployment BLOCKED; live protection NOT TESTED.**

Resumed the exact clean `feature/vella-multichain` checkout at
`f9b20d84ab40beecd189a52b6847f80ebe49627a`. The candidate still corresponds to that
application commit. This resume changes only this report and verification evidence.
The original checkpoint, application sources, candidate build, credentials, databases,
served files, routing and services were preserved. No rebuild or regression-suite
rerun was needed. The current report/evidence commit is separate from the candidate's
source commit; neither has been merged or activated by this resume.

| Verification category | Status | Current evidence and limits |
|---|---|---|
| Checkpoint and candidate integrity | PASS | Checkpoint checksum, 17 checkpoint evidence files, both release archives, manifest, HTML, all 18 assets and all 27 build inputs match |
| Saved regression results | PASS | Reused 192 backend, 51 frontend and 21 contract tests; unchanged application code |
| Seven-network API and server rendering | PASS | Saved synthetic journeys retained for DogeOS, Solana, Base, Ethereum, BNB Chain, Arbitrum and Robinhood Chain Testnet |
| HTTPS-origin sessions on all seven networks | PASS | New in-process ASGI checks at `https://defivela.xyz`: candidate onboarding HTML, origin-bound synthetic signatures, Secure/HttpOnly/SameSite cookies, session restoration, network isolation, logout and replay denial |
| WebSocket origin enforcement | PASS | New in-process ASGI check accepts the public origin and rejects an unrelated origin with 1008; empty feed, no network transport claim |
| Candidate production safety configuration | PASS | Dedicated environment remains PAPER/DEMO; all seven isolated configs disable live trading, real funding/lending/stablecoins and cross-chain settlement; no configured factory enables testnet transfers |
| Chromium desktop/mobile acceptance | BLOCKED | Current AF_INET socket capability check returns `PermissionError: [Errno 1] Operation not permitted`; no alternate Chromium executor available in session tools; existing restricted launch was not retried |
| Genuine MetaMask, Phantom and MyDoge approvals | BLOCKED | No wallet-equipped browser or physical approval channel; synthetic signatures do not count as real wallet approval |
| Genuine AI model connectivity and paper signals | BLOCKED | Neither `VELA_MODEL` nor `VELA_MODEL_KEY`, nor compatible aliases, is configured in the dedicated environment or current process; no `ai-connection` record exists in the preview DB |
| DogeOS network settings and SDK source wiring | PASS | Chain 6281971, testnet RPC/explorer and installed `@dogeos/dogeos-sdk` 4.0.4 reviewed; Sandbox EVM configuration and supported signing hooks retained |
| DogeOS runtime initialization, connection and transaction simulation | BLOCKED | MyDoge client registration, factory and demo attestor are absent; no genuine SDK connection, RPC simulation or testnet transaction performed |
| Lighter BTC/ETH/SOL database observations | PASS | Two read-only observations at 19:01:30 and 19:02:56 UTC show advancing timestamps, `safe=1`, source `Lighter public`; ages 0.168–0.331 seconds at observation |
| Direct Lighter REST/WebSocket, reconnect and stale-feed checks | BLOCKED | Socket restrictions prevent direct probes; prior fixture tests remain PASS, but DB timestamps and documentation do not establish direct connectivity or live reconnect behavior |
| Public service handoff | BLOCKED | Nginx still routes to 8787; current multichain launcher is loopback-only at 8803 and selects the older `site/multichain` artifact; no validated candidate public service exists |
| Public HTTP/HTTPS/API/WebSocket health | BLOCKED | Executor cannot open host sockets; independent web retrieval of the public URL was also unavailable; this does not establish that the public service is down |
| Existing services and process identity | BLOCKED | Saved host PID is not visible in this executor's process namespace; no service was signalled or restarted |
| TLS certificate file | PASS | Existing certificate covers `defivela.xyz`, valid 21 October 2026 through 19 January 2027; serving certificate/handshake NOT TESTED |
| Deployment and rollback file integrity | PASS | All 161 captured deployment files remain unchanged; archived public/multichain frontend members match rollback manifest hashes |
| Operational rollback rehearsal | NOT TESTED | No deployment or service restart occurred |
| Live position protection | NOT TESTED | No live stop was changed or verified |

All browser steps remain **BLOCKED**: homepage, Start Trading CTA, wallet connection
modal, seven-network selector, dashboard, demo funds, Start AI Trading, positions/P&L,
Stop Trading and the 320/390/768/1440px matrix. Genuine wallet account changes,
rejected requests and disconnects remain **BLOCKED** for MetaMask, Phantom and MyDoge;
EVM network switching remains **BLOCKED**. Solana cluster switching is **NOT TESTED**
and is not represented as an EVM switching operation.

The dedicated file `.runtime/multichain/preview.env` remains mode 0600. The model
requires a securely provisioned model name and key. `VELA_MODEL_ENDPOINT` is optional
when using the existing default `https://api.openai.com/v1/responses`; no custom endpoint
is configured. No key was printed, borrowed from another project, written or committed.
Actual model response → deterministic risk approval → paper fill → decision log
verification remains **BLOCKED**. The saved synthetic pipeline tests remain **PASS**.

The DogeOS settings match the [official developer quickstart](https://docs.dogeos.com/en/developers/developer-quickstart).
The SDK bridge retains active account/provider checks, `signMessage`, EVM switching
and disconnect handling, consistent with the [official SDK workflows](https://docs.dogeos.com/en/sdk/guides).
Missing dedicated settings are `VERA_PROTOCOL_MYDOGE_CLIENT_ID`,
`VERA_PROTOCOL_FACTORY` and `VERA_PROTOCOL_DEMO_ATTESTOR_KEY` (compatible ADP aliases
are also absent). Optional WalletConnect project configuration is absent. DogeOS
uses testnet EVM addresses; the other six vault adapters remain explicitly simulated.
The transaction path currently requests owner-approved `eth_sendTransaction` and
checks receipts/three confirmations; it contains no separate application-side
`eth_call` preflight simulation. No wallet or transaction success is inferred.

The configured Lighter WebSocket endpoint matches the
[official WebSocket reference](https://apidocs.lighter.xyz/docs/websocket-reference).
Independent web retrieval of the REST URL reported JSON content type but returned
no inspectable market payload, so it does not pass market-data acceptance. No real
order execution was enabled. The existing feed's sequence, reconnect/resubscribe and
stale-feed fixture coverage is retained without relabeling it as a live test.

The new handoff checks demonstrate why a port-only activation is unsafe: a request
from `https://defivela.xyz` against the current loopback configuration receives HTTP
403 on authentication writes, and its WebSocket closes with 1008. Public-origin
configuration passes those checks only in a disposable in-process application.
Nginx's existing legacy proxy-auth include remains untouched. The pending 8802 TLS
configuration belongs to the earlier DogeOS preview and is not a multichain release
procedure. Actual service startup, candidate selection, TLS/proxy routing and rollback
must be verified on a host with working sockets before activation. There is no Git
remote; the existing `main` checkout still contains pre-existing work and was preserved.

The all-account maximum remains the stricter existing limit or **$2 per position**,
including fees and inward stop rounding. Saved tests cover long/short positions,
partial exits and reconciliation. No risk parameter changed. This stop-trigger
budget is not a guarantee against slippage or funding. No new P&L result is claimed.

Candidate: `.runtime/releases/candidate-f9b20d84ab40`.
Rollback: `/root/VellaMultichain/.runtime/releases/pre-final-20261022T135152Z`.
Public target: [defivela.xyz](https://defivela.xyz); new-version activation **BLOCKED**.
Machine-readable current evidence: [resume result](../snapshot/resume-activation-20261022/result.json),
[integrity/configuration audit](../snapshot/resume-activation-20261022/audit.json),
[public-origin ASGI checks](../snapshot/resume-activation-20261022/public-origin.json),
[market observations](../snapshot/resume-activation-20261022/market-observation.json).
Reproduction scripts are preserved locally in `.runtime/resume-activation-20261022/`.
Critical browser, genuine-model and public-service gates remain unresolved; deployment
was not forced. Missing wallet approvals did not prevent the independent checks above.

## Original checkpoint report — retained as historical evidence

**Prepared and tested: PASS. Public activation and deployment: BLOCKED.**
The existing implementation was continued from `e6c31854dd31ea33df09b9acc9d8cee644561720`
on `feature/vella-multichain`. A new production-mode frontend exists in
`site/candidate`, including the selector recovery fix. No served frontend was
replaced, no process was restarted, and no production routing, DNS, credentials,
application database or contract deployment was changed. The final commit is the
Git commit containing this report; the local release archive also records its hash.

## Implementation and design

- Updated the homepage to **VELA — Multichain AI-Powered DeFi Trading**, the requested
  subtitle, **START TRADING**, and seven compact network badges. DogeOS remains
  featured; the badges and caption distinguish demo DeFi and the DogeOS testnet.
- Kept the three-section page structure, navigation, graphic geometry, animations,
  colors, typography and existing responsive CSS. Only copy, the requested strip
  and its scoped styles changed. The existing illustration changed only its visible
  brand spelling from VELLA to VELA. Browser/pixel preservation remains unverified.
- Kept `/vault` as the CTA destination and preserved wallet onboarding, dashboard,
  simulated capital, borrowing, portfolio, history and Start/Stop controls.
- Preserved the seven-network registry, wallet/session implementation and network
  selector from `e6c3185` byte-for-byte. Recovery regressions cover retaining the
  selector, restoring the previous network/configuration, clearing private state,
  rejecting mismatches and retrying without a partial adapter configuration.
- Added the requested backend-only `VELA_MODEL`, `VELA_MODEL_KEY` and
  `VELA_MODEL_ENDPOINT` aliases. Existing `VERA_PROTOCOL_`/`ADP_` model configuration
  remains compatible; conflicting aliases fail closed without disclosing values.
  No credential was supplied, copied or changed. Missing model configuration now
  has a clear **AI disconnected** message in the existing dashboard.
- Added isolated candidate output to the existing esbuild process, asset SHA-256
  records and exact-artifact ASGI routing checks. The host browser validator can
  consume `--build candidate` without replacing an active artifact. Both browser
  suites now record launch failures as BLOCKED rather than leaving stale PASS files.

## Seven-network verification

PASS below means local API/cryptographic authentication, synthetic wallet-adapter
tests and server rendering. It does **not** mean browser acceptance, genuine wallet
approval, live chain transactions or live AI responses.

| Network | Existing wallet adapter | Collateral | Synthetic journey / rendering | Genuine wallet |
|---|---|---|---|---|
| DogeOS, 6281971 | MyDoge Sandbox SDK / injected EVM | test DOGE | PASS | BLOCKED |
| Solana, solana:mainnet-beta | Phantom-compatible | demo SOL | PASS | BLOCKED |
| Base, 8453 | Injected EVM | demo ETH / WETH | PASS | BLOCKED |
| Ethereum, 1 | Injected EVM | demo ETH / WETH | PASS | BLOCKED |
| BNB Smart Chain, 56 | Injected EVM | demo BNB / WBNB | PASS | BLOCKED |
| Arbitrum, 42161 | Injected EVM | demo ETH / WETH | PASS | BLOCKED |
| Robinhood Chain Testnet, 46630 | Injected EVM | demo ETH / WETH | PASS | BLOCKED |

These are preserved registry IDs, not a new external RPC audit. The homepage uses
the requested “BNB Chain” label; the registry name remains “BNB Smart Chain.”

The seven API journeys exercise selection, chain-scoped authentication, vault and
collateral state, demo borrowing, the research/review/direction pipeline, risk-approved
paper fills, partial repayment, settlement, withdrawal of demo/testnet fixture
collateral, account isolation, logout and persisted re-login. DogeOS uses synthetic
confirmed chain observations and retains its distinct testnet settlement boundary.
The other six networks use persistent local simulations without token transfers or
blockchain receipts. No real deposits, borrowing, withdrawals or orders were enabled.

## Verification results

| Category | Status | Evidence / boundary |
|---|---|---|
| Backend regression | PASS | 192 tests, 0 failures; `snapshot/final-activation/backend.xml` |
| Frontend unit and wallet-adapter regressions | PASS | 51 tests; `snapshot/final-activation/frontend.log` |
| Homepage, onboarding, network selector and five dashboard tabs | PASS | Server rendering for all seven networks; no browser claim |
| Candidate production build | PASS | Existing minified/split esbuild build; `site/candidate/build-manifest.json` |
| Routes and static assets | PASS | Exact candidate HTML at `/`, `/vault` and network query; asset hash checks through ASGI |
| Contract regression | PASS | 21 offline Foundry tests; no deployment or transaction |
| Paper execution and risk | PASS | Long/short, market/limit fills, stops, targets, partial exits, liquidation, funding gaps/cost allocation, P&L, reconciliation, stricter limits and Stop Trading |
| AI configuration and decision pipeline | PASS | Alias/configuration, missing-key and synthetic three-stage decision/risk/execution tests |
| Genuine AI model request | BLOCKED | No dedicated model name/key in the multichain environment or this process |
| Lighter adapter behavior | PASS | BTC/ETH/SOL metadata, books, funding, sequence gaps, stale detection, reconnection and resubscription fixtures |
| Current Lighter runtime observation | PASS | At 14:07:25 UTC, read-only preview DB showed BTC/ETH/SOL from `Lighter public`, safe flags and age 0.21–0.22s |
| Direct live REST/WebSocket verification | BLOCKED | Host networking rejected with `PermissionError: [Errno 1] Operation not permitted`; database observations do not replace this check |
| Chromium acceptance / mobile visual matrix | BLOCKED | Existing `setsockopt: Operation not permitted`; unchanged environment, no repeated launch. 320/390/768/1440px checks remain pending |
| Genuine MyDoge, Phantom and EVM wallet approvals | BLOCKED | No working browser/approval environment; MyDoge registration also absent |
| Actual DogeOS testnet vault journey | BLOCKED | Dedicated factory and demo attestor absent; no testnet deployment performed |
| Candidate PAPER/DEMO restrictions | PASS | All seven configs return `liveEnabled=false`, `realFunding=false`; live order/funding endpoints absent |
| New public application / homepage / seven networks | BLOCKED | Candidate has not been deployed; public HTTP verification unavailable |
| Live position protection | NOT TESTED | No live protection changed or claimed |

Every account retains the stricter of its existing limit and **$2 per-position,
fee-inclusive stop-trigger budget**. Inward rounding, partial exits and existing
positions are covered. This is not a guarantee against slippage or funding. No
risk parameter or Lighter execution adapter was changed. Today's UTC realized P&L
and closed-fill counts remain distinct from all-time figures.

## Exact deployment state and blockers

- Public address: `https://defivela.xyz`. Existing Nginx still proxies to **8787**
  and includes `/root/Vera/ops/vela-portal-proxy.conf`. The older public source
  checkout `/root/Vera` is at `ad8f98948de4ddcd93150939a8efe57423bcbd63`.
  A current public HTTP response could not be verified; no new deployment is claimed.
- The independent DogeOS preview remains associated with **8802**. The saved
  multichain preview activation is **8803**, commit `9cfdd7339e15de95278adc2bde6e326efe58e144`,
  PID 1560417. Saved PID metadata is not current process-identity verification.
  Its served artifact is still the older bundle, without `e6c3185` selector recovery.
- The multichain environment remains `PAPER`, `DEMO`, multichain enabled, with
  origin `http://127.0.0.1:8803`. It has no dedicated model name/key, MyDoge client
  registration, DogeOS factory or demo attestor. Old application credentials were
  neither borrowed nor exposed. Dynamic brand reads from this feature checkout
  may see the new copy; this is not activation of the new frontend or backend process.
- The existing multichain launcher is intentionally restricted to the isolated
  loopback origin and refuses a mismatched saved runtime fingerprint. The public
  routing/origin/cookie/WebSocket configuration and service handoff have not been
  validated for this release. Blindly changing the upstream port would not deploy
  a correct public application. No deployment branch/pipeline for public multichain
  activation is configured, and the repository has no Git remote.
- `main` and `release/dogeos-competition` remain at
  `1d82c1cbb45104a9e2c0b94d8c826949e9e55747`. The `main` checkout has unrelated,
  pre-existing modified/untracked files and backs the earlier preview. Changes
  against this baseline were reviewed: scoped network identities/auth/ledgers,
  simulated adapters, selector recovery, candidate packaging and model aliases;
  no new live order adapter, contract change or risk-limit increase.
- **Merge: BLOCKED. Deployment: BLOCKED.** The user authorized conditional
  deployment, but the unresolved browser/wallet/integration and host service/public
  configuration gates prevent satisfying that condition. No branch was merged or
  unrelated service restarted. This is not a request for the same authorization again.

## Rollback and next host verification

Rollback archive: `/root/VellaMultichain/.runtime/releases/pre-final-20261022T135152Z`.
It contains the prior multichain frontend, public frontend and public Nginx config
(no included secret files), plus a SHA-256 manifest. All **161** captured served
artifact/routing files are unchanged. The source rollback reference is `e6c3185`;
the existing DogeOS baseline tag is `dogeos-competition-baseline`. No database restore
is needed because this phase did not change application data.

On a host where the environment has changed to permit these checks, consume the
prepared candidate with `.venv/bin/python tools/validate_multichain_host.py --build candidate`.
Complete actual wallet approvals separately. Securely configure the missing dedicated
model/testnet dependencies, verify real Lighter subscriptions and an actual approved
AI paper signal, then complete public service/origin/routing validation before merge
and deployment. Build and backend must be promoted together; copying frontend files
over the service on 8787 is insufficient. Verify public HTML/assets, all seven configs,
private-account denial, market WebSocket and PAPER/DEMO restrictions after deployment.
Retain the old service/artifacts until that verification passes.

Machine-readable evidence: [final release checks](../snapshot/final-activation/release.json).
No unavailable external integration is counted as passed.
