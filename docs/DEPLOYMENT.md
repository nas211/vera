# Vela deployment and verification

Updated October 20, 2026. Code, tests, deployed testnet behavior and actual
mobile-wallet approval are distinct verification scopes.

## Website and domain

The updated site is published at **https://defivela.xyz** and
https://defivela.uuuiiaaaa.chatgpt.site. The owner explicitly authorized public
access after the private site blocked ordinary browser access. Domain status,
provider status and HTTPS certificate are all `active`. The owner changed
Spaceship DNS; no unrelated DNS records were changed by this agent.

Sites project: `appgprj_6ad757d1f93c8191bc736d87ada530be`.
Published source: `7d57ea3d8032a57a38fa4ccfe7fd90080f8df4fc`.
Version: `appgprj_6ad757d1f93c8191bc736d87ada530be~appgver_671473117f7881919c298780a90f7293`.
Successful deployment: `appgdep_6ad798bd6af8819189fbec0b03e54658`.
`site/` contains source, generated static assets, dependency locks and checks.

A fresh Chrome session fetched both the homepage and `/mydoge.html` from
`defivela.xyz` with HTTP 200. The hosted page reached the backend with HTTP 200
and `ready=true`, with no JavaScript errors. The official MyDoge login modal
also opened on the actual domain.

The homepage keeps the original dark-and-gold design and hero. Its three product
labels are AI Trading, Lending & Borrowing, and Staking. Lending & Borrowing is
Coming soon. Staking opens a dedicated `/earn` page with the illustrative APY/rewards calculator; staking
contracts and ZK privacy are not implemented. Current vault transactions are
public. The opening four-box row and visible paper-trading labels were removed;
the main action says Start earning and opens `/earn`. `/mydoge.html` is the actual
TESTNET + PAPER ONLY dashboard. It connects through official SDK 4.0.4 with email, Google and wallet login (X removed), checks
DogeOS Chikyū, displays test-DOGE balance/faucet, and requests one START or STOP
transaction. Backend ledger events drive active/stopping/settled states.

## Deployed contracts and confirmed transactions

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

## Tests and running services

- Foundry: **20 tests passed**, including 256 fuzz iterations, permissions,
  stale prices, pause/exit behavior, double start, pool caps, other-user reserves,
  withdrawal failure, reentrancy and mint/burn restrictions.
- Backend: **24 unit tests plus 2 local-chain integration tests passed**.
  Coverage includes both stop directions, fees, partial exits, stricter limits,
  persisted-position reconciliation, price gates, no live adapter, event replay,
  signed outbox recovery, settlement, reorganization halt and secret-header scope.
- Copied execution core: **41 offline tests passed** again. Mock venues only,
  no network, writes confined to a temporary directory.
- Browser: landing, MetaMask, MyDoge, email-signing, injected-wallet and paper-dashboard suites passed, including
  responsive layouts, network/account changes, cancellation, single START/STOP,
  pending reload, outage exit and settlement links. Providers are mocked.
- Real MyDoge service check: official login modal opened with no JavaScript
  errors. Actual mobile login/signing remains untested.

Only NEW `vela-testnet-api`, `vela-testnet-worker` and `vela-testnet-tunnel`
units were started. API, prices, event listener, operator and paper engine report
healthy. SQLite and signed transaction outbox stay in ignored `.runtime/`.
Operator key is process environment only; it is never in frontend or Git.
The key was disclosed in chat and is suitable only for this disposable testnet.

## Remaining limitations

- Actual MyDoge mobile/email approval on DogeOS must still be tested by its owner.
  The owner reported the hosted send error after email login. The new path asks
  for one signature, verifies its payload and broadcasts directly to the chain
  RPC. Local signing fixtures pass; that does not prove real email approval.
- The unified AI Agents portfolio includes existing signal and risk code.
  Its research model needs dedicated `VELA_MODEL` and `VELA_MODEL_KEY`.
  The UI reports the disconnected model. Once configured, review cannot be
  bypassed by falling back to a rule entry.
- Pyth's August 26, 2026 upgrade requires an API key; the requested no-key API
  now rejects price-update requests. Binance crypto fallback is working. XAU
  stays unavailable without a fresh Pyth source. Configure `VELA_PYTH_API_KEY`.
- The current public API uses a Cloudflare Quick Tunnel. Its address can change
  on tunnel restart. New transient services and operator environment need
  reinitialization after a host reboot. Permanent hosting/secret injection
  remains an ops task; see `docs/TESTNET.md`.
- TUSDC is operator-priced paper accounting, not real USDC or a DEX trade.
  Settlement trusts the operator and is capped by available test pool funds.
- Contracts and SDK are not independently audited. The pinned frontend dependency
  tree reports 52 advisories (9 low, 21 moderate, 22 high); see `site/README.md`.
- The interface has no real-money execution, real yield, rewards or bonuses.

## GitHub and isolation

Private source repository: https://github.com/nas211/vera. The complete local
committed tree is uploaded via the Git Data API; unchanged copied components
are preserved. Remote and local commit IDs can differ because commit metadata
is generated separately. Matching root-tree hashes and recursive file paths,
modes and object hashes verify identical source. The upload verification record
is retained under ignored `.git/vera-upload/verification.json` after completion.

No WHY, forex/MT5, live exchange accounts, original services, source, orders or
stop protection were modified in this implementation. No live protection was
reverified. The earlier shadow-service failure is historical evidence only:
its prior journal reported `candle_schema_or_history` / `shadow_market_errors`;
this task did not investigate or repair it.

Every paper position retains the universal **$2 fee-inclusive maximum stop
trigger loss budget**, preserving stricter limits. Slippage and gaps can exceed
that trigger budget. Today's performance and cumulative performance are not
mixed: dashboard statistics are current-session; leaderboard is all-time closed
strategy trades with counts and win rate from that same set.
