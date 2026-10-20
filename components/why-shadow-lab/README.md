# WHY shadow AI lab

Built October 1, 2026. **SHADOW_ONLY. No real-order capability.**

## Current status

Updated October 2, 2026: all six agent identities use OpenAI `gpt-6-luna`
through the Responses API with medium reasoning effort. The owner-authorized
key is installed in the root-only lab environment. The model-access check and
a live function-call/continuation test returned HTTP 200. The bounded `remember`
tool round trip used 1,437 reported tokens in an isolated temporary database.
The agent runtime has been restarted with the new model; numerical screening,
strategy evidence, peer inboxes, private memory and paper books are preserved.
Forward results and strategy promotion remain subject to all existing hard gates.
No profitable or promoted AI strategy is claimed.

Previously the agents used Groq `openai/gpt-oss-120b` and reached its token
limits. On provider/key changes, quota records are archived and a separate
quota scope is initialized; old quota waits and pending tool conversations are
not reused with the new provider. Keys are never included in agent prompts or
committed configuration.

## Architecture

| AI agent | Goal and bounded authority |
| --- | --- |
| Research | Develop hypotheses, select numerical-tool search focus, submit immutable candidates |
| Critic | Challenge evidence; approve historically hard-gated candidates for research paper only |
| Direction | Choose long, sideways, short, or abstain; consume risk/peer advice |
| Paper | Authorize specific strategies and simulated books for a limited time, or pause |
| Promotion | Review forward evidence and allow gated promotion to **live paper only** |
| Feedback | Diagnose results, retain lessons, advise peers on research allocation and risk |

Each identity has its own mission, persistent inbox cursor and private memory,
model-selected function tools, bounded multi-step turns, reasoned decision
summaries, and an auditable conversation ledger. Peers can challenge and advise
each other; advice is not authority to bypass another role or a safety gate.
The same configured model can power separate agent identities. The agents do
not receive shell access, arbitrary Python execution, browsing, or exchange tools.

`agents.py` contains the model-driven runtime. `lab.py` is the deterministic
tool/safety kernel, **not** an AI-agent substitute. `screen.c` is its fast proxy
screening tool. Gaussian directional statistics are evidence for the direction
agent, not the direction agent itself. Search currently covers momentum,
breakout, mean reversion, range rejection and acceleration; new code/families
require a separate reviewed implementation rather than agent-generated execution.

## Evidence and safety

- Numerical screening uses training-only terminal-return histograms. It is an
  approximation, **not a path-aware backtest**. Minimum-sample skips are excluded
  from reported scored permutations. Repeated screenings across refreshed data
  are evaluations, not new independent discoveries.
- Exact simulations use chronological 60/20/20 splits, causal features, next-bar
  entries, one position per market/strategy, conservative stop-first ambiguous
  bars, adverse spread, two-sided taker fees and a funding reserve. Historical
  holdout is explicitly exploratory under repeated selection. Automated research
  and feedback metric views omit holdout statistics; agents are also instructed
  not to relay holdout-based tuning advice.
- AI criticism cannot approve a strategy that failed hard historical gates.
  AI direction confidence is bounded by recorded statistical evidence rather
  than a model's unsupported confidence claim. Probabilities are not guaranteed
  calibrated and are not promises of profit.
- Fresh quotes, a fresh direction decision and a separate expiring paper-agent
  authorization are all needed for entries. Inference or data failures block new
  entries. Simulated exits continue without model approval when fresh prices exist.
- Research paper and promoted live paper have independent $25,000 ledgers,
  $200 simulated positions, at most eight open markets and two per family per
  book, plus book/strategy loss limits. No production sizing or settings change.
- Paper fills are simulations from sampled mark prices, not executable quotes.
  Covered completed candles recover missed barriers conservatively; the partial
  entry candle is excluded to avoid pre-entry extrema. Ambiguous candles stop
  first, target fills are capped at the target, and unknown/stale prices are not
  fabricated. Fees/spread/funding are configured estimates. Liquidity, capacity,
  actual funding and exchange fills are not demonstrated by this book.
- Promotion requires at least 14 forward-observed trading days, 100 closed
  trades, profit factor 1.2, expectancy statistic 2, and drawdown limits. Promoted
  families are diversified with daily correlation evidence. These gates are
  safeguards, not proof of profitability. Model approval cannot relax them.
- Promotion creates `LIVE_PAPER` only, never actual trades. Retired strategies
  cannot enter again; fresh-price risk exits close remaining simulations.

## Isolation and operations

New services only:

```
why-shadow-lab.service         # deterministic safety tools + loopback dashboard
why-shadow-lab-screen.service  # numerical tool, not an agent
why-shadow-lab-agents.service  # six model-driven reasoning identities
```

