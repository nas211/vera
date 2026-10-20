# Component map

All paths below are under `components/`. Original names are retained to make
imports, source comparisons and the eventual integration easier to review.

| Directory | Copied responsibility |
|---|---|
| `why-yellow` | Main trading/execution core, customer APIs, tenancy, account vault interfaces, Yellow/WEEX adapters, scalp integration, Supabase migrations, affiliate UI and tests |
| `why-discovery` | Strategy discovery agents, model access, learning, market/context feeds, historical screening, forward paper evaluation, gateway and release regression tests |
| `why-strategy-platform` | Independent strategy research, hypotheses export, paper strategies and promotion evidence |
| `why-shadow-lab` | Model-driven research, critic, direction, paper, promotion and feedback roles; safety kernel; screening C source |
| `why-github-shadow` | Isolated external strategy experiments, public-market relay, result publisher and upstream strategy license files |
| `why-bybit` | Bybit paper/execution source and research/replay scripts |
| `why-bybit-demo-yellow` | Yellow-signal-to-Bybit demo bridge and guards |
| `why-bybit-live-yellow` | Legacy Bybit execution bridge, capital and arming/guard source; no account credentials |
| `why-bybit-shadow` | Independent Qwen-driven shadow book, model transport and simulator |
| `why-yellow-markshadow` | Mark-candle recording, shadow book, comparisons and replay |
| `why-yellow-micro-paper` | Paper micro-scalping strategies and tests |
| `why-yellow-micro-experiments` | Independent experimental paper exit variants and tests |
| `why-scalp-research` | Scalp research recorder, replay/evaluation code, dashboard and tests |
| `why-website` | Next.js site/dashboard, account UI, support bridge and browser/API tests |
| `why-partners` | Partner portal, authentication, mail and read-only book integration |
| `why-referrals` | Historical referral/reward ledger, quests and conversion logic |
| `why-auth-mail-relay` | Authentication confirmation-email fallback |
| `why-loculus-paper` | Available paper configuration only; no implementation source was present in this installed directory |

`ops/production-reference/` records installed service definitions with inactive
suffixes and without inline environment values. Those files describe the source
system, not a Vera deployment.

The source manifest includes SHA-256 hashes of the source and copied file, and a
description of any configuration/reference conversion. Runtime data was not
copied, so this repository does not contain running agents' accumulated memory,
open positions, tenant balances or exchange account connections.

The next integration phase should define the wallet-to-customer identity
boundary, isolated Vera execution/account namespaces, DOGE accounting and reward
settlement, and a separate deployment. Preserve the universal $2 stop budget and
test every new execution path before enabling it.
