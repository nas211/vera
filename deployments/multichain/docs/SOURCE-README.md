# VELA — Multichain AI-Powered DeFi Trading

Seven network experiences share **Vault | Borrow | AI Trade | Agent Mode**, with DogeOS featured. All trading is simulated. Branding lives in `config/brand.json`. The older Vela application is a read-only source, never a runtime dependency or shared database.

Current release state: [29 October status](docs/STATUS-20261029.md) — the candidate is served at `https://defivela.xyz` through the read-only DogeOS gateway and public browser acceptance passes; genuine wallet approvals remain unverified. Earlier history: [final multichain activation report](docs/MULTICHAIN-FINAL-ACTIVATION.md). The private-preview descriptions below record earlier DogeOS work and do not establish current public activation.

## Run the local rehearsal

From this repository:

```bash
PYTHONPATH=backend .venv/bin/python tools/rehearse.py
```

This exercises the real API, cryptographic signature verification, SQLite accounting, reused Vela decision pipeline and Lighter paper engine. Wallet keys, chain observations, market data and model replies are explicit deterministic **fixtures**. It performs no testnet transaction. Results are saved to `snapshot/rehearsal.json`.

## Run the web application on a socket-enabled host

```bash
PYTHONPATH=backend .venv/bin/python -m uvicorn autodefi.api:create_app --factory --host 127.0.0.1 --port 8802
```

Open `http://127.0.0.1:8802` **only after that command binds successfully**. An existing private TLS process was observed on the configured preview, but the latest loopback health probe timed out. It was not restarted. Its previous frontend entrypoint is preserved byte-for-byte. Do not run a second writer against its database. Use one API worker so the paper engine has a single writer; a database-specific file lock rejects a duplicate runtime.

The short homepage introduces four independent products. START TRADING opens the wallet-first `/vault` application with Overview, Vault, Borrow, AI Trade and Agent Mode. The borrowing simulator and tutorials live inside the application. Personal data requires a verified wallet signature. No account is automatically funded. The MyDoge button requires a valid Sandbox SDK client ID with the development origin registered; browser-wallet fallback is separately labeled. Neither connector's genuine user approval has been browser-verified here. The intended production origin is `https://defivela.xyz`; this build has not changed the old Vela deployment or its routing.

Configuration names are listed in `.env.example`. The existing `VERA_PROTOCOL_` and legacy `ADP_` variable names remain compatible with VELLA. Conflicting old/new values fail closed. Only the explicitly authorized `VELA_MODEL`, `VELA_MODEL_KEY` and `VELA_MODEL_ENDPOINT` aliases are supported; other `VELA_` settings remain isolated. The `autodefi` Python import, auth cookie/header names and contract signing domain remain unchanged for compatibility. The repository and data moved together from `/root/AutonomousDeFi` to `/root/VeraProtocol`, without a second copy. Export only this application's configuration; never source Vela's environment. There is no automatic `.env` loader. Network reads default off. Actual testnet use requires explicit deployment authorization, a new factory address, a dedicated demo attestor whose address matches the factory, a registered MyDoge SDK origin/client ID, an independently configured model, and permitted RPC/market/model access. No testnet contracts have been deployed by this build.

## Build and test

```bash
# In a normally network-enabled environment, install the exact locked dependencies:
.venv/bin/python -m pip install -r requirements.txt
npm --prefix site ci
npm --prefix site run build:candidate  # isolated release; preserves all served directories
PYTHONPATH=backend .venv/bin/python -m pytest tests -q
npm --prefix site test
npm --prefix site run test:multichain
(cd contracts && ../.tools/foundry/forge test --offline -vv)
```

The environment already contains an independent Python environment and frontend dependency copy. An online npm attempt failed with DNS `EAI_AGAIN`; all eight direct/override dependency versions were checked against the existing installation before copying. The lockfile retains compatible transitive records from that installation. A clean online `npm ci` has **not** been verified. No version was silently changed. The SDK's `wagmi/connectors` import is resolved to the same `injected` implementation exported by `@wagmi/core`, avoiding unused optional connector packages.

On a host with working Chromium, run `.venv/bin/python tools/validate_multichain_host.py --build candidate`. This consumes the exact candidate without rebuilding or restarting a service. It uses synthetic provider/API fixtures, not genuine wallet approvals. The separate `test:browser` command targets an already reachable preview; do not repeatedly retry blocked sockets. Genuine MyDoge approval and testnet transactions remain unverified.

## What is real, simulated and pending

