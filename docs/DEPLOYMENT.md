# Vela deployment and verification

Updated October 20, 2026. Prepared code, local tests, published website,
actual practice execution, and on-chain execution are separate evidence.

## Current application

The default `/mydoge.html#ai-portfolio` runs the independent practice portfolio.
Its balance is virtual, trades use live public prices, and START/STOP use zero
wallet transactions. Optional MyDoge/email/Google/MetaMask connection remains.
A browser capability controls only this practice book; it is not wallet ownership.
The existing dark/gold design, three products and separate `/earn` are retained.
No repetitive paper-trading banner was added; the app explains virtual balances
and non-withdrawable simulated returns.

A genuine model/live-price/browser check passed: research, review and direction
approved BTC/USD long; the engine opened it and STOP closed it. That one session
finished at **−0.036068 simulated USD**, including simulated entry/exit fees.
Reload preserved the session and mobile layout passed. **Zero wallet transactions**
were used. The engineering session is excluded from leaderboard results.
See `snapshot/practice-e2e.json`; this is not a performance backtest.

The dedicated OpenAI key is configured only in Vela's runtime environment.
`gpt-5.6-luna` returned an actual validated model response. No WHY credentials
were read or reused. Research requires complete closed candles; missing bars
are repaired from public history. Model approvals are bound to a session,
expire after five minutes, and never fall back to an unreviewed rule signal.

## Website and domain

Public URL: **https://defivela.xyz**; generated origin:
https://defivela.uuuiiaaaa.chatgpt.site. Audience stays public as authorized.
DNS, provider and HTTPS had been verified active. Only this site's files and
new Vela services are changed. Published version 9 includes both MyDoge and MetaMask entry pages.
Source: `16f2d3b4551e4ff5d74aef9cd4fb3505d96b2b20`.
Saved version: `appgprj_6ad757d1f93c8191bc736d87ada530be~appgver_33e8170b4df88191b2ba9685666b6822`.
Successful deployment: `appgdep_6ad7ab6fa1088191a7e43203d8940adf`.
The actual custom-domain page returned HTTP 200, START enabled, no horizontal
overflow and zero JavaScript errors in a fresh mobile Chrome session.
Sites project: `appgprj_6ad757d1f93c8191bc736d87ada530be`.

## Fee incident and paused chain execution

The original 42.069 DOGE reconciled to:

| Location / cost | DOGE |
| --- | ---: |
| Wallet | 1.960864137251449715 |
| V1 pool, held as WDOGE | 10.0086420521760787 |
| Network fees, all 100 outgoing transactions | 30.099493810572471585 |

Deployment fees were 19.315033769726285299 DOGE. The 94 price writes cost
9.256152484313953066 DOGE; 90 were idle writes costing 8.125521127326736019 DOGE.
The remaining five actions cost 1.528307556532233220 DOGE. Missing data/finality
fees in the old estimate and idle oracle writes caused this cost. It is not
trading loss. Reconciliation left zero unexplained wei. Nonce was 100, no
pending transaction, active sessions zero. No new chain transactions were sent
while implementing these fixes.

Idle writes are removed. Chain writes default OFF, full fee receipts are
tracked, and per-transaction/rolling-day estimate gates fail closed. Practice
execution has a separate DB and never creates an operator outbox entry.

VelaVaultV2 is prepared and locally tested, not deployed. Signed START prices
and settlement prices eliminate recurring on-chain updates. A guarded pool
withdrawal works only after pausing and settling every V2 session. V1 cannot
be upgraded and has no direct pool withdrawal. The V2 withdrawal cannot recover
V1 pool funds. A deployment dry run estimated 36.451851792369962275 DOGE;
no deployment was submitted against the 1.960864137251449715 wallet balance.

## Historical V1 on-chain verification


| Contract | Address |
| --- | --- |
| VelaVault | `0x595e36cDD915db23b563b26fAe575B7Ff6066d3f` |
| WDOGE | `0xc4d9A03B101a5d11894c9DE9Da49A5d9F88845C5` |
| TUSDC | `0xAC9cED37e9F23f3effDf09B505b2F7809416e1E0` |

All are on chain **6281971**, verified through the official RPC. The pool was
prefunded with **10 faucet DOGE**. Operator/owner is the dedicated test signer
`0xF8b7120F466f39063398fdc62F5f8cBCe37Ad1dB`.

