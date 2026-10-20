# DogeOS testnet showcase runbook

Vela is TESTNET + PAPER ONLY. Use Python 3.12, Node 22+, and Foundry (verified
here with v1.8.5). Work in `/root/Vera`; never run the inherited `.reference`
deployment files. Only new `vela-testnet-*` units belong to this showcase.

## Configuration and deployed contracts

`config/dogeos-testnet.json` is canonical. `npm run build` copies it to
`site/src/chain-config.json` for a self-contained Sites checkout and emits the
MetaMask configuration. Do not hand-edit the generated copies.

| Setting | Value |
| --- | --- |
| Chain | DogeOS Chikyū Testnet |
| Chain ID | 6281971 / `0x5fdaf3` |
| Gas | Native test DOGE, 18 decimals |
| RPC | `https://rpc.testnet.dogeos.com/` |
| Explorer | `https://dogeos-testnet.l2scan.co` |
| Faucet | `https://faucet.testnet.dogeos.com/` |
| VelaVault | `0x595e36cDD915db23b563b26fAe575B7Ff6066d3f` |
| WDOGE | `0xc4d9A03B101a5d11894c9DE9Da49A5d9F88845C5` |
| TUSDC | `0xAC9cED37e9F23f3effDf09B505b2F7809416e1E0` |

