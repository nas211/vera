# Vera

Independent WHY source snapshot, extended with the Vela DogeOS Chikyū
**testnet + paper-only** showcase.

The new **Vela competition portal** at `/trade` adds signed wallet onboarding,
persistent demo-USDC deposits, a simple AI Managed dashboard, Start/Stop controls,
confirmed position exits and demo withdrawals. The homepage keeps its design and animations; its **START TRADING** CTA and
wallet/trading entry points share the demo onboarding. See [the competition runbook](docs/COMPETITION-PORTAL.md)
for startup, verification and mobile-wallet limitations. The portal is now integrated
into the original `vela.api:app`; the existing API, worker, domain and Sites project
are retained. These are prepared source changes, not a verified production update.
See [integration and rollback](docs/INTEGRATED-DEPLOYMENT.md).

The new **wallet-free Vela paper desk** at `/paper` uses Lighter's public BTC,
ETH and SOL data with an isolated virtual-USD ledger. It preserves this site's
branding and the existing agent/risk logic. See [the Lighter paper runbook](docs/LIGHTER-PAPER.md)
for local startup, configuration, test commands and limitations. It is prepared
for local demonstration, not deployed to production. LIVE execution is disabled;
no existing exchange accounts or live stops are changed. The dedicated local
`gpt-4.1-mini` integration was verified through actual research, review, direction,
paper position creation and closure using live public market data. Each new
installation requires its own private development-model configuration.

The older published testnet flow is wallet funded: MyDoge/email or MetaMask → faucet DOGE →
one START transaction → wrapped DOGE and TUSDC credit → AI paper trading →
one STOP transaction → operator settlement returns DOGE. The separate
virtual-balance practice screen was removed from the public product.

**Public deposits remain paused** after the network-fee incident. The correct
visitor flow is restored, but this is not a claim that on-chain operation is
ready. The deployed V1 vault is retained; V2 is prepared, not deployed.
No real exchange orders, WHY services, forex/MT5 or live protection are changed.

## Run the showcase

See the complete [testnet runbook](docs/TESTNET.md) for contract deployment,
faucet funding, environment configuration, new service units and recovery.

```sh
cd /root/Vera
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
cd contracts && npm ci && forge test
cd ../site && npm ci && npm run build
cd ..
PYTHONPATH=backend .venv/bin/uvicorn vela.api:app --host 127.0.0.1 --port 8787
# Securely verify the dedicated model key and restart only the Vela worker:
.venv/bin/python tools/configure_model.py worker-service
```

Open `/mydoge.html#ai-portfolio`, connect the wallet that holds your faucet DOGE,
and choose the deposit amount. START prepares a fresh conversion price, shows
full network fees and requests one transaction signature. STOP requests one
signature; the operator closes positions, logs trades and returns DOGE.
The backend must be ready before START becomes available.

Actual mobile/email transaction approval remains untested. TUSDC is test
accounting, not real USDC or a DEX swap. `VELA_ENABLE_PRACTICE=true` enables the
separate internal engineering harness; it is disabled in the public deployment.

Canonical addresses and network sources: [`config/dogeos-testnet.json`](config/dogeos-testnet.json).
Actual verification hashes and payout: [`snapshot/testnet-e2e.json`](snapshot/testnet-e2e.json).
Publication, DNS and remaining limitations: [deployment status](docs/DEPLOYMENT.md).

## Included

- Trading execution, position management, venue adapters and risk controls.
- Customer accounts, authentication, tenant isolation, database migrations,
  credential-vault interfaces, billing and account APIs.
- Strategy discovery, model-driven research, criticism, direction, paper trading,
  promotion and feedback agents, plus numerical screening tools.
- Independent paper/shadow systems and external strategy experiments.
- The website, trading dashboard, partner portal, affiliate and referral code,
  email relay and support bridge.
- Existing tests, dependency manifests where available, source checksums, and
  inactive references describing the original service topology.

See [the component map](docs/COMPONENTS.md) and
[the source manifest](snapshot/source-manifest.json).

## Copy boundaries

