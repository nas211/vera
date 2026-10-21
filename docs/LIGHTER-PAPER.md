# Vela public-market paper showcase

The `/paper` desk adds BTC, ETH and SOL perpetual paper trading to Vela's existing
graphite/gold website. It uses Lighter public market data, the existing Vela
research/review/direction agents, the existing strategy evaluator, SQLite ledger
shape, execution interface and universal stop module. The wallet/testnet portfolio
retains its existing endpoints in the same application. The paper desk does not require a wallet, Lighter
account, private signing key, deposit, chain transaction or exchange order.

## Start locally

Use the repository's existing Python virtual environment and frontend dependencies:

```sh
cd /root/Vera
npm run build --prefix site
PYTHONPATH=backend VELA_EXECUTION_MODE=PAPER VELA_CHAIN_WRITES=false \
  VELA_PORTAL_ORIGIN=http://127.0.0.1:8790 \
  .venv/bin/uvicorn vela.api:app --host 127.0.0.1 --port 8790
```

Open `http://127.0.0.1:8790/paper`. The homepage now enters the wallet-authenticated `/trade` portal.
The wallet-free desk remains available directly at `/paper`; see
[the competition portal runbook](COMPETITION-PORTAL.md).
Use one process/worker per paper database; an exclusive file lock rejects a second
engine. The original API's lifespan starts the paper tasks; there is no additional
backend service. `vela.worker` keeps managing only its existing testnet ledger.
The existing Sites project forwards API and WebSocket traffic to this integrated
API on the existing port 8787. See [integration and rollback](INTEGRATED-DEPLOYMENT.md).

The default isolated database is
`.runtime/lighter-showcase/paper.sqlite3`. `VELA_PAPER_DB` can select another file
**only beneath `.runtime/lighter-showcase/`**. No existing account ledger is
read, copied or changed. The browser creates a random 256-bit capability, sends
it in `X-Vela-Paper` headers and retains it locally. Public market streaming has
no account credentials. An account starts with $10,000 of virtual USD, zero
positions and zero results. Accounts persist across reloads/server restarts.

## Demonstration

1. Open the desk and wait for **Market data live**.
2. Select BTC, ETH or SOL; observe mark price, depth, funding and real closed
   five-minute candle history.
3. Choose **Analyze & start AI**. With a configured development model this enables
   the original research → review → direction pipeline for the selected market.
   Its approved signal is checked again and queued into paper execution. A hold,
   delay or veto is valid; the app never forces a trade to produce a screenshot.
4. Manual paper orders are independently available: long/short, market/limit,
   1–3× leverage, stop distance and take-profit distance. Orders appear after
   simulated latency; risk sizing may reduce requested notional. Inspect order
   status for minimum-size, margin or depth rejections.
5. Inspect positions, close half or all, cancel a pending limit, and review the
   history, performance, cost accounting and activity log.

The inherited model requires dedicated `VELA_MODEL`, `VELA_MODEL_KEY`, and
optionally `VELA_MODEL_ENDPOINT` environment configuration. Supply these securely
to this development process; never copy WHY credentials or put secrets in the
frontend. No model key is required for public data or manual simulation.
Without it, the desk displays **MODEL OFFLINE**, reports rule-based evidence,
and does not claim that those rules are model-generated decisions. On 2026-10-21,
the dedicated `gpt-4.1-mini` model completed actual research, review and direction
for ETH and SOL, opening simulated positions from live public data. The browser checks
verified their explanations, positions and closures. BTC research abstained. These
are integration checks, not evidence of profitable strategy performance. The
deterministic suite also covers the complete pipeline independently of a model.

For an owner-only development configuration file, the local launcher reads only
the three model variables without evaluating shell commands or printing values:

```sh
chmod 600 .runtime/lighter-showcase/model.env
.venv/bin/python tools/run_lighter_paper.py \
  --model-env .runtime/lighter-showcase/model.env --port 8790
```

Stop the previous local preview before launching this process. Do not restart
existing Vela/WHY production services. The configuration file is ignored by Git.

## Market data and execution

- Public HTTPS `orderBookDetails` discovers IDs, supported price/size precision,
  minimum amounts, market status, margin parameters and leverage. Symbols are
  matched explicitly to active perpetuals; spot markets are excluded.
- One public upstream WebSocket subscribes to each market's `order_book` and
  `market_stats`. Subscription snapshots replace state; deltas update/delete price
  levels. `begin_nonce` must equal the preceding `nonce`; offsets are not assumed
  consecutive. Gaps or crossed books invalidate depth and reconnect with backoff.