| Layer | Implemented behavior | Verification boundary |
|---|---|---|
| Vault contracts | Solidity native-DOGE custody, deterministic factory, immutable ownership, collateral locks, bounded permissions, revocation, pause, replay-bound attestations, owner-signed settlement | 21 previously passed local Foundry tests; no DogeOS deployment or transactions |
| Wallet authentication | Single-use, expiring, origin/domain/chain-bound EIP-191 challenge; recovered address; hashed sessions; HttpOnly cookie, Secure on HTTPS; same-origin writes | Cryptographic backend and synthetic-provider tests; genuine MyDoge/mobile approval unverified |
| Lending | Atomic, duplicate-protected simulated USDC loan credit, interest, pool reserves, per-wallet isolation, debt retention | Local SQLite and ASGI tests |
| Trading | Unchanged copied Vela research/review/direction, strategy DSL, Lighter depth fills, fee accounting and risk reconciliation | Deterministic fixture pipeline/market tests; real model and live Lighter connection unverified |
| Settlement | Stop entries, close paper positions at fresh depth, repay interest/principal, retain shortfall, simulated collateral sale, persist default, owner certificate | Local rehearsal; real native test-DOGE settlement unverified |
| Frontend | New responsive design, wallet-first dashboard, vault transaction signing, borrowing, agent permissions, trading, health lab and settlement dialogs | Build + wallet logic + server rendering passed; browser layout unavailable |
| Real lending/execution | Disabled adapters, no exchange signer or real funding endpoint | Negative API/adapter tests |

## Accounting and controls

The existing `.runtime/lighter-showcase/protocol.sqlite3` is independent of Vela. Loan and paper ledgers use separate tables in one SQLite transaction boundary, so borrowing cannot debit liquidity without crediting the correct paper account. Monetary values use decimal accounting; USDC obligations round upward to micro-units at repayment, and collateral uses integer wei. Interest is accrued at full precision rather than rounded on every observation. Loan APR is fixed in its ledger row at origination.

Research parameters are configurable in `config/risk.json`: illustrative $0.20/DOGE, 40% maximum LTV, 65% liquidation threshold, 5% APR, 20% liquidity reserve, and a 5% simulated liquidation discount. These are not verified market quotes or safe production parameters. Quote freshness and confirmed chain freshness fail closed. The health lab changes a clearly labeled illustrative scenario.

The contract conservatively locks **all** collateral during Requested/Active debt. The UI displays actual contract withdrawal capacity separately from theoretical health-based capacity. Backend state alone cannot withdraw DOGE. A dedicated attestor certifies the simulated ledger; only the vault owner can submit the signed settlement. Any test DOGE seized is explicitly displayed for confirmation and goes to the immutable testnet treasury. This demo attestor is a trust boundary, not a production oracle/lender.

Every new and existing paper position retains a fee-inclusive stop-trigger budget of **$2 or less**, including partial exits and inward stop rounding. This is not a guarantee against slippage or funding. Permission sizing, leverage, expiry, market allowlist, collateral health and drawdown checks run before both submission and fill. Below the configured health thresholds, size/leverage are reduced or entries are blocked. Optional automatic closure requires owner authorization; it does not silently authorize collateral transfers. Normal Stop leaves positions under protective management; Stop & Settle explicitly confirms closure.

A shortfall keeps debt and collateral locked. Simulated liquidation proceeds debit a separate simulated buyer-liquidity ledger. Insufficient proceeds retain bad debt and any execution deficit; no debt is erased. Surplus is a separately labeled simulated ledger balance, not USDC in the wallet. The protocol does not offer real redemptions.

P&L separates today's realized results (UTC, closed-fill counts) from all-time total P&L and all-time closed fills. Loan, direct allocation, repayment and demo withdrawal cashflows do not masquerade as trading profits. DOGE execution remains disabled because venue support was not verified; BTC, ETH and SOL are the reused supported markets.


## Modular services and repayment

`autodefi.vaults` delegates to the preserved DogeOS confirmation code; `lending` owns the existing debt ledger; `trading` governs the preserved paper engine; `repayment` transfers free equity to that same ledger; `orchestration` composes them for Agent Mode. One authenticated wallet maps to one paper account and vault. No schema migration or duplicate loan ledger is introduced. Repayment configuration uses the existing `kv` table; receipts and permission audits use `loan_events`.

Vault-only users can deposit and withdraw contract-eligible test DOGE without borrowing or trading. Borrow-only users receive a simulated credit in the shared account and can repay without starting AI. AI Trade-only users explicitly allocate up to 10,000 non-transferable demo USDC over the wallet's lifetime and authorize a 24-hour allocation, position-size, leverage and stop budget. Direct allocations cannot top up an unsettled loan. A demo withdrawal retires equity into the existing simulated-surplus record; it never sends real funds. When a loan exists, its confirmed on-chain permission and health limits govern all entries, including previously allocated capital.

