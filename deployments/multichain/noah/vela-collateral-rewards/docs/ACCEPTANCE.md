# Acceptance record — initially unverified

Record build identifier, environment, wallet/network, timestamps, input, expected/actual result and evidence for each test. Mark pass/fail/unverified. Never use a fixture pass as proof of a genuine wallet flow.

| Area | Required check |
| --- | --- |
| Wallet | Approve, reject, wrong network, 45-second timeout, Cancel, Retry, change account, late approval/signature, reload; network selector recovers. |
| Amounts | Negative, zero, blank, malformed, excessive precision, above available/max; inline error, disabled Confirm and independent server rejection. |
| Borrow authorization | Borrow during an active run; explicit pause and demo authorization reason; restart only after new consent, no widened stop limits. |
| AI state | Stop success and card agree; late polling response cannot restore running; visible reason and resume action after risk stop/expiry. |
| Repay quote | $10.000001 debt displays $10.00; $10.02 authorization explicitly shows rounding plus $0.01 buffer; actual debt only is debited. |
| Precision | 100.009999 free demo USD prefills 100.00; exact balance remains intact; sub-cent remainder is disclosed. |
| Native rewards | Fund separate reserve; deposit; wait actual time; earn; claim; confirmed receipt and state readback; principal unchanged by claim. |
| Reward accounting | Late deposits, principal withdrawal, seizure, rate change, empty reserve, top-up, repeated claim, fractional dust, clock rollback; no retroactive or unfunded rewards. |
| Reward security | Owner and chain isolation, malicious receiver/reentrancy, failed transfer, unsolicited native tokens, paused state, double submission, reload/reorg. |
| Paper opening | Real paper order becomes a fill with entry price, size, fee and protective stop; pending/rejected orders are not fills. |
| Stops | Long and short, stricter limits, JP/future accounts, partial exits, existing-position reconciliation and inward rounding; fee-inclusive budget <= $2. |
| Paper closing | Partial then full close with actual fill, fees/funding and realized P&L; no invented fills on stale/unavailable depth. |
| Profit sweep | Explicit permission; new realized net profit only; interest first; principal second; no duplicate sweep on retry/restart; losses and earlier sweeps handled. |
| Negative cases | Loss-making trades, insufficient free equity, pending margin, funding history incomplete, expired/revoked permission, loan identity mismatch. |
| Settlement | Debt and balances reconcile; surplus/shortfall explicit; collateral stays locked pending required confirmation; bad debt persists. |
| LTV/HF | Interest increases debt; repayment improves health; price drops worsen it; stale data unavailable; sold collateral excluded exactly once. |
| Liquidation | Warning versus voluntary demo sale versus actual automatic action labeled separately; no claim of an unimplemented keeper. |
| Scope of results | Today's UTC trade count/wins/losses/P&L share one scope; cumulative results labeled separately. |
| Mobile | Every enabled control at 390px and desktop, readable units, no clipped dialogs, visible error/cancel/retry states. |

The user's earlier Chrome test had no fills. Opening, protective stops, closing, fees, P&L and settlement were NOT verified in that browser session. Preserve this status until actual evidence replaces it.

Suggested showcase: first prove a funded native test-DOGE reward claim; then prove the separate demo repayment flow. If a natural paper fill/profit is unavailable, demonstrate a clearly labeled deterministic replay and leave the public-paper result unverified. Do not force or fabricate a profitable trade.
