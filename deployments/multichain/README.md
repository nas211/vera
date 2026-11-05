# Vela — DogeOS vaults, rewards and AI paper trading

[Working application](https://defivela.xyz/app/) · [Noah project](https://trynoah.ai/playground/6aeb352f7d19514dabedc93a) · [Hackathon](https://trynoah.ai/hackathon/6ad632514ff7b2bf9f7ea37d)

Vela combines owner-controlled native DOGE vaults, reserve-funded rewards, demo borrowing and AI paper trading. The current implementation is in **this directory**. Other top-level code and earlier documents are retained as project history; they are not the current deployment manifest.

## What Noah built

The **VELA Collateral Rewards** project was created in Noah on November 4, 2026. Noah contributed the dashboard, initial rewards-accounting logic, vault/factory components and build checkpoints. We exported that module, repaired financial and wallet-flow edge cases and integrated it with Vela's pre-existing backend and paper-trading engine. The whole Vela application was not built in Noah. The saved Noah preview uses deterministic fixtures; the connected experience is the external application linked above. Eligibility is for the organizers to assess; this repository does not claim organizer approval.

## Current functionality

- DogeOS testnet chain **6281971**: native-DOGE vault creation, deposits, reserve-funded rewards, owner-signed claims and withdrawals.
- Current reward rate: **5% simple APR**, accruing while the shared reserve supports it. This is not a guaranteed yield or an immediate 5% payout.
- Demo lending: simulated USD credit, interest-first repayment and owner-confirmed testnet collateral settlement.
- AI trading: paper execution only. No live exchange orders or real stablecoin lending.
- Same-owner migration from an empty, settled legacy vault preserves account history and demo cash.
- Every account retains its stricter existing limit or a **$2 fee-inclusive per-position stop-trigger budget**. Long/short positions, partial exits, inward rounding and existing-position reconciliation are covered by automated checks. Slippage and funding can exceed that trigger budget.

## Validation and deployment

The published implementation previously passed **502 backend tests, 251 frontend tests and 47 contract tests**, including fuzz properties. TypeScript, scoped lint, build and release integrity checks passed. Those local results are separate from GitHub Actions for this source-publication commit. This is not an independent security audit or a production-readiness certification. Dependency advisories remain in the wider repository/tooling tree.

The deployed reconciler records completed loan and reward-vault round trips. The reward payout is recorded as **0.000003757610350076 DOGE**, followed by principal withdrawal and zero collateral/debt. Additional accrued rewards remain claimable. A fresh independent RPC audit of the new reward receipts was blocked by this terminal's network policy; do not describe that audit as completed. Live financed-trading fills and protective exits remain unverified. Trading is stopped.

## Inspect the code

- `deployments/multichain/backend/autodefi/`: vault, loan, account, repayment and trading orchestration.
- `deployments/multichain/contracts/`: reviewed vault/factory/shared-pool contracts and tests.
- `deployments/multichain/site/vela-dashboard/`: integrated Noah-derived dashboard.
- `deployments/multichain/tests/`: backend and financial invariants.
- `deployments/multichain/noah/vela-collateral-rewards/`: original build prompts and module architecture.
- `deployments/multichain/tools/`: build and deployment tooling; publication does not execute deployment scripts.

No live database, user session, wallet private key or production environment file is included in this update. Example configuration files contain placeholders. Keep credentials server-side and never import them into a frontend build.

## Reproduce local checks

```bash
cd deployments/multichain
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix contracts install --ignore-scripts
forge build --root contracts
npm --prefix site ci --ignore-scripts
npm --prefix site run build
npm --prefix site run build:candidate
# Only in a fresh local checkout, initialize the legacy UI test fixture:
test -d site/dist || cp -R site/prepared site/dist
PYTHONPATH=backend .venv/bin/python -m pytest tests -q
npm --prefix site/vela-dashboard ci --legacy-peer-deps --ignore-scripts
cd site/vela-dashboard
npx vitest run src/lib
npx tsc --noEmit -p tsconfig.app.json
npx tsc --noEmit -p tsconfig.node.json
```

Contract checks use Solidity 0.8.30 and Foundry with dependencies from `contracts/package.json`. Test fixtures are not live transactions. Native actions require the owner's browser wallet and the configured testnet backend; do not deploy or fund contracts to browse the repository.

## DogeOS evidence

Reward factory: `0x7304EbdE8D313c95c3a89caa20e7915545a0665e`  
Shared pool: `0x79a20c6c39525A6253C8aA5D04EE5682ECB2B0Ae`  
Demonstrated vault: `0x2937bfdaea76fc29529d7792ac674b6eef55eca5`

| Action | Recorded confirmed transaction |
| --- | --- |
| Reward vault creation | [0x5b2750…8b603](https://dogeos-testnet.l2scan.co/tx/0x5b2750a6cd5db170cbb456cb2dab13edfaae3df2682b326524513d2c6da8b603) |
| Reward vault deposit | [0x1fad3b…202c](https://dogeos-testnet.l2scan.co/tx/0x1fad3b1797bc08c7aa6339501f320180e61d7cff91906201d72fd9d8d501202c) |
| Reward claim | [0x737df8…d53d](https://dogeos-testnet.l2scan.co/tx/0x737df894fca8138f8fcbd57ca9473775092053caa2c3d310fdbd7b46a6b6d53d) |
| Principal withdrawal | [0x764907…a886](https://dogeos-testnet.l2scan.co/tx/0x764907c41d388795073b92b89645997925a123a4b0ad1de19f968daa6722a886) |

Third-party components retain their existing notices and licenses. Making the repository public does not relicense those components or grant an additional license to the rest of the repository.