Application source was copied from the installed `/opt/why-*` component trees,
including source changes that were not committed to their older Git histories.
Discovery regression tests came from the recorded working release. This is a
new source snapshot, not a fork of only the older website repository.

Credentials, private environment files, account databases, current positions,
agent memory/runtime state, logs, market-data caches, compiled builds, installed
dependencies and historical Git objects are excluded.

Runtime configuration files are included as `*.example.json`; a linked exchange
UID was cleared and the strategy-platform example's `live.enabled` is false.
Service files, server configuration and shell scripts have `.reference` suffixes;
inline service environment values were omitted. The manifest records every such
change. Application trading logic was not rewritten for this copy.

The inherited code and documentation still contain WHY names, domains, absolute
paths and default ports. **Do not run the inherited deployment commands on the
WHY production host.** A Vera deployment needs separate paths, service names,
ports, databases, credentials, order namespaces and infrastructure.

## Verify the copied execution core

With Python 3.12 and `requests` available:

```sh
python3 tools/check_execution.py
```

This invokes the copied regression tests against mock venues. The runner clears
inherited credentials/arming settings, blocks network and subprocess operations,
and confines writes to a temporary directory.

The initial check passed **41 tests**, covering long and short stops, fees,
inward rounding, partial-exit accounting, account overrides, future accounts,
existing-position reconciliation and restart behavior. Python and JSON syntax
checks also passed. See [validation details](snapshot/validation.json).

These checks do not certify the complete platform, a Doge OS integration, or
exchange behavior. The inherited WHY website, model-provider, database and live-venue
integration suites were not run.

## Risk requirement

Every account, including JP and future accounts, has a maximum **$2 per-position
stop trigger loss budget**, including trading fees. Keep stricter limits.
Account size, leverage, strategy, profit targets and account overrides must never
raise or bypass that budget. Round stops inward and account for partial exits.
This is not a guarantee against execution slippage, gaps or funding charges.

The requirement is preserved in [AGENTS.md](AGENTS.md), and the active execution
core's existing implementation is copied in
`components/why-yellow/execution/loss_stop.py`. Legacy adapters and research code
are retained for reference; their presence does not authorize live trading or
establish compliance for a new Vera execution path.

## Vela website and paper agents

The independent [Vela website](site/README.md) targets `defivela.xyz`. It includes
a responsive landing-page preview plus the MyDoge paper dashboard, with MetaMask and MyDoge Wallet
integration for DogeOS Chikyū Testnet: user-approved connection and network
switching, account display, native testnet DOGE balances, explorer links and
disconnect. MyDoge uses the official DogeOS SDK and the owner's public Sandbox
client ID. Its connection screen opens as a top-level page for the native
MyDoge mobile bridge.

The landing-page ticket, yield calculator, bonuses and agent animation remain
illustrative. `/mydoge.html` defaults to the separate practice ledger.
An unguessable browser capability controls only that simulated portfolio;
it cannot access a wallet, create chain transactions or modify the vault ledger.
The older vault flow remains event-authorized and paused.
The backend reuses pure WY signals, the bounded discovery AI pipeline, and the
fee-inclusive stop module. OrderlyAdapter is an unusable stub; live execution
is not implemented.

The app exposes one **Vela AI Agents** portfolio. Existing reversion/pullback
signals and risk controls are internal inputs. The research/review/direction
model needs separate `VELA_MODEL` and `VELA_MODEL_KEY` settings; its disconnected
state is shown in the app. When configured, model review is required and cannot
be bypassed by a rule fallback. Pyth now requires `VELA_PYTH_API_KEY`; Binance fallback
supports the four crypto markets meanwhile, and XAU stays unavailable.
Lending & Borrowing, staking contracts and the requested ZK privacy layer remain future work. The Staking calculator uses assumed APY.
Existing referral rewards are historical WHY mechanisms, not DOGE rewards.
No APY or returns are promised by this repository.

Third-party strategy license files and notices are retained with their source.
Do not assume that a copied third-party strategy has the same license as the
rest of the application.