- WebSocket ping/pong, receive timeout, server timestamps and separate 5-second
  book / 15-second stats freshness gates are enforced. A failed metadata refresh
  pauses execution until the specifications recover. No synthetic-price fallback
  is used. The browser reconnects its same-origin public stream independently.
- REST `candles` backfills the existing 250-closed-bar strategy window. The observed
  API uses compact `c`/`t`/`o`/`h`/`l`/`c` fields; the parser also handles the verbose
  field form. Only complete five-minute bars enter strategy features.
- Executions walk observed depth for volume-weighted fills, apply adverse spread/
  slippage, round fills to supported ticks and sizes down to supported steps.
  No fill is invented without sufficient depth. Limits never fill beyond their
  limit. Resting maker fills are a conservative approximation without queue
  priority. Market orders expire after 30 seconds; limits after seven days.
- Pending order margin is rechecked at fill; it is not reserved at submission.
  One position per market, up to three markets per paper account. Leverage retains
  Vela's stricter 3× ceiling even when the market permits more. Risk-triggered
  closes override pending manual reductions and cannot be blocked by user rate
  limits. Risk orders remain pending through stale data; they never fill at an
  invented historical stop price.

## Risk and accounting

Every account, including any future account, receives a maximum **$2 per-position
stop-trigger budget including trading fees**. Smaller configuration/order limits
persist with the position. Strategy, balance, leverage and account labels cannot
increase it. The inherited `loss_stop.stop_price` rounds long stops up and short
stops down, preserves stricter prior stops, includes entry/partial-exit fees and
realized gross partial P&L, and is reapplied on every cycle after restart.
A one-microdollar allowance covers exit-fee accounting rounding. The inherited
minimum 0.04% exit-fee risk reserve remains stricter than the current zero-fee
simulation; it is not charged as a trading fee.

**A stop-trigger budget is not a guarantee against slippage, gaps, funding or data
outages.** Fills use the available market after latency. Isolated liquidation is
approximated when isolated margin plus mark P&L, less entry fees and accrued
funding, falls to the market's maintenance requirement; it is not a reproduction
of Lighter's liquidation/insurance/ADL mechanisms.

Initial fees are 0% maker, 0% taker and 0% Vela platform fee. These are configurable
simulation assumptions, not promises about future live trading. Configuration is
in [`showcase.env.example`](../backend/vela/lighter/showcase.env.example); fees,
slippage and additional half-spread are fractional rates (0.0002 means 0.02%).

Spread, depth impact, slippage, trading fees and signed funding have separate
ledger entries/totals. Spread/slippage already affect execution price and are
not debited twice. Funding is a continuous prorated simulation of the current
hourly estimate using index price, quantity and side. WebSocket percentage rates
are divided by 100 once; positive rates charge longs and credit shorts. Funding
is debited as it accrues and allocated proportionally to partial exits without
double charging. It is **not** exact hourly exchange settlement. Unobserved
intervals (including restart gaps) are excluded and permanently flagged in that
account's P&L display. Historical funding backfill is not implemented.

Equity is simulated balance plus mark P&L minus unallocated entry fees. Funding
already affects balance. Available margin deducts open isolated position margin.
Trade net P&L includes allocated entry/exit fees and funding. Today's closed-fill
count, wins, losses and net realized P&L all use the same UTC day. All-time numbers
use all closed fills in this account. Partial exits count as separate closed
fills; there is no fabricated win rate. The chart shows recent observed equity.
The account endpoint returns the latest 100 trade rows and 30 order rows; totals
still cover the full account history. No engineering accounts are aggregated
into public strategy results or an advertised leaderboard.

## Architecture and changed files

| Files | Purpose |
| --- | --- |
| `backend/vela/lighter/feed.py` | Public REST/WS, market specifications, depth sequencing, reconnection, history |
| `backend/vela/lighter/config.py` | Paper-only configuration, isolated database, fee/latency/cost assumptions |
| `backend/vela/lighter/engine.py` | Existing adapter/ledger extension, orders, stops, partials, funding, liquidation, reporting |
| `backend/vela/lighter/app.py` | Dedicated API, public browser stream, model worker, local static preview |
| `backend/vela/agents.py` | Optional market/eligibility/price-gate hooks and execution-cost context; original default behavior retained |
| `site/src/paper-entry.jsx` | Branded responsive React desk; wallet-free sessions and no signing code |
| `site/dist/paper.html`, `paper.css`, `assets/paper.js*` | Static desk entry, styles and built output |
| `site/scripts/build-wallet.mjs` | Adds a separate small paper bundle without loading the wallet SDK |
| `site/dist/index.html`, `styles.css` | Adds a showcase link and discreet footer attribution; preserves navigation and existing flows |
| `backend/tests/test_lighter.py`, `site/checks/paper.cjs`, `site/checks/paper-ai.cjs` | Deterministic core/API/AI/reconnection tests, real-public-data browser flow and opt-in real-model verification |
| `site/checks/browser.cjs` | Updates the prior landing-page expectation for the explicitly requested paper link |
| `tools/run_lighter_paper.py` | Loopback-only launcher and private allowlisted model configuration loader |