Manual repayments accept a chosen amount from settled free cash. The capacity calculation reserves open-position margin, unrealized losses, remaining entry fees, conservative exit fees and pending entry orders; it never spends unrealized gains. Stale observations or incomplete funding accounting block equity movement. Interest is paid before principal. Amounts use decimal micro-USDC precision; unpaid debt survives losses. Full repayment updates health immediately but only owner-confirmed contract settlement can unlock collateral. Existing contracts conservatively lock all DOGE until that confirmation.

Profit Sweep allocates 25%, 50%, 75%, 100% or a custom percentage of new realized net profit. Realized gross fills minus all observed trading fees and funding determine eligibility; loan cashflows and unrealized gains are excluded. A persistent high-water mark, authorization baseline and consumed-profit accounting prevent reused profits, including after restarts and intervening losses. Deferred sweep credit cannot repay from capital after subsequent losses consume the profit. Changes to permission establish a fresh baseline.

Daily and weekly schedules use fixed UTC intervals; monthly schedules retain the authorization day and clamp to month-end. Missed intervals coalesce into one check, never a burst of catch-up debits. The amount is bounded by the selected maximum and debt. If free cash after the minimum remaining balance is insufficient, the payment is skipped and a visible event records why. Both automation methods bind to the current loan, expire after the chosen 1–365 days, stop at zero debt, and are revocable; disconnect also revokes them. A scheduled permission must outlast its first due date. The default is 90 days.

All money-changing API operations require wallet authentication and an idempotency key. Repayments, interest/principal updates, pool credits, cash deductions, receipts and sweep/schedule progress commit atomically under SQLite's write lock. Tests cover rollback after an injected failure and concurrent duplicate requests. No real borrower, lender or exchange adapter is enabled. `real_lending.py` remains the disabled interface for future genuinely funded liquidity.

The primary simulation notices appear only at the top of Borrow and AI Trade. Action confirmations and receipts retain the appropriate simulated/testnet disclosures. Five 40-second scripts and interactive storyboards are in `site/src/tutorials.js`. Generated video links supplement the walkthroughs where available; the video provider's allowance prevented generation of the remaining tutorials. There are no fabricated player thumbnails.

## Prepared build versus active preview

`npm --prefix site run build` writes `site/prepared`. The private preview now serves that directory through the modular backend after authorized host activation. The previous `site/dist` assets and source/database backups remain available for rollback. No public deployment, DNS, SSL, Nginx, contract deployment or funded transaction was performed. The old activation manifest describes the prior release; `snapshot/modular-release.json` describes the build, and `snapshot/modular-activation-report.json` records actual serving verification.

The maximum $2 fee-inclusive per-position stop-trigger budget and stricter existing limits apply to every account, including direct allocation and future accounts. Long, short, partial exit and existing-position reconciliation are covered in local tests. The implementation is activated on the private preview; actual position protection remains unverified. A stop trigger does not guarantee realized losses against slippage or funding.

The existing launcher supports guarded modular replacement, preservation and rollback. The authorized-host activation succeeded: the modular build is now verified on private port 8802, with matching frontend assets, modular API, PAPER/DEMO enforcement and fresh Lighter data. See [private preview activation](docs/PRIVATE-PREVIEW-ACTIVATION.md) and [the activation report](snapshot/modular-activation-report.json). Genuine MyDoge approval, testnet transactions and live position protection remain unverified. Public production is unchanged.

## Source provenance

`config/reuse-manifest.json` records exact SHA-256 hashes for 17 copied Vela source files at `ad8f98948de4ddcd93150939a8efe57423bcbd63`. The originals and this immutable source snapshot were not edited. All new behavior is in `backend/autodefi`, the new contracts and the new frontend. No Vela credentials, customer balances, databases, service configurations or deployment files were copied. The owner-authorized client-ID check found no DogeOS SDK ID in the inspected Vela configuration; portal `clientId` fields are request idempotency IDs, not wallet SDK registration. DogeOS supports registered origins within an SDK project; reuse requires the correct Sandbox ID and allowed VELLA origin, without removing Vela’s origins.

Official references: [DogeOS developer quickstart](https://docs.dogeos.com/en/developers/developer-quickstart), [wallet workflows](https://docs.dogeos.com/en/sdk/guides), [SDK configuration](https://docs.dogeos.com/en/sdk/configuration), [Lighter market metadata](https://apidocs.lighter.xyz/reference/orderbooks), [Lighter WebSockets](https://apidocs.lighter.xyz/docs/websocket-reference), [OpenZeppelin contracts](https://docs.openzeppelin.com/contracts/5.x).