Network values come from the [official quickstart](https://docs.dogeos.com/en/developers/developer-quickstart)
and [SDK guide](https://docs.dogeos.com/en/sdk/guides), checked October 20, 2026.
The faucet is linked by the SDK guide. The vault has bytecode verified through
the official RPC. Deployment and funding hashes are in the canonical config.

## Install and test

```sh
cd /root/Vera
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
cd contracts
npm ci
forge test
cd ../site
npm ci
npm run build
cd ..
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_paper.py
python3 tools/check_execution.py
```

For the local EVM integration suite, run `anvil --port 18545 --chain-id 31337`
in another terminal, then
`PYTHONPATH=backend .venv/bin/pytest -q backend/tests`.
It uses randomly generated, in-memory local keys and no testnet funds.

## Deploy and fund

The existing deployment above is usable. The Python deploy command refuses to
redeploy while `deployment` is present in the canonical config.
For an intentional new test deployment, remove that field in a separate checkout,
fund a disposable EVM signer using the official faucet, and run:

```sh
cd /root/Vera
export VELA_EXPECTED_OPERATOR=YOUR_PUBLIC_EVM_ADDRESS
.venv/bin/python tools/with_operator.py deploy --pool-doge 10
```

The helper prompts without echo and injects the key into the process environment.
Never put a private key in a shell argument, frontend, Git, or chat. The current
test key was disclosed in chat; it is disposable and unsuitable for real assets.
The deployment checks chain 6281971, runs Foundry tests, writes actual deployed
addresses/ABI, and calls `fundPool()` with the specified faucet DOGE. The pool
funding is wrapped immediately. Anyone can add further test DOGE through
`fundPool()`; plain native transfers are rejected.

`contracts/script/Deploy.s.sol` is also available for Foundry users with
`VELA_OPERATOR_PRIVATE_KEY` already securely injected. It deploys contracts
only; the Python command additionally records config and funds the pool.

## Run backend and frontend

Environment names are documented in `backend/.env.example`. Use dedicated Vela
credentials only. Model and Pyth keys are optional; the operator key is required.

```sh
cd /root/Vera
# Terminal 1, API and built frontend; no signer needed:
PYTHONPATH=backend .venv/bin/uvicorn vela.api:app --host 127.0.0.1 --port 8787
# Terminal 2, prompts for the disposable operator key:
.venv/bin/python tools/with_operator.py worker
```

Open `http://127.0.0.1:8787/mydoge.html`. For a pure static preview use
`python3 -m http.server 8769 --bind 127.0.0.1 --directory site/dist`.
`site/dist/backend-config.js` chooses the HTTPS backend origin on the hosted
site; an empty string uses the same origin. Publish the built `site/dist/`
through the existing Sites project. Never publish backend runtime files.

The API is read-only. Session authorization is the confirmed vault event from
the connected user's wallet, not an unauthenticated HTTP mutation. Public
endpoints expose only testnet ledger data:
`/health`, `/health/prices`, `/health/engine`, `/health/events`,
`/health/operator`, `/health/agents`, `/api/config`, `/api/prices`,
`/api/strategies`, `/api/users/{address}`, `/api/leaderboard`,
and `/api/logs/{service}`.

New service templates are in `ops/vela-testnet-{api,worker,tunnel}.service`.
They are independent of WHY. The current deployment uses new transient
systemd units. `tools/with_operator.py worker-service` starts the worker while
importing its key into systemd's environment without a secret command argument.
It also forwards present `VELA_*` settings named in the helper. Do not run two
workers against the same database; a process lock enforces that.

The current API tunnel is
`https://plot-adds-vertical-workforce.trycloudflare.com`.
It is a temporary Cloudflare Quick Tunnel, not a permanent service address.
A tunnel restart may require updating `backend-config.js` and republishing.
After a host reboot, recreate the transient units and securely re-inject the
operator key. A permanent tunnel/secret-manager setup is still needed for
unattended operation across host reboots. Logs: `journalctl -u
vela-testnet-worker`, `vela-testnet-api`, or `vela-testnet-tunnel`.
Do not dump service environments into logs.

## Execution and accounting

START sends native test DOGE in one transaction. VelaVault wraps it into this
showcase's WDOGE and mints 6-decimal TUSDC held by the vault while crediting a
user's internal paper balance. This is operator-priced accounting conversion,
not a DEX swap or real USDC. Each user can have one active session/position.

The single Vela AI Agents portfolio reuses both existing reversion/pullback
signal families across the four crypto markets. Its discovery pipeline reuses
the bounded research/review/direction model and validated rule DSL.
Without `VELA_MODEL` and `VELA_MODEL_KEY`, signal and risk agents operate and the
UI explicitly reports that the research model is disconnected. Once configured,
model review is mandatory; an absent or expired approval cannot fall back to
a rule-only entry. The app never reuses WHY's model credentials.
Agents wait for valid fresh candle evidence.
The verification trade is explicitly marked engineering-only and is excluded
from strategy leaderboard performance.

Email wallets use one `eth_signTransaction` approval followed by direct broadcast
to the official chain RPC. The client verifies the recovered signer, chain,
destination, amount, calldata, nonce and gas before broadcasting. It retains
the hash before sending so a timeout does not prompt a duplicate signature.
Balance and gas preflight explain when the connected email address differs from
the MetaMask address the owner funded. An explicit MetaMask alternative is at
`/mydoge.html?wallet=metamask`. Actual email/mobile signing needs owner verification;
mocked signing tests and direct testnet signing are separate evidence.

Fills use midpoint ± 0.02% default slippage and 0.05% fees per side.
Leverage is at most 3x, sizing defaults to 10% of paper balance, and funding is
ignored. Every position preserves the stricter of its strategy stop and a
fee-inclusive $2 maximum stop trigger loss budget. Partial exits consume the
same position budget; persisted positions are reconciled every tick. This
trigger budget is not a guarantee against gaps, slippage, or funding.

Pyth Hermes is primary; Binance public midquotes are fallback for DOGE, BTC,
ETH, and SOL, using USDT as a USD proxy. Gold has no configured fallback.
**The requested no-key Hermes service no longer exists:** the
[Pyth upgrade docs](https://docs.pyth.network/price-feeds/core/upgrade/preparing)
require an API key since August 26, 2026. Supply `VELA_PYTH_API_KEY` to use it.
Without that key, crypto fallback works and XAU remains unavailable.
Missing/stale sources block the affected market; divergence above 1% blocks
new paper entries globally. No fabricated close price is used during outages.
The worker queues DOGE oracle updates every 30 seconds when users are active,
60 seconds when idle; confirmation and RPC delays can extend submission time.
Contracts reject an oracle older than 120 seconds.

STOP is one user transaction. The worker observes it, closes all positions,
confirms each `TradeLogged`, and settles with the exact sum of logged P&L.
The operator pays settlement gas; the user does not sign a second withdrawal.
Losses floor payout at zero. Profits are capped at the user's reserved
principal plus available faucet pool; other active users' principal is reserved.
The owner can pause new entries and rotate the operator. Exits remain allowed
while paused. Only the operator may update prices, log trades, or settle.

SQLite lives only in `.runtime/`; it stores cursor, event deduplication,
positions, trades, equity, and a signed transaction outbox. Signed bytes are
persisted before broadcast and the same transaction is retried after restart.
The default indexer waits three blocks. A confirmed-block reorganization
halts processing for operator reconciliation; it does not silently rebuild
balances. A mined revert is retained as failed for inspection, not treated as
successful settlement. Preserve/back up the DB before any manual recovery.
The operator remains trusted for prices and paper P&L; this is not audited.

## Verification

`snapshot/testnet-e2e.json` records the actual public testnet round trip,
transaction hashes, P&L, and amount returned. Run the explicit engineering
check with `.venv/bin/python tools/with_operator.py e2e` while the worker is
stopped. It deliberately submits a labeled smoke-test signal, rather than
claiming an AI strategy decided to trade.

Browser checks from `site/` with the preview running:

```sh
CHROME_PATH=/opt/google/chrome/chrome node checks/browser.cjs
CHROME_PATH=/opt/google/chrome/chrome node checks/wallet.cjs
CHROME_PATH=/opt/google/chrome/chrome node checks/mydoge.cjs
CHROME_PATH=/opt/google/chrome/chrome PREVIEW_URL=http://127.0.0.1:8769 node checks/trading.cjs
CHROME_PATH=/opt/google/chrome/chrome node checks/sdk-smoke.cjs
```

The trading suite uses mocked provider/API fixtures. The SDK smoke check uses
real MyDoge services but stops at the login screen. Actual mobile approval on
DogeOS testnet remains an owner verification step.
