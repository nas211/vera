# Vela competition portal

`/trade` adds wallet authentication, demo-USDC funding and a simple AI Managed
dashboard to the existing paper engine. The homepage keeps its existing layout,
CSS, artwork and animation script. Its wallet controls and AI Trading links now
enter **`/trade`**; outdated vault/settlement copy describes the demo instead.
`/earn` serves the existing staking calculator. The original `vela.api:app` now
hosts this portal and the existing testnet API together. `/mydoge.html` remains
available for compatibility; the homepage onboarding continues to use `/trade`.
The original worker and testnet database remain independent of the paper ledger.

## Local startup

```sh
cd /root/Vera
npm run build --prefix site
VELA_PAPER_DB=/root/Vera/.runtime/lighter-showcase/competition/paper.sqlite3 \
  .venv/bin/python tools/run_lighter_paper.py \
  --model-env .runtime/lighter-showcase/model.env --port 8791
```

Open `http://127.0.0.1:8791/`. The launcher binds to loopback and sets the exact
authentication origin. The private model file must be mode 600 and contain only
`VELA_MODEL`, `VELA_MODEL_KEY`, and optionally `VELA_MODEL_ENDPOINT`. No provider
secrets enter the frontend, API responses or diagnostics. Use one server worker
per database, beneath `.runtime/lighter-showcase/`.

Direct uvicorn users must set `VELA_PORTAL_ORIGIN` to the exact browser origin;
the integrated API defaults to `https://defivela.xyz`. `localhost` is a different
origin. The existing Sites proxy authenticates forwarded public-origin headers
with a server-only shared token. See [integration and rollback](INTEGRATED-DEPLOYMENT.md).

## Visitor flow

1. START TRADING opens exactly two wallet options. MetaMask uses EIP-6963 and
   EIP-1193. MyDoge displays exactly **Coming Soon — Mobile Only** and is disabled on desktop.
   Mobile loads the existing SDK only after selection and explicitly states that
   actual mobile approval is unverified. Use MetaMask for the competition demo.
2. After an approved network switch to Chikyū, an off-chain signature proves
   ownership. A verified wallet retrieves its persistent, initially empty account.
3. Deposit adds simulated USDC, the only displayed funding asset. Unsupported
   assets and conversion routes are hidden. Wallet test-DOGE balance is separate. No transaction, approval or conversion occurs.
4. Start Trading checks the session, collateral, fresh markets/history, model
   connectivity and risk engine before enabling AI Managed.
5. The existing research → review → direction pipeline proposes and approves
   signals. They must pass the existing evaluator and risk-sized paper execution.
   Abstention, delay and veto are valid; the application never forces a demo trade.
6. Stop Trading immediately invalidates pending approvals and cancels entry
   orders. Existing positions remain managed. **Close all** requires separate confirmation.
7. Withdraw removes simulated funds only when trading is paused and exposure and
   pending orders are gone. Reset requires an idle account and confirmation;
   complete prior ledger records are archived and transfer history is retained.
8. Disconnect revokes the server session and pauses entries. Account/network
   changes clear the view and revoke its session. Expiry cancels pending entries;
   risk management continues. A failed logout remains visible for retry.

Dashboard, Trades, History and Account use Vela branding and mobile bottom
navigation. Tab changes use browser history, including Back/Forward and reload.
Dialogs use explicit auto margins and viewport bounds to override the SDK CSS
reset. Manual orders, leverage, cost details and execution assumptions are collapsed
by default.
Customers never configure exchange credentials or AI providers.

## Authentication, funding and P&L

The server generates an EIP-4361-shaped message with a random one-use nonce,
checksummed address, chain 6281971, configured origin and five-minute expiry.
It verifies the exact stored message's EIP-191 signature. EOA signatures are
supported; EIP-1271 contract-wallet verification remains unimplemented.

The random 24-hour session token is in an HttpOnly, SameSite=Strict cookie,
scoped to `/api/portal`, with Secure on HTTPS. Only its SHA-256 digest is stored.
Writes require the exact Host, Origin and a custom request header. Private API
responses are `no-store`; challenges are rate limited and consumed after one attempt.

Wallet accounts use `wallet:<address>` in the isolated paper database. Legacy
wallet-free accounts use a distinct capability-derived `paper:` identity. Neither
touches the on-chain ledger or WHY accounts. Demo transfers atomically update
balance and net contributed capital, so cash flows do not become profits.
Per-user idempotency keys prevent retry credits and reject different actions or
amounts under the same key. Withdraw/reset inspect all pending orders, including
orders older than the API's latest-30 display window.

Today's realized P&L, counts, wins and losses share the UTC day. All-time closed
fills are separately labeled. The dashboard's all-time P&L includes open mark
P&L; today's headline uses closed fills only. Partial exits are separate closed
fills. The recent-equity chart shows observed values and its auto-scaled endpoints.
Engineering accounts are never advertised as an AI performance record.

## Execution and risk

BTC/ETH/SOL use the existing public Lighter feed: sequenced depth, specifications,
mark/index prices, funding estimates and closed candles. Reconnection/freshness
gates remain active. Fills use observed depth, configured latency and adverse
slippage. Stale data never produces invented fills.

AI-opened positions receive bounded direction reviews after 30 seconds, at most
once every two minutes per position, sharing the existing daily model budget.
Only `exit` can queue a reduction. Hold, open, flip, provider errors, stale
responses or changed positions cannot increase exposure or change stops. Manual
positions retain the existing numerical exits. Stop/liquidation processing runs
independently of model I/O and takes priority over model reductions.