`LighterPaperAdapter` implements the existing `ExecutionAdapter` boundary.
`LighterLiveAdapter` is an unusable stub and `LIVE` configuration is rejected.
No signer, exchange transaction endpoint, private-key field or chain client is
introduced. A future authenticated implementation belongs behind this boundary,
with separate authorization, custody design and verification.

## Validation

Run the paper suite without network credentials:

```sh
.venv/bin/pytest backend/tests/test_lighter.py -q
```

The existing chain regression tests need the repository's local Anvil process on
`127.0.0.1:18545` (chain 31337) and the existing built contract artifacts. These
are loopback EVM tests, not testnet/mainnet transactions:

```sh
.tools/foundry/anvil --host 127.0.0.1 --port 18545 --silent
# In a second terminal:
.venv/bin/pytest backend/tests -q
```

Implementation-session evidence is recorded in
[`snapshot/lighter-validation.json`](../snapshot/lighter-validation.json).
The full backend suite passed **85 tests** (two dependency deprecation warnings),
the frontend build succeeded, and both the landing and paper browser checks
passed. The paper flow was checked again with the real model configured.
Both a long and a short paper stop were also observed closing automatically
against live public prices in explicit engineering checks. These are not real
exchange stops or AI strategy performance, and no live protection was changed.

With the paper service running, the browser checks use real public data and only
the isolated simulated ledger. They create a fresh engineering account, test
three markets, long/short, partial/full closes, limit cancellation, persistence,
P&L reconciliation, offline/reconnect and responsive widths. No AI model is called
by that check. The separate opt-in AI check calls the configured development model,
records genuine decisions, accepts abstention, and pauses its engineering agent
and closes its simulated position when finished.

```sh
cd site
PREVIEW_URL=http://127.0.0.1:8790 CHROME_PATH=/opt/google/chrome/chrome node checks/paper.cjs
PREVIEW_URL=http://127.0.0.1:8790 CHROME_PATH=/opt/google/chrome/chrome node checks/browser.cjs
# Requires the server to have the dedicated development model configured:
PREVIEW_URL=http://127.0.0.1:8790 CHROME_PATH=/opt/google/chrome/chrome AI_CHECK_SYMBOL=ETH node checks/paper-ai.cjs
```

Set `CHROME_PATH` to your installed browser. `CHECK_ARTIFACTS_DIR` overrides the
default screenshot/result directory `/tmp/vela-paper-checks`. Browser test P&L is
an engineering result from actual simulated fills, never an AI track record.

## Production/commercial blockers

- Owner-approved deployment and domain/reverse-proxy/WebSocket setup; none was
  performed by this task. Existing live stops and exchange accounts are unchanged.
- Production model provisioning, credential rotation and per-tenant quotas. A
  dedicated local model has been verified; the inherited 200-calls/day guard
  is suitable for a small showcase, not a commercial multi-tenant model service.
- Commercial authentication, capability recovery, abuse controls, per-tenant
  quotas, audit/retention/backups, monitoring, a durable market-data service and
  load testing. This is one SQLite writer with a 500-account showcase ceiling.
- Exact funding settlement/history, maker queue/partial-fill realism, and complete
  liquidation/ADL/insurance modeling if tighter exchange parity is required.
- For any future LIVE product: a separately implemented and audited authenticated
  adapter, custody/key management, jurisdiction/access review, commercial fee/tier
  terms, reconciliation/recovery and independent verified exchange protection.
  The current app cannot enable LIVE through configuration.

## Official references checked 2026-10-21

- [WebSocket reference](https://apidocs.lighter.xyz/docs/websocket-reference)
- [Market specifications](https://apidocs.lighter.xyz/reference/orderbookdetails)
- [Public candle history](https://apidocs.lighter.xyz/reference/candles)
- [Account types and Standard fee/latency table](https://apidocs.lighter.xyz/docs/account-types)
- [Trading fees](https://docs.lighter.xyz/trading/trading-fees)
- [Funding](https://docs.lighter.xyz/trading/funding)
- [GPT-4.1 mini API and structured output support](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

The current Standard table advertises 0% maker/taker and 300ms taker latency.
TWAP fees are a separate exception and TWAP orders are not offered here. Only
the requested text attribution links to `https://lighter.xyz`; no Lighter logo
or claim that Vela operates the underlying exchange is added.