| Operation | Explorer receipt |
| --- | --- |
| Deploy | [0x475f…b907](https://dogeos-testnet.l2scan.co/tx/0x475f5d09f55b0c5f1f265eff8ee6dd7d4217df0d4e628886f09a04bd31e7b907) |
| Fund pool | [0xa237…486f](https://dogeos-testnet.l2scan.co/tx/0xa2371ba05946df1d1ae1aef6b2f5cfdfdd2650f323852d8e65d7a057d355486f) |
| START | [0x9561…72fc](https://dogeos-testnet.l2scan.co/tx/0x9561c60e4ff4bddfae7785a9ee9e8ba1c985ad475edb2fd8a89e274dc0a572fc) |
| TradeLogged | [0xa16d…f2c2](https://dogeos-testnet.l2scan.co/tx/0xa16d71cdb4fdc23dc623eea0b0e3c6c28d0586d75b02b7422a1d4090f837f2c2) |
| STOP | [0x8ad2…e7c7](https://dogeos-testnet.l2scan.co/tx/0x8ad2d8d22b61ef7f1f11daf8e27780c9d8bc8e71bd272d6f3994f2cae49fe7c7) |
| Settlement | [0x1273…d22c](https://dogeos-testnet.l2scan.co/tx/0x127360776176ddeee207b852c28ae9251c7b36bdd7f7f026c777cddfe86ad22c) |

The verification session deposited **5 test DOGE**, opened and closed one paper
DOGE position using live prices, logged it on-chain, requested STOP, and received
**4.9913579478239213 test DOGE**. That session's net trading P&L was
**−0.000244 TUSDC**, including entry/exit fees. The DOGE amount also reflects the
changed conversion price; user gas is separate. Contract session returned to
Idle and the trade ID is recorded on-chain. Full hashes are also in
`snapshot/testnet-e2e.json`.

This was an explicitly labeled engineering signal signed directly by the test
account, not evidence of an AI-generated trade or real MyDoge mobile approval.
The check is excluded from strategy leaderboard returns.


## Verification and limitations

- Foundry: **26 passed** (20 V1, 6 V2, including 256 fuzz iterations).
- Backend: **36 passed**, run in groups: 33 unit/API and 3 local-EVM tests.
  Includes no-gas practice isolation, token separation, duplicate start, stale
  close refusal, candle-gap repair, session-bound AI review, fee reservation,
  V1/V2 round trips and replay/reorganization handling.
- Browser: practice real-model/live-price round trip, full-fee wallet fixtures,
  retained vault flow fixtures, MetaMask fixtures and real MyDoge login modal
  passed. V2 remains a local-only result. See `snapshot/current-verification.json`.
- Actual owner MyDoge mobile/email transaction approval is still untested.
  The SDK login screen and mock single-signature tests passed. Practice START
  does not need transaction signing, so the hosted signing error cannot block it.
- The requested four-digit email code/template is controlled by MyDoge's hosted
  login service, not this frontend; it was not changed or truncated.
- Pyth requires its own API key. Binance public crypto fallback is working;
  XAU remains unavailable. No missing price is fabricated.
- Lending/Borrowing, real staking, yield payouts and ZK privacy remain unimplemented.
  `/earn` is an illustrative calculator. Current blockchain records are public.
- The API uses a temporary Cloudflare Quick Tunnel. Transient Vela services and
  runtime secrets require reinitialization after a host reboot. No secret was
  written into source. The key pasted into chat should be rotated.
- Existing SDK dependency advisories remain documented in `site/README.md`.
  Neither contracts nor wallet SDK are claimed independently audited.

## Isolation and risk

No WHY, forex/MT5, live exchange accounts, original services, orders or stops
were modified. Live protection was not reverified. The historical failed shadow
service was not repaired here. All simulated positions retain the fee-inclusive
maximum $2 stop-trigger budget, preserving stricter limits; gaps/slippage can
exceed that trigger budget. Dashboard P&L is current-session; leaderboard P&L,
counts and win rate are all-time closed trades from the same set.

Repository: https://github.com/nas211/vera. Upload verification compares root
trees and recursive paths, modes and blob hashes. Local and remote commit IDs
can differ when the Git Data API generates metadata separately.