Dashboard: `http://127.0.0.1:8110/`; JSON: `/api/status`. There is no public route
or change to whytradingbot.com. Connect using an SSH tunnel if needed.

All services use a new unprivileged `whyshadowlab` identity. Production `/opt`
projects and `/etc` credentials are hidden by private mount namespaces. The safety
and screening tools can read only this lab and the mark recorder's public-data
state; the source is mounted **read-only as a directory** so atomic snapshot
replacement remains visible. The AI runtime cannot read even that source; it
receives evidence through the lab's SQLite state. Only lab state is writable.
Screening has no IP network access; the safety/dashboard tool is loopback-only.
The model runtime uses only its configured model HTTP endpoint (no redirects or
environment proxies), and has no trading credentials or exchange methods.
CPU limits are 45%, 20%, and 10% of one CPU respectively, with low scheduling
priority and separate memory caps.

On deployment, the existing Yellow, mark-recorder, and live-Yellow services
remained active with the same PIDs/start times. Checked production source hashes
were unchanged. No existing service was restarted or edited.

To stop just this lab:

```
systemctl stop why-shadow-lab-agents why-shadow-lab-screen why-shadow-lab
```

## Configure a model, separately

The root-owned, mode-0600 `/etc/why-shadow-lab/model.env` contains the dedicated
OpenAI endpoint/model and the owner-authorized key. `deploy/model.env.example`
is its key-free template. The selected endpoint is
`https://api.openai.com/v1/responses`, model `gpt-6-luna`.
HTTPS is required except for an explicitly configured loopback local model.
Restart only `why-shadow-lab-agents.service` after credential changes.

The Responses transport supports strict function schemas, medium reasoning,
and `store=false`. All response reasoning/output items, including encrypted
reasoning state, are replayed with corresponding tool outputs during multi-step
turns and after quota pauses. Incomplete, refused or unsupported responses cannot
authorize tool actions. Chat Completions compatibility remains available for
other explicitly configured providers.

Official compatibility sources:
- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Reasoning state](https://developers.openai.com/api/docs/guides/reasoning)

Local rolling limits are 900 requests and 1,000,000 charged tokens per 24 hours,
plus 28 requests / 30,000 reserved tokens per minute. Each response is bounded
at 4,096 output tokens, and each turn at four requests/eight actions. Actual
reported usage settles token accounting; failures without usage retain reserved
estimates. These are local guards, not organization-wide billing controls.
Provider rate-limit headers and HTTP 429 delays still govern pacing.

Agents run when their roles have relevant evidence and stages. Research and
feedback normally run no more frequently than every ten minutes; direction and
paper require eligible strategies and fresh authorization. Quota-paused turns
persist reasoning/tool context without repeating completed actions. Stale
direction/paper contexts are discarded. When inference is unavailable, new
simulated entries cease after authorizations expire, while numerical screening
and deterministic risk exits continue.

## Verification

```
OPENBLAS_NUM_THREADS=1 python3 -m unittest discover -s /opt/why-shadow-lab/tests -v
```

33 tests pass: causal features, coverage gaps, conservative stop-first exits,
missing-model and low-confidence entry vetoes, restart deduplication, net-cost
accounting, role boundaries, peer messaging/memory, research holdout metric
filtering, bounded direction confidence, independent forward promotion gates,
paper-only promotion, mocked model-selected tool turns, endpoint restrictions,
truthful minimum-sample screening counts, stale snapshots, completed-candle
barrier recovery, exclusion of pre-entry extrema, selected-Luna/missing-key
behavior, pre-network minute pacing, rolling daily tokens, provider reset/retry
handling, mocked Groq payload/usage, durable quota-paused turn resumption,
serialization of the learned direction after sufficient covered history,
and credential-redacted provider errors with preserved Retry-After pacing.
Responses tests additionally verify encrypted reasoning/tool replay, strict
function payloads, tool continuation after quota pause, incomplete/refused output
vetoes, and provider-scope migration with evidence and paper-book preservation.

Observed first deployed cycle under the 45% CPU quota: 2,000,000 scored proxy
permutations over 10 markets / 21,170 bars in 21.97 seconds including preparation.
The service schedules **two million screenings per minute**, not continuous peak
throughput. The initial unthrottled diagnostic took 9.30 seconds. Neither number
measures AI decisions, exact backtests, independent strategies or future profits.
External inference returned HTTP 200 and a real `submit_strategy` call. That
hypothesis was rejected, not silently admitted to paper or production. No safety
gate was relaxed to obtain this result.
