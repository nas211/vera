# Paper, earn and DogeOS deployment — 2026-11-03

`https://defivela.xyz/paper` and `/earn` now serve working demo products. `/trade` continues to redirect to the existing `/trading` portal. The homepage's nine served files remain byte-for-byte unchanged. DogeOS is featured in the existing portal selector and its read-only testnet vault heading; all seven networks remain available. No layout, navigation flow or financial activation was changed by that copy update. No claim of being the first DogeOS lending protocol is made.

## Implementation

The paper desk reuses the original desk interface and the current single backend ledger, Lighter feed, three-stage AI pipeline and independent risk engine. Its random browser capability is separate from signed wallet sessions. Initial 10,000 demo USDC is allocated once per desk, with bounded account creation and order limits. Manual long/short market and limit orders, cancellation, partial/full closes, history, charts, AI start/stop and stream reconnection are implemented. Stopping AI leaves manual paper access and protective position management available; wallet-account stop rules are unchanged. This is not real USDC or live exchange execution.

Earn now attaches wallet-authenticated deposit, reward accrual, claim and withdrawal to the existing demo staking ledger. The assumed simple annual rate is 5%, subject to a shared simulated reward pool. The separate hypothetical APY calculator does not set that rate. Rewards are separate cash flows and do not inflate trading P&L. Principal is limited to 10,000 demo USDC; existing debt and trading exposure block deposits. No staking contract, token transfer, funded reward or guaranteed return is activated. The public factory explicitly enables demo staking; the library configuration remains opt-in and the environment flag can disable it.

DogeOS contract information remains read-only. Customer contract deposits, borrowing, settlement and transfers remain disabled. Other supported network experiences retain their existing simulated lending accounts. MyDoge remains disabled; real device approvals were not performed. ZK/privacy is excluded.

## Executed verification

- Full backend suite: **283 passed**, three dependency deprecation warnings. Covers authentication, idempotency, ownership, model failures, fee-inclusive long/short risk, partial exits and restart reconciliation.
- Local desktop/mobile browser journeys: manual paper fills, partial/full closes, persisted history, offline/reconnect, signed fixture-wallet staking, reward claim, withdrawal and wallet reconnect passed at 1440px and 390px. Prices, model and wallet approvals were fixtures.
- Public HTTPS acceptance: real Lighter prices, manual long/short simulated fills, partial/full closes, limit cancellation, duplicate-request protection and persistent ledger passed. Each QA position used a stricter $0.75 fee-inclusive stop-trigger budget. QA positions were closed and AI left disabled.
- Public signed EOA authentication, demo allocation/staking/claim/withdrawal, logout/reconnect and history passed. This used a disposable software signing key, not a physical wallet approval. QA principal and available balance were returned to zero.
- Both public market WebSockets passed. Twelve public desktop/mobile page checks passed without JavaScript errors or horizontal overflow; all seven network options remained visible. The route monitor passed all **26** cases.
- `pip check`, Nginx configuration validation, backend readiness and operational health checks passed. Daily backups and two-minute health monitoring remain enabled.

Funding accrual retains Decimal precision; public balance-versus-fill reconciliation uses a tolerance of 1e-18 USDC, far below micro-USDC. No result was inferred merely from a successful build. Evidence is in `snapshot/paper-earn-20261103.json`.

## Deployment and rollback

The authoritative source remains `/root/VellaMultichain`. A fresh WAL-aware backup passed integrity and isolated restoration checks. Backend release `29274943fd7dbd411a83daf14f5d984e3ef0657c` passed source reconciliation before a controlled restart. One pre-existing paper position and all ledger identities/history were preserved. No database was replaced, migrated or opened by a second engine. Other observed service PID/state pairs, including WHY, remained unchanged.

The release overlay and rollback are under `/root/vela-completion-20261103/release-paper-earn-backend`; the guarded deployer records its private recovery path beneath the active application's `.runtime/deployments`. Open paper exposure requires the explicit `--preserve-paper-positions` option, which still rejects pending orders and verifies exposure unchanged while stopped. This option never enables live execution.

Additive static roots are `/var/www/defivela-products/paper-earn-20261103` and `/var/www/defivela-portal/dogeos-20261103`. The portal build applies only three reviewed text replacements to the verified existing bundle, retaining all shared CSS/assets. Its preparation script refuses a changed source hash. The exact domain configuration is recorded in `ops/nginx-defivela.xyz.conf.pending`. Restore the privately backed-up prior Nginx configuration, validate it and reload Nginx to roll back page routing. Backend rollback is code-only; never restore an old ledger over current trading state.

## Remaining security limits

The current full prepared frontend dependency audit reports **15 high, 27 moderate and 62 low** findings, largely through the disabled DogeOS/embedded-wallet SDK dependency tree. This is unresolved and must not be described as a clean security audit. The new paper/earn build explicitly checks its bundled dependencies and excludes that SDK; the existing portal's code is unchanged apart from the three text replacements. No vulnerable SDK is enabled to make the feature appear complete. Backend hosted advisory validation is recorded separately.

No independent security audit, genuine mobile-wallet approval, real exchange protection, financial-contract deployment or real-money operation is claimed. The maximum fee-inclusive stop-trigger budget remains $2 or stricter; slippage, gaps and funding can produce larger realized losses.
