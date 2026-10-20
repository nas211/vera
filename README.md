# Vera

Independent source snapshot of the WHY trading system, prepared for future
integration into Doge OS and its app/wallet.

**This repository is a development copy. It is not deployed or connected to a
trading account.** The original WHY services remain separate. Copying this
repository does not start agents, move funds, change exchange orders, or update
live stop protection.

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

## Vela website for DogeOS

The independent [Vela website](site/README.md) targets `defivela.xyz`. It includes
a responsive interactive product preview with MetaMask and MyDoge Wallet
integration for DogeOS Chikyū Testnet: user-approved connection and network
switching, account display, native testnet DOGE balances, explorer links and
disconnect. MyDoge uses the official DogeOS SDK and the owner's public Sandbox
client ID. Its connection screen opens as a top-level page for the native
MyDoge mobile bridge.

The website's trade ticket, yield calculator, bonuses and agent activity are
illustrative. Vela does not request trading signatures, token approvals or
transactions. The SDK manages its own wallet login and consent. Website and
mock-wallet browser checks passed independently of the 41 copied execution
tests. The real SDK connection modal also opened without JavaScript errors;
approval inside a real MyDoge mobile app or MetaMask extension was not exercised.
See [deployment and verification status](docs/DEPLOYMENT.md).

Backend wallet authentication, DOGE deposits/withdrawals, reward settlement,
bonuses, and an APY product remain future integration work.
Existing referral rewards are historical WHY mechanisms, not DOGE rewards.
No APY or returns are promised by this repository.

Third-party strategy license files and notices are retained with their source.
Do not assume that a copied third-party strategy has the same license as the
rest of the application.
