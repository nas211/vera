# WHY external-strategy shadow experiment

Started October 5, 2026 under the owner's instruction to shadow all six GitHub
candidates. This is a forward observation experiment, not a claim of profitable
strategies. No strategy can graduate automatically to real trading or to the
existing lab's approved strategy registry.

## Running books

Each strategy has an independent Freqtrade 2026.9 process and SQLite ledger:

- FAdxSmaStrategy: 1h, long/short ADX + SMA crossover.
- BbandRsi: 1h, long-only Bollinger + RSI mean reversion.
- Strategy001: 5m, long-only EMA + Heikin-Ashi.
- FSupertrendStrategy: 1h, long/short Supertrend.
- VolatilitySystem: 1h with 3h resampling, long/short volatility breakouts.
- NostalgiaForInfinityX8 v18.0.119: 5m with native informative timeframes and
  native custom entry/exit/position-adjustment logic.

All use public Bybit USDT perpetual data for BTC, ETH, SOL, XRP, DOGE, BNB, LINK,
SUI, TAO and HYPE. Each starts with 25,000 simulated USDT, a requested initial
stake of 200, maximum eight simultaneous trades, forced 1x leverage, and at most
two additional entries. Native custom sizing and market lot precision may reduce
the requested initial stake. Native ROI, stop, trailing, exit and adjustment
rules are preserved; some stops are deliberately very wide. Those are research
subjects, not settings approved for live trading. Fees are fixed at 0.055% per
side; funding follows Freqtrade's public exchange data and accounting. Fills are
Freqtrade simulations, not actual exchange fills or demonstrated capacity.

These books use exchange OHLCV, volume and orderbooks, unlike the existing lab's
mark-candle simulations. Their raw results must not be ranked against the
existing lab without aligning inputs, costs and execution assumptions. There was
no historical profitability or bias qualification before this explicitly
authorized forward-only experiment.

## Provenance and adaptations

`review/sources.json` pins original GitHub commit IDs and SHA-256 hashes.
`sources/` retains original source files and licenses. No updater is installed.
`review/runtime-manifest.json` hashes every adapted strategy and configuration.
The launcher checks those hashes before loading strategy code.

- FAdxSmaStrategy: 400-candle warm-up; exit ADX uses the declared exit parameter
  (its default equals the entry threshold, so default thresholds are unchanged).
- FSupertrendStrategy: enable its existing short signals; 400-candle warm-up.
- BbandRsi, Strategy001 and VolatilitySystem: 400-candle warm-up.
- NFI X8: source unchanged; its native Bybit warm-up override remains in effect.
- Common experiment overrides are recorded above and in the manifest.

`review/requirements.lock` and `review/install-report.json` record resolved
dependencies and distribution hashes. Dependencies were installed into this
dedicated environment, not any production environment. The small sdnotify
source distribution was inspected before building because no wheel was
available. `review/static-source-scan.json` records imports and explicit
high-risk-call checks. This is limited static inspection, not proof that the
entire dependency graph is free of vulnerabilities.

## Enforced isolation

The six systemd services run as an unprivileged dedicated account. Their
filesystem exposes this project read-only and only their own simulation state
writable. Production `/opt` projects, `/root`, host `/run` sockets and `/etc`
credentials are hidden. No exchange credentials are configured.

`PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`, and `IPAddressDeny=any`
prevent strategy processes from using IP networking. They fetch exchange data
through a Unix socket exposed by `why-github-market.service`. That trusted
standard-library relay accepts only allowlisted public-market GET paths at a
fixed HTTPS Bybit host, never forwards supplied credentials, rejects redirects,
and has no order or account routes. CCXT's sync and async HTTP transports are
adapted in the launcher to use this socket; websocket access is disabled.

API servers, Telegram, webhooks, environment configuration overrides and nested
configuration files are disabled. The launcher refuses live mode or credentials.
Even changing a strategy cannot give it an upstream order route through the relay.

## Visibility and AI agents

`why-github-shadow-report.service` reads each simulation ledger and publishes
`/opt/why-shadow-lab/state/github_shadow.json` atomically. The existing dashboard's
`/api/status` exposes it as `github_shadow`. Research and Feedback agents receive
a compact read-only summary in their evidence. Other agents' admission and
promotion gates are unchanged. These six benchmarks are not inserted as approved
strategies in the lab database.

Reports distinguish a live process from successful analysis of fresh candles for
all ten pairs. Missing/stale reports are explicit. Realized P&L excludes open
position P&L; it is not total equity. Null profit factor means no realized losses,
not evidence of an infinite or established edge.

## Operations

Services start automatically at boot:

```
systemctl status 'why-github-shadow@*' why-github-market why-github-shadow-report
```

Stop only these simulations (preserves all ledgers):

```
systemctl stop why-github-shadow@FAdxSmaStrategy why-github-shadow@BbandRsi why-github-shadow@Strategy001 why-github-shadow@FSupertrendStrategy why-github-shadow@VolatilitySystem why-github-shadow@NostalgiaForInfinityX8
```

Start the same services to recover their simulated positions. Never delete their
state to repair an error. Source/config updates require a new reviewed manifest;
do not relabel altered strategies as the same experiment.

The existing lab kernel and AI runtime were restarted only to expose read-only
benchmark evidence; their ledgers were retained. Production Yellow/customer/demo
services and the existing numerical screener were not restarted.

## Verification

- Boundary tests exercise all six configurations and reject live mode, credentials,
  private routes, POST requests, alternate hosts and increased sizing limits.
- The sandbox probe successfully reads public data while verifying that IP
  sockets, production paths, host sockets, private paths and order POSTs fail.
- Lab regression tests cover the separate report, stale/missing evidence,
  unchanged registry and forbidden promotion.
- Runtime verification requires all six to analyze ten pairs successfully and
  all saved simulated trades to have 1x leverage and dry-run order identifiers.

Evidence and checks are retained under `review/` and `state/SandboxProbe/`.