Every account retains a maximum **$2 per-position stop-trigger budget including
trading fees**, preserving stricter limits. Account size, leverage, strategy and
account labels cannot increase it. Long stops round inward/up; short stops round
inward/down. Paid fees and partial realized P&L are included; persisted positions
are reconciled every cycle. This is **not** a guarantee against slippage, gaps,
funding or data outages, and creates no exchange stop protection.

Default maker, taker and Vela fees are each 0%, configurable simulation assumptions.
Spread, slippage, depth impact and funding are separate. Funding is prorated during
observed intervals and gaps are flagged. Maker queue and liquidation behavior
remain approximations. See [the engine runbook](LIGHTER-PAPER.md).

## Official wallet and funding research

Sources checked on 2026-10-21:

- [DogeOS native wallets](https://docs.dogeos.com/en/sdk/embedded-wallets) document
  SDK 4.0.4's native-first MyDoge flow on a top-level dApp page. Native signing
  must not depend on embedded readiness. The portal uses this SDK, with no guessed
  MyDoge deep link or Chrome extension URL.
- [DogeOS workflows](https://docs.dogeos.com/en/sdk/guides) cover accounts, networks,
  message signing and balances. The portal verifies ownership and reads native
  testnet DOGE. It submits no transactions.
- [MetaMask signing](https://docs.metamask.io/metamask-connect/evm/guides/sign-data/)
  supports `personal_sign` for off-chain authentication. No `eth_sign` or spending
  authorization is requested.
- [Lighter funding](https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals)
  documents direct Ethereum deposits, CCTP and a Fun.xyz universal-deposit API
  requiring a builder key. Its documented chains do not include DogeOS or Dogecoin.
  No executable DogeOS-to-Lighter collateral quote was verified.
- [Chikyū configuration](https://docs.dogeos.com/en/developers/developer-quickstart)
  identifies chain 6281971 and native DOGE. The earlier Vela testnet vault remains
  separate and publicly paused. Its historical testnet hashes do not verify this
  portal's mobile authentication.

Real deposits, swaps, bridges, payouts and LIVE execution remain disabled. Future
funding requires verified compatibility, executable quotes, settlement reconciliation,
an approved custody design and explicit authorization. Vela does not claim Lighter
trades execute or settle on DogeOS. Commercial launch also requires production
authentication/recovery, multi-instance coordination, abuse limits, quotas, backups,
monitoring, SDK dependency review and security review.

## Validation

```sh
# Chain regressions use only local Anvil, chain 31337.
.tools/foundry/anvil --host 127.0.0.1 --port 18545 --silent
.venv/bin/pytest backend/tests -q
python3 tools/check_execution.py
npm run build --prefix site
cd site
PREVIEW_URL=http://127.0.0.1:8791 node checks/portal.cjs
PREVIEW_URL=http://127.0.0.1:8791 node checks/portal-mydoge.cjs
PREVIEW_URL=http://127.0.0.1:8791 CHROME_PATH=/opt/google/chrome/chrome node checks/browser.cjs
# Optional actual model check; abstention is accepted:
PREVIEW_URL=http://127.0.0.1:8791 PORTAL_REAL_AI=1 node checks/portal.cjs
```

Browser checks use **mocked providers/SDK hooks with genuine EIP-191 signatures**,
the actual backend, and live public market data. The opt-in check uses the real
configured development model. Deterministic backend fixtures cover both directions,
partial exits, stop reconciliation, auth/session races and reduction-only AI exits.
Actual MyDoge mobile and MetaMask-extension approval remain unverified.

The following results describe the earlier implementation, not fresh verification
of the final UI fixes. Exact results: [competition validation](../snapshot/competition-validation.json).
The full backend suite passed **119 tests**, and the isolated inherited execution
suite passed **41 tests**. The frontend build, final portal journey, mobile SDK
hook checks, original homepage and wallet-free paper regressions passed. The real
`gpt-4.1-mini` check completed research/review/direction and opened a BTC long paper
position using live public prices; its engineering agent was paused and the
position closed afterward. This is integration evidence, not strategy performance.
Prepared code, tests and a loopback preview are separate from production deployment
and live protection. No WHY services, accounts, credentials or live stops changed.

## Changed files

| Files | Purpose |
| --- | --- |
| `backend/vela/portal.py` | Wallet sessions, demo transfers, controls and account API |
| `backend/vela/lighter/app.py`, `monitor.py`, `engine.py` | Paper runtime, entry cancellation, expiry and AI exits |
| `backend/vela/agents.py` | Optional market/eligibility/revision gates; stale responses cannot restart entries |
| `site/src/portal-entry.jsx`, `portal-wallet.js`, `portal-mydoge.jsx` | Dashboard, authentication and mobile SDK bridge |
| `site/dist/portal.html`, `portal.css`, `assets/portal*` | Static portal entry, responsive styles and bundle |
| `site/dist/index.html`, `earn.html`, `wallet.js` | Shared demo onboarding and corrected paper-only copy; original styling/animations retained |
| `site/scripts/build-wallet.mjs` | Separate portal build; existing bundles retained |
| `backend/tests/test_portal.py`, `site/checks/portal*.cjs` | Backend, browser and mobile-hook verification |
| `tools/run_lighter_paper.py` | Loopback launcher sets the exact auth origin |

The previous Lighter implementation was already uncommitted when work resumed.
It is preserved; its inventory is in [LIGHTER-PAPER.md](LIGHTER-PAPER.md).
