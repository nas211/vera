# Vela — DOGE lending on DogeOS

[Live website](https://defivela.xyz/) · [Lending dashboard](https://defivela.xyz/app/) · [Trading dashboard](https://defivela.xyz/app/trading) · [Existing Noah project](https://trynoah.ai/playground/6aeb352f7d19514dabedc93a)

Vela lets users deposit test DOGE as collateral, borrow test DOGE, withdraw their borrowed funds to a wallet, and repay their loan. Optional AI paper trading can support repayment through a simulated conversion and a separately funded test-DOGE reserve.

The current implementation is in **this directory**. Earlier code and documents remain as project history. See [the current release record](docs/VELA-CURRENT-RELEASE.json) for deployment identities and verification scope.

## Current product

- Native lending on DogeOS Chikyū testnet, chain **6281971**. Borrow up to **70%** of deposited DOGE collateral, subject to pool liquidity, at **5% simple APR**. Debt and collateral use the same asset. The 80% risk marker is informational; this market does not implement automatic liquidation.
- Borrowed DOGE is available to withdraw to the wallet. Withdrawal does not repay the loan. Wallet repayment, repayment from available borrowed funds, and collateral management use the reviewed on-chain contract.
- Separate Lending and Trading dashboards, mobile layouts, a profile menu and Settings, with persistent light/dark appearance.
- MetaMask and DogeOS Wallet/MyDoge connection choices. Mobile Chrome/Safari users can open Vela inside MetaMask; DogeOS Wallet also supports its SDK login flow. Sign-in is bound to the account returned by wallet approval, with signature/session checks and account-change invalidation.
- Scalp and swing strategy controls, Start/Stop, settings and open positions. Running traders must stop before switching strategies.
- Deposit confirmation updates the displayed status and balances. Reverted transactions show a failure rather than a success; receipt recovery does not resend a transaction.

## Trading and repayment scope

Market prices are live; trading balances, fills and the USDC-to-DOGE conversion are simulated. No real USDC or exchange orders are involved. With explicit, time-limited consent, eligible net closed-trade profit after fees, funding and losses is converted at **1 demo USDC = 1 test DOGE**. A separate project reserve supplies the actual test DOGE through `repayFor` on the lending contract. The lending pool and customer collateral do not fund these payments.

The worker requires a fully closed account with no pending orders and complete funding history. It persists the signed transaction before broadcast, retries uncertain outcomes with the same transaction, verifies canonical receipts, and refunds unused simulated cash exactly once. Limits are 1 DOGE per payment, 10 DOGE per wallet lifetime and 100 DOGE project-wide lifetime, further restricted by debt, earned profit, cash and reserve availability. Profit and repayment are not guaranteed.

Every account retains the stricter of its existing limit and a **$2 fee-inclusive per-position stop-trigger budget**. Account size, leverage, strategy and overrides cannot raise it. Tests cover long and short positions, inward rounding, partial exits and existing-position reconciliation. Slippage and funding can exceed this trigger budget.

## What Noah contributed

The same **VELA Collateral Rewards** Noah project and its build history are retained. Noah contributed the initial dashboard/rewards components; Vela reused and adapted those components and integrated them with its existing backend. The complete backend was not generated in Noah.

The owner reports that the browser extension saved the new landing, lending, trading and Settings design in that same Noah project. Desktop/mobile navigation and theme checks were reported. The [saved Settings preview](https://ta-01m6q79frp9mgfjn4gkgqztk1s-5173-ot8bowqvo9zyfoug81dv04649.preview.trynoah.app/settings) now includes Change picture and Remove, browser-only storage, and JPG/PNG/WebP validation up to 500 KB. Built-in lint reportedly passed; tests and a production build were not run because Noah exposes no shell. The checkpoint ID and latest ZIP export are unavailable. These are owner-reported results, not an independent inspection.

The Noah source still lacks a named DogeOS wallet option and approved-wallet identity checks; its wallet code was unchanged. Those features exist in Vela's connected implementation, but that does not establish their presence in Noah. The connected product is hosted on Vela's origin. An unconnected Noah preview must not invent account data or copy authentication tokens from Vela.

## Deployed and verified

Current frontend release: `vela-app-8a5ec923157b-6b872caca0cf`.

Backend fingerprint: `1fd2215cd96d7fb939716af7ba5ffb078abd011767d8052e514260d7400e9d1d`.

Native lending market: `0xB0D753703B1a207bC980b3667C007fd5c2F26573`.

The market is deployed and the project-funded lending pool was activated with 20 test DOGE. The separate repayment reserve was funded with 10 test DOGE. These are funding observations, not promises about the current remaining balances. The owner reports completing the lending cycle; independently observed native receipts also establish lending actions.

Recent checks include 46 wallet/authentication tests, 25 mobile/loan/theme tests, 13 packaging tests, TypeScript and build checks, 10 desktop/mobile lending browser scenarios, and public-browser rejection of account mismatches. Repayment logic passed 12 policy/API tests and a local EVM integration with actual local contract repayment, uncertain-broadcast recovery and revert/refund cases. These runs were performed at different stages; they are not a claim that every historical suite was rerun together.

Public profitable AI trading followed by automatic native repayment remains unverified and its observation was deferred by the owner. Physical mobile wallet approval and real exchange stop protection remain unverified. Public browser and simulated-provider checks are distinct from those observations. Publishing this source does not redeploy services or establish new live protection.

The latest connected-bundle dependency exposure check passed after scoped dependency fixes. The wider repository/tooling dependency tree still has advisories. This is not an independent security audit.

## Run locally

Use Python 3.11+ and Node 22.12+:

```bash
bash tools/setup_judge_demo.sh
.venv/bin/python tools/run_judge_demo.py
```

Open the local URL printed by the runner. The judge demo uses isolated simulated accounts; it does not deploy contracts or spend wallet funds. The public app is the connected DogeOS experience.

Selected checks:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_demo_doge_repayment.py tests/test_doge_lending_reader.py tests/test_strategy_profiles.py tests/test_noah_testnet_release.py -q
node --test site/checks/unit.test.mjs site/checks/wallet-discovery.test.mjs site/checks/wallet-events.test.mjs
cd site/vela-dashboard
npx vitest run src/lib
npx tsc --noEmit -p tsconfig.app.json
npm run build -- --mode testnet
```

Solidity sources and tests are under `contracts/`; use the compiler and Foundry configuration pinned there. Deployment/activation scripts are operator tools and are not part of the normal user or judge setup. Never run them merely to browse the project.

No live database, wallet key, session token, operator log or private environment file is included. Third-party components retain their existing licenses and notices.
