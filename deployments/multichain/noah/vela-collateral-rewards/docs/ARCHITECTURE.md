# Vela collateral rewards build boundary

The intended deliverable is a Noah-built reward-enabled testnet vault and dashboard connected to the existing Vela demo loan and paper-trading services. This kit is the scaffold; the contracts, integration and live payout remain to be implemented and verified.

## Two explicit money paths

1. Faucet test DOGE → separate reward reserve → checkpointed earned reward → owner claim → confirmed native wallet transfer.
2. Demo USD loan → authorized paper execution → realized net trading profit → authorized repayment sweep → accrued interest → loan principal → eligible settlement.

Collateral principal backs the loan and is not the reward reserve. A reward claim is not trading profit. No automatic conversion between DOGE and USD is specified. Real loan-funded trading and real stablecoin issuance are out of scope.

## Contract accounting

Use one owner and one funded reserve per new vault. Record principal, reserve, accrued liabilities, fractional carry, APR and last checkpoint. Accrual is principal × APR × actual elapsed seconds, capped by reserve minus accrued liabilities. Before changing principal, rate or reserve, checkpoint the previous state. After reserve exhaustion, advance the timestamp without creating unfunded liabilities; a later top-up funds only future accrual.

Native contract balance must cover principal plus the reward reserve. A claim reduces accrued liability and reserve together; it never reduces principal. Direct unsolicited token transfers must not automatically become eligible collateral or rewards. Define a conservative accounting policy and test it. Owner withdrawals must satisfy loan locks in addition to balance checks. Collateral seizures must checkpoint first and affect future eligible principal. Prevent reentrancy and duplicate claims; failed transfers revert state atomically.

`src/reward-model.mjs` is an exact-integer accounting reference. It has no access control, chain integration, settlement engine or real transfers. Its tests cannot establish smart-contract security. The Solidity interface intentionally has no deployable implementation.

## Existing Vela integration

Reuse Vela's authoritative services. Existing paths include `/api/account`, `/api/repay`, `/api/repayment/policy`, `/api/trading/start` and `/api/trading/stop`. Read actual request schemas and authentication before implementing an adapter. Simulated vault operations include the expected wallet/network, vault and loan context. Financial writes use idempotency keys. Do not add balances directly from Noah.

Vela's existing Earn service is a separate demo staking ledger, not collateral rewards. It cannot be advertised as native DOGE yield. Existing deployed native vaults have no reward hooks; the proposed new vault requires a separately reviewed deployment and onboarding plan. Native reward reads can be standalone, but a functioning Vela loan adapter must use an approved authentication boundary. Default to fixture mode when that boundary is absent.

## Loan health and liquidation

Current debt includes principal and accrued interest. Calculate risk using effective collateral after any booked but unreconciled sale, and remove that adjustment after canonical settlement. Reject stale/missing prices for new borrowing and entries. Show quote source and age. The current illustrative prices are not a production oracle.

The current owner-authorized demo collateral sale covers a settlement shortfall; it is not an autonomous on-chain liquidation engine. A health factor below one indicates a threshold breach, not proof a sale happened. An optional auto-reduce policy also needs independent review: stopped entry preferences must not accidentally disable separately authorized risk actions, and stale quotes must never trigger forced settlement. These are audit items for the build, not verified capabilities.

Every position retains the stricter of existing limits and a $2 fee-inclusive stop-trigger budget across all accounts. Protective exits remain independent of whether new AI entries are enabled. Preserve stricter existing settings and reconcile old positions. The trigger budget is not a slippage/funding guarantee.

## Evidence and release

Track four columns: prepared code, tests, deployment, verified live protection/payout. Never infer a live change from edited code. Never label fixture receipts as blockchain receipts. On-chain success requires chain/owner/address/code checks, successful receipt, canonical block and at least the configured confirmations, plus readback of contract state. Wallet balance verification includes gas and concurrent transactions; inspect exact contract events/transfers rather than expecting a simple balance delta equal to the reward.
