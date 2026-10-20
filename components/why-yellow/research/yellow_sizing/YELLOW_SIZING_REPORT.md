# Yellow WY — Position-Size Audit (offline)

Measurement only. Nothing was deployed, restarted or edited outside this directory. Bybit live, the Qwen shadow, Gold/MT5 and the Yellow strategy were not touched.
Reproduce: `python3 /opt/why-yellow/research/yellow_sizing/yellow_sizing.py` (reads the journal, writes this directory; about 0.1 s).

| | |
|---|---|
| **AUTHORITATIVE YELLOW SAMPLE** | **108 trades** (88 wins / 20 losses, 81.5%) |
| **REFERENCE RESULT REPRODUCED** | **YES**: all 108 rows match pnl_usd, fees_usd and pnl_pct to the cent; net $607.12 exact ($607.13 from rounded rows, $607.11 in the snapshot) |
| LEVERAGE | 10X (fixed) |
| STRATEGY / SIGNALS / EXITS CHANGED | NO / NO / NO |
| LIVE SYSTEMS TOUCHED | NO (journal sha256 identical before and after the run) |

## 1. The authoritative sample (verified from disk)

| Item | Value | Source |
|---|---|---|
| Journal | `/opt/why-yellow/state/journal.jsonl`: 108 OPEN + 108 CLOSE, 0 orphans | sha256 in `source_manifest.json` |
| Period | 2026-09-11 15:55 UTC → 2026-09-18 01:18 UTC (6.39 days, 16.9 trades/day) | journal ts. The book froze when Yellow's feed died on 09-18 |
| Markets | BTC 10, ETH 11, SOL 9, XRP 14, DOGE 8, BNB 6, LINK 12, SUI 9, TAO 19, HYPE 10 | journal |
| Sleeves | WY-PULLBACK 93, WY-REVERT 15 | journal |
| Sides | 58 long / 50 short | journal |
| Exits | TARGET 87, STOP 11, TIMEOUT 10 (12h). All three kept as they occurred | journal |
| Original sizing | $2,000 fixed notional per position, every trade; book equity $25,000 | journal `notional`, snapshot `clip_usd`, `equity0` |
| Leverage | 10x (display/config only; not used in P&L) | snapshot `leverage` |
| Concurrency | max_open 8, one position per market | engine CFG, snapshot |
| Fees | VIP5: taker 1.5 bps on entry; exit maker 0.0 bps on TARGET, taker 1.5 bps on STOP/TIMEOUT; charged on entry notional | `why-yellow.service` Environment, `yellow_paper.py close_position` |
| Slippage / spread | 2.0 bps per side, baked into the fill prices (entry and exit) | `WY_SPREAD_BPS=2.0`, `open_position`/`close_position` |
| Funding | **not modelled** in the accepted book | engine |
| History note | Rows were re-stamped to VIP5 on 09-17 (`journal.jsonl.bak.pre-vip5`, which mixes 4/1 and 1/0 bps eras). Only `pnl_usd` and `fees_usd` changed; prices, labels and reasons are identical on all overlapping rows | diff in `results.json → reference` |

Reference reproduced: 108 trades, 88 wins, net **$607.12**, fees **$38.70**. By sleeve: PULLBACK 93 trades / 74 wins / $565.92, REVERT 15 / 14 / $41.20. This matches the snapshot.

One caveat, stated once: this is one week of 108 trades, and a good week (PULLBACK won 79.6% vs 68.3% in its 31-day calibration). Dollar figures below describe *this* week scaled, not a forecast. The sizing mathematics (bps invariance, rounding, capital) does not depend on that.

## 2. Terms (at 10x)

- **Account equity**: the user's Yellow balance (cross-margin, so all of it backs every position).
- **Allocated capital**: the part of equity WHY is allowed to use. Here it equals equity, with free-collateral headroom enforced by a margin-use cap.
- **Position notional (S)**: the size of one position in USD. This is what the study varies.
- **Margin per trade** = S / 10. A $200 notional ties up $20 of margin.
- **Leverage**: fixed at 10x. It sets how much margin a notional ties up. It does not change P&L per dollar of notional.

## 3. Primary sizing table (pure scaling, arbitrary quantity precision, accepted VIP5 costs)

| Notional | Margin @10x | Trades | Win rate | Net P&L | Avg net/trade | Avg win | Avg loss | Win:Loss | PF | Max DD $ | Peak margin | Net bps/trade |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $10 | $1.00 | 108 | 81.5% | $3.04 | $0.028 | $0.068 | -$0.147 | 0.463 | 2.04 | $1.07 | $8.00 | 28.11 |
| $20 | $2.00 | 108 | 81.5% | $6.07 | $0.056 | $0.136 | -$0.293 | 0.463 | 2.04 | $2.13 | $16.00 | 28.11 |
| $30 | $3.00 | 108 | 81.5% | $9.11 | $0.084 | $0.203 | -$0.440 | 0.463 | 2.04 | $3.20 | $24.00 | 28.11 |
| $40 | $4.00 | 108 | 81.5% | $12.14 | $0.112 | $0.271 | -$0.586 | 0.463 | 2.04 | $4.27 | $32.00 | 28.11 |
| $50 | $5.00 | 108 | 81.5% | $15.18 | $0.141 | $0.339 | -$0.733 | 0.463 | 2.04 | $5.33 | $40.00 | 28.11 |
| $75 | $7.50 | 108 | 81.5% | $22.77 | $0.211 | $0.508 | -$1.099 | 0.463 | 2.04 | $8.00 | $60.00 | 28.11 |
| $100 | $10.00 | 108 | 81.5% | $30.36 | $0.281 | $0.678 | -$1.465 | 0.463 | 2.04 | $10.66 | $80.00 | 28.11 |
| $150 | $15.00 | 108 | 81.5% | $45.53 | $0.422 | $1.017 | -$2.198 | 0.463 | 2.04 | $16.00 | $120.00 | 28.11 |
| $200 | $20.00 | 108 | 81.5% | $60.71 | $0.562 | $1.356 | -$2.931 | 0.463 | 2.04 | $21.33 | $160.00 | 28.11 |
| $300 | $30.00 | 108 | 81.5% | $91.07 | $0.843 | $2.034 | -$4.396 | 0.463 | 2.04 | $31.99 | $240.00 | 28.11 |
| $400 | $40.00 | 108 | 81.5% | $121.42 | $1.124 | $2.712 | -$5.862 | 0.463 | 2.04 | $42.66 | $320.00 | 28.11 |
| $500 | $50.00 | 108 | 81.5% | $151.78 | $1.405 | $3.390 | -$7.327 | 0.463 | 2.04 | $53.32 | $400.00 | 28.11 |
| $750 | $75.00 | 108 | 81.5% | $227.67 | $2.108 | $5.085 | -$10.990 | 0.463 | 2.04 | $79.98 | $600.00 | 28.11 |
| $1,000 | $100.00 | 108 | 81.5% | $303.56 | $2.811 | $6.780 | -$14.654 | 0.463 | 2.04 | $106.64 | $800.00 | 28.11 |
| $1,250 | $125.00 | 108 | 81.5% | $379.45 | $3.513 | $8.475 | -$18.317 | 0.463 | 2.04 | $133.30 | $1,000.00 | 28.11 |
| $1,500 | $150.00 | 108 | 81.5% | $455.34 | $4.216 | $10.170 | -$21.981 | 0.463 | 2.04 | $159.97 | $1,200.00 | 28.11 |
| $1,750 | $175.00 | 108 | 81.5% | $531.23 | $4.919 | $11.865 | -$25.644 | 0.463 | 2.04 | $186.63 | $1,400.00 | 28.11 |
| $2,000 | $200.00 | 108 | 81.5% | $607.12 | $5.621 | $13.560 | -$29.308 | 0.463 | 2.04 | $213.29 | $1,600.00 | 28.11 |

Max DD is realized, on closing order. Peak margin = 8 simultaneous positions (the historical maximum) × S / 10.

### Full cost decomposition per size

| Notional | Gross | Entry fees | Exit fees | Fees | Spread/slip | Total cost | Net | Median | Best | Worst | Funding proxy* |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $10 | $3.66 | $0.16 | $0.03 | $0.19 | $0.43 | $0.63 | $3.04 | $0.076 | $0.077 | -$0.205 | $0.011 |
| $20 | $7.32 | $0.32 | $0.06 | $0.39 | $0.86 | $1.25 | $6.07 | $0.153 | $0.153 | -$0.410 | $0.022 |
| $30 | $10.98 | $0.49 | $0.09 | $0.58 | $1.30 | $1.88 | $9.11 | $0.229 | $0.230 | -$0.615 | $0.033 |
| $40 | $14.64 | $0.65 | $0.13 | $0.77 | $1.73 | $2.50 | $12.14 | $0.306 | $0.306 | -$0.820 | $0.043 |
| $50 | $18.30 | $0.81 | $0.16 | $0.97 | $2.16 | $3.13 | $15.18 | $0.382 | $0.383 | -$1.025 | $0.054 |
| $75 | $27.46 | $1.21 | $0.24 | $1.45 | $3.24 | $4.69 | $22.77 | $0.574 | $0.574 | -$1.538 | $0.081 |
| $100 | $36.61 | $1.62 | $0.32 | $1.93 | $4.32 | $6.25 | $30.36 | $0.765 | $0.765 | -$2.050 | $0.109 |
| $150 | $54.91 | $2.43 | $0.47 | $2.90 | $6.48 | $9.38 | $45.53 | $1.147 | $1.148 | -$3.076 | $0.163 |
| $200 | $73.22 | $3.24 | $0.63 | $3.87 | $8.64 | $12.51 | $60.71 | $1.530 | $1.530 | -$4.101 | $0.217 |
| $300 | $109.83 | $4.86 | $0.94 | $5.80 | $12.95 | $18.76 | $91.07 | $2.295 | $2.295 | -$6.151 | $0.326 |
| $400 | $146.44 | $6.48 | $1.26 | $7.74 | $17.27 | $25.01 | $121.42 | $3.059 | $3.061 | -$8.202 | $0.434 |
| $500 | $183.04 | $8.10 | $1.57 | $9.67 | $21.59 | $31.26 | $151.78 | $3.824 | $3.826 | -$10.252 | $0.543 |
| $750 | $274.57 | $12.15 | $2.36 | $14.51 | $32.38 | $46.90 | $227.67 | $5.736 | $5.739 | -$15.378 | $0.815 |
| $1,000 | $366.09 | $16.20 | $3.15 | $19.35 | $43.18 | $62.53 | $303.56 | $7.648 | $7.652 | -$20.504 | $1.086 |
| $1,250 | $457.61 | $20.25 | $3.94 | $24.19 | $53.97 | $78.16 | $379.45 | $9.561 | $9.565 | -$25.630 | $1.358 |
| $1,500 | $549.13 | $24.30 | $4.72 | $29.02 | $64.77 | $93.79 | $455.34 | $11.473 | $11.477 | -$30.756 | $1.629 |
| $1,750 | $640.65 | $28.35 | $5.51 | $33.86 | $75.56 | $109.42 | $531.23 | $13.385 | $13.390 | -$35.882 | $1.901 |
| $2,000 | $732.18 | $32.40 | $6.30 | $38.70 | $86.36 | $125.06 | $607.12 | $15.297 | $15.303 | -$41.008 | $2.172 |

\*Funding proxy: Bybit 8h funding prints over each hold (Yellow funding history is not on disk). It is **not** in Net, because the accepted model has no funding. It is small because the median hold is 91 min, so few trades cross a print: about 0.1 bps/trade, $2.17 on the whole week at $2,000.

### Typical winner / loser in actual dollars

| Position size | Typical winner (avg, after costs) | Typical loser (avg, after costs) | Median winner | Median loser |
|---:|---:|---:|---:|---:|
| $10 | $0.068 | -$0.147 | $0.076 | -$0.185 |
| $20 | $0.136 | -$0.293 | $0.153 | -$0.370 |
| $30 | $0.203 | -$0.440 | $0.229 | -$0.555 |
| $40 | $0.271 | -$0.586 | $0.306 | -$0.740 |
| $50 | $0.339 | -$0.733 | $0.382 | -$0.925 |
| $75 | $0.508 | -$1.099 | $0.574 | -$1.387 |
| $100 | $0.68 | -$1.47 | $0.765 | -$1.850 |
| $150 | $1.02 | -$2.20 | $1.147 | -$2.774 |
| $200 | $1.36 | -$2.93 | $1.530 | -$3.699 |
| $300 | $2.03 | -$4.40 | $2.295 | -$5.549 |
| $500 | $3.39 | -$7.33 | $3.824 | -$9.248 |
| $750 | $5.08 | -$10.99 | $5.736 | -$13.872 |
| $1,000 | $6.78 | -$14.65 | $7.648 | -$18.496 |
| $1,500 | $10.17 | -$21.98 | $11.473 | -$27.745 |
| $2,000 | $13.56 | -$29.31 | $15.297 | -$36.993 |

## 4. Sleeve breakdown

| Sleeve | Trades | Wins | Losses | Win rate | Avg win $2k | Avg loss $2k | Expectancy $2k | Avg win $100 | Avg loss $100 | Expectancy $100 | PF | Net bps/trade | TARGET | STOP | TIMEOUT |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| WY-PULLBACK | 93 | 74 | 19 | 79.6% | $15.12 | -$29.11 | $6.09 | $0.756 | -$1.456 | $0.304 | 2.02 | 30.43 | 73 | 10 | 10 |
| WY-REVERT | 15 | 14 | 1 | 93.3% | $5.30 | -$32.99 | $2.75 | $0.265 | -$1.650 | $0.137 | 2.25 | 13.73 | 14 | 1 | 0 |

The two sleeves pay differently:
- **PULLBACK**: TP 0.80% / SL 2.00%. Winner about 76 bps net, loser about 146 bps net.
- **REVERT**: TP 0.30% / SL 1.60%. Winner only about 26.5 bps net; one stop costs about 6 wins. Costs take **17%** of a REVERT winner's gross (6.8% for PULLBACK).

REVERT is the sleeve most exposed to any cost increase at *every* size. The exposure comes from its payoff shape, not from position size.

## 5. Concurrency and required account capital

Historical book (at any size):
- Max simultaneous positions = **8**, reached at the 09-11 cold-start burst: 8 opens in one minute.
- Time share: 7–8 open for 6.8% of the time, 1–4 open for 72%, flat for 10%.

Worst case assumed: all 8 slots are PULLBACK and all stop together at −2.07% each (SL 2.00% + 2×2 bps spread + 2×1.5 bps taker). That is 8 × 2.07% = **0.166 × S** lost in total. Crypto majors are correlated, so this scenario is plausible, not theoretical.

| Notional | Max simul. | Peak notional | Peak margin @10x | Max DD realized | MTM DD proxy | Min math capital | Practical (hist peak, ≤50% margin use) | Full 8-slot margin | All-8-stopped loss | Practical full-occupancy |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $10 | 8 | $80 | $8.00 | $1.07 | $1.13 | $9.13 | $17.13 | $8.00 | $1.66 | $17.66 |
| $20 | 8 | $160 | $16.00 | $2.13 | $2.25 | $18.25 | $34.25 | $16.00 | $3.31 | $35.31 |
| $30 | 8 | $240 | $24.00 | $3.20 | $3.38 | $27.38 | $51.38 | $24.00 | $4.97 | $52.97 |
| $40 | 8 | $320 | $32.00 | $4.27 | $4.51 | $36.51 | $68.51 | $32.00 | $6.62 | $70.62 |
| $50 | 8 | $400 | $40.00 | $5.33 | $5.64 | $45.64 | $85.64 | $40.00 | $8.28 | $88.28 |
| $75 | 8 | $600 | $60.00 | $8.00 | $8.46 | $68.46 | $128.46 | $60.00 | $12.42 | $132.42 |
| $100 | 8 | $800 | $80.00 | $10.66 | $11.27 | $91.27 | $171.27 | $80.00 | $16.56 | $176.56 |
| $150 | 8 | $1,200 | $120.00 | $16.00 | $16.91 | $136.91 | $256.91 | $120.00 | $24.84 | $264.84 |
| $200 | 8 | $1,600 | $160.00 | $21.33 | $22.55 | $182.55 | $342.55 | $160.00 | $33.12 | $353.12 |
| $300 | 8 | $2,400 | $240.00 | $31.99 | $33.82 | $273.82 | $513.82 | $240.00 | $49.68 | $529.68 |
| $400 | 8 | $3,200 | $320.00 | $42.66 | $45.10 | $365.10 | $685.10 | $320.00 | $66.24 | $706.24 |
| $500 | 8 | $4,000 | $400.00 | $53.32 | $56.37 | $456.37 | $856.37 | $400.00 | $82.80 | $882.80 |
| $750 | 8 | $6,000 | $600.00 | $79.98 | $84.56 | $684.56 | $1,284.56 | $600.00 | $124.20 | $1,324.20 |
| $1,000 | 8 | $8,000 | $800.00 | $106.64 | $112.74 | $912.74 | $1,712.74 | $800.00 | $165.60 | $1,765.60 |
| $1,250 | 8 | $10,000 | $1,000.00 | $133.30 | $140.93 | $1,140.93 | $2,140.93 | $1,000.00 | $207.00 | $2,207.00 |
| $1,500 | 8 | $12,000 | $1,200.00 | $159.97 | $169.11 | $1,369.11 | $2,569.11 | $1,200.00 | $248.40 | $2,648.40 |
| $1,750 | 8 | $14,000 | $1,400.00 | $186.63 | $197.30 | $1,597.30 | $2,997.30 | $1,400.00 | $289.80 | $3,089.80 |
| $2,000 | 8 | $16,000 | $1,600.00 | $213.29 | $225.49 | $1,825.49 | $3,425.49 | $1,600.00 | $331.20 | $3,531.20 |

- **Min math capital** = peak margin + max drawdown (MTM proxy). This is the bare minimum: margin plus the worst observed loss, with zero cushion.
- **Practical** = peak margin sized to ≤50% of equity, plus drawdown.
- **Practical full-occupancy** = 8-slot margin at ≤50% of equity + all-8-stopped loss = **1.77 × S**.

Rule of thumb: **a user needs about 1.8× the position notional in equity** to run max_open 8 at 10x with half of equity left free. For $2,000 positions that is about $3,530 (the reference book used $25,000, i.e. 7× more cushion than needed).

## 6. Small-size economics ($10–$200)

- **Fees scale with notional, so they never eat a bigger share at small sizes.**
  - Fees = 5.3% of gross profit, and fees + spread = 17.1% of gross profit, at $10 exactly as at $2,000.
  - Per winning trade, fees are 2.1% of the gross winner (7.5% including spread).
- **Net bps/trade is 28.11 at every size.**
- **What breaks small sizes is dollars and exchange granularity, not fees:**
  - **$10**: winner **6.8¢**, loser **−14.7¢**, 2.8¢/trade. All 88 winners are under 10¢. Whole week: $3.04. Pointless.
  - **$20**: winner **13.6¢**, loser −29¢. Week: $6.07. Pointless.
  - **$50**: winner **34¢**, loser −73¢, 14¢/trade. Week: $15.18.
  - **$100**: winner **68¢**, loser −$1.47, 28¢/trade. Week: $30.36. Every winner is still under $1.
  - **$200**: winner **$1.36**, loser −$2.93, 56¢/trade. Week: $60.71.
- Drawdown relative to required capital is the same at every size: max DD ≈ 10.7% of S ≈ 6.0% of the 1.77×S practical capital.
- **Funding** (proxy): about 1¢ on the whole week at $10, $0.11 at $100, $2.17 at $2,000. Immaterial at every size.
- **Off-model fixed costs:** Yellow deposits are USDT on Ethereum mainnet only (min withdrawal $5). A single mainnet transfer's gas can exceed a small account's weekly P&L. Not quantified here, because no confirmed number is on disk.

## 7. Real Yellow executability (confirmed metadata)

Source: Yellow public `/perpetual/exchangeInfo`, captured by `yellow_exec.py selftest` at 2026-09-12 06:47 UTC. The min_notional values cross-check against the 09-11 listing.

| Market | tick | qty step | min notional | $ per qty step @ sample price |
|---|---:|---:|---:|---:|
| BTC | 0.1 | 0.001 | $1 | **≈$76–78** |
| ETH | 0.01 | 0.001 | $1 | ≈$2.5 |
| SOL | 0.01 | 0.01 | $5 | ≈$1.0 |
| XRP | 0.0001 | 0.001 | $5 | ≈$0.001 |
| DOGE | 0.00001 | 1 | $5 | ≈$0.08 |
| BNB | 0.01 | 0.01 | $5 | **≈$7.2** |
| LINK | 0.001 | 0.01 | **$20** | ≈$0.11 |
| SUI | 0.0001 | 0.1 | $5 | ≈$0.07 |
| TAO | 0.01 | 0.001 | $5 | ≈$0.23 |
| HYPE | 0.001 | 0.01 | $5 | ≈$0.79 |

Rounding follows `yellow_exec.py round_qty` (ROUND_DOWN). Executable = qty ≥ 1 step and filled notional ≥ min_notional. Contract multiplier is 1 (linear USDT; qty 0.025 BTC for $2,000 confirmed). `min_qty` was not printed by the selftest, so it is assumed equal to step (**flagged**).

| Requested notional | Executable trades /108 | Skipped (market×n) | Filled/requested notional | Net P&L real rounding | Net P&L pure | Net bps (on requested ×108) | Net bps (on filled) |
|---:|---:|---|---:|---:|---:|---:|---:|
| $10 | 86 | LINK×12, BTC×10 | 76.1% | $2.56 | $3.04 | 23.66 | 31.09 |
| $20 | 87 | BTC×10, LINK×11 | 78.1% | $5.32 | $6.07 | 24.64 | 31.56 |
| $30 | 98 | BTC×10 | 89.8% | $8.05 | $9.11 | 24.84 | 27.66 |
| $40 | 98 | BTC×10 | 89.6% | $10.67 | $12.14 | 24.71 | 27.57 |
| $50 | 98 | BTC×10 | 89.5% | $13.29 | $15.18 | 24.61 | 27.50 |
| $75 | 98 | BTC×10 | 90.3% | $20.22 | $22.77 | 24.96 | 27.66 |
| $100 | 108 | — | 97.3% | $29.31 | $30.36 | 27.14 | 27.90 |
| $150 | 108 | — | 95.1% | $42.86 | $45.53 | 26.46 | 27.82 |
| $200 | 108 | — | 97.6% | $58.95 | $60.71 | 27.29 | 27.98 |
| $300 | 108 | — | 97.7% | $88.49 | $91.07 | 27.31 | 27.96 |
| $400 | 108 | — | 99.5% | $120.57 | $121.42 | 27.91 | 28.06 |
| $500 | 108 | — | 99.2% | $150.17 | $151.78 | 27.81 | 28.05 |
| $750 | 108 | — | 99.2% | $225.27 | $227.67 | 27.81 | 28.05 |
| $1,000 | 108 | — | 99.7% | $302.92 | $303.56 | 28.05 | 28.14 |
| $1,250 | 108 | — | 99.8% | $378.04 | $379.45 | 28.00 | 28.07 |
| $1,500 | 108 | — | 99.7% | $453.28 | $455.34 | 27.98 | 28.07 |
| $1,750 | 108 | — | 99.8% | $529.80 | $531.23 | 28.03 | 28.10 |
| $2,000 | 108 | — | 99.8% | $606.05 | $607.12 | 28.06 | 28.11 |

Per market (average rounding of filled vs requested notional; **NO** = not executable; `k/n` = only k of n trades executable):

| Size | BTC | ETH | SOL | XRP | DOGE | BNB | LINK | SUI | TAO | HYPE |
|---:|---|---|---|---|---|---|---|---|---|---|
| $10 | **NO** | -8.4% | -4.2% | -0.0% | -0.5% | -28.0% | **NO** | -0.2% | -1.2% | -5.1% |
| $20 | **NO** | -4.9% | -2.0% | -0.0% | -0.2% | -28.0% | 1/12 -91.7% | -0.1% | -0.6% | -1.6% |
| $30 | **NO** | -3.8% | -1.2% | +0.0% | -0.2% | -4.0% | -0.2% | -0.1% | -0.4% | -1.4% |
| $40 | **NO** | -3.2% | -1.1% | +0.0% | -0.1% | -10.0% | -0.1% | -0.0% | -0.3% | -0.8% |
| $50 | **NO** | -2.9% | -0.9% | +0.0% | -0.1% | -13.6% | -0.1% | -0.1% | -0.2% | -0.9% |
| $75 | **NO** | -1.2% | -0.5% | +0.0% | -0.0% | -4.0% | -0.1% | -0.0% | -0.2% | -0.5% |
| $100 | -23.6% | -0.8% | -0.4% | +0.0% | -0.0% | -6.4% | -0.1% | -0.0% | -0.1% | -0.4% |
| $150 | -49.0% | -0.6% | -0.3% | +0.0% | -0.0% | -4.0% | -0.0% | -0.0% | -0.1% | -0.3% |
| $200 | -23.6% | -0.5% | -0.4% | +0.0% | -0.0% | -2.8% | -0.0% | -0.0% | -0.1% | -0.2% |
| $300 | -23.6% | -0.3% | -0.1% | +0.0% | -0.0% | -1.6% | -0.0% | -0.0% | -0.0% | -0.1% |
| $400 | -4.5% | -0.3% | -0.2% | +0.0% | -0.0% | -1.0% | -0.0% | -0.0% | -0.0% | -0.1% |
| $500 | -8.3% | -0.2% | -0.1% | +0.0% | -0.0% | -0.6% | -0.0% | -0.0% | -0.0% | -0.1% |
| $750 | -8.3% | -0.2% | -0.1% | +0.0% | -0.0% | -0.6% | -0.0% | -0.0% | -0.0% | -0.1% |
| $1,000 | -2.9% | -0.1% | -0.1% | +0.0% | -0.0% | -0.3% | -0.0% | -0.0% | -0.0% | -0.0% |
| $1,250 | -2.2% | -0.1% | -0.0% | +0.0% | -0.0% | -0.4% | -0.0% | -0.0% | -0.0% | -0.0% |
| $1,500 | -3.2% | -0.1% | -0.0% | +0.0% | -0.0% | -0.2% | -0.0% | -0.0% | -0.0% | -0.0% |
| $1,750 | -2.2% | -0.1% | -0.0% | +0.0% | -0.0% | -0.2% | -0.0% | -0.0% | -0.0% | -0.0% |
| $2,000 | -1.8% | -0.1% | -0.0% | +0.0% | -0.0% | -0.2% | -0.0% | -0.0% | -0.0% | -0.0% |

Findings:
- **BTC is untradeable below about $78** (one step = 0.001 BTC ≈ $77). From $100 to $300 it fills only 1–3 steps, so BTC positions are **24–49% smaller** than requested. BTC is still 4.5–8% undersized at $400–$750, and under 3% only from $1,000.
- **LINK needs ≥ $20** after rounding down, so it is effectively ≥ $30.
- **BNB** is 4–28% undersized at $10–$50.
- From **$100** up, all 108 trades are executable; net is 26.5–28.1 bps on requested notional (vs 28.11 pure).
- From **$400** up, the real result is within 1% of pure scaling.
- Price tick is size-independent: at most about 1.4 bps (SUI, DOGE).

## 8. Cost sensitivity (same trades; fees AND spread multiplied)

| Notional | Cost × | Net P&L | Net bps/trade | PF | Avg win | Avg loss | Wins |
|---:|---:|---:|---:|---:|---:|---:|---:|
| $20 | 1.0× | $6.07 | 28.11 | 2.04 | $0.136 | -$0.293 | 88 |
| $20 | 1.5× | $5.45 | 25.21 | 1.91 | $0.130 | -$0.300 | 88 |
| $20 | 2.0× | $4.82 | 22.32 | 1.78 | $0.125 | -$0.307 | 88 |
| $50 | 1.0× | $15.18 | 28.11 | 2.04 | $0.339 | -$0.733 | 88 |
| $50 | 1.5× | $13.61 | 25.21 | 1.91 | $0.325 | -$0.750 | 88 |
| $50 | 2.0× | $12.05 | 22.32 | 1.78 | $0.311 | -$0.768 | 88 |
| $100 | 1.0× | $30.36 | 28.11 | 2.04 | $0.678 | -$1.465 | 88 |
| $100 | 1.5× | $27.23 | 25.21 | 1.91 | $0.650 | -$1.500 | 88 |
| $100 | 2.0× | $24.10 | 22.32 | 1.78 | $0.623 | -$1.535 | 88 |
| $200 | 1.0× | $60.71 | 28.11 | 2.04 | $1.356 | -$2.931 | 88 |
| $200 | 1.5× | $54.46 | 25.21 | 1.91 | $1.301 | -$3.001 | 88 |
| $200 | 2.0× | $48.21 | 22.32 | 1.78 | $1.246 | -$3.071 | 88 |
| $500 | 1.0× | $151.78 | 28.11 | 2.04 | $3.390 | -$7.327 | 88 |
| $500 | 1.5× | $136.15 | 25.21 | 1.91 | $3.252 | -$7.502 | 88 |
| $500 | 2.0× | $120.52 | 22.32 | 1.78 | $3.114 | -$7.676 | 88 |
| $1,000 | 1.0× | $303.56 | 28.11 | 2.04 | $6.780 | -$14.654 | 88 |
| $1,000 | 1.5× | $272.30 | 25.21 | 1.91 | $6.504 | -$15.003 | 88 |
| $1,000 | 2.0× | $241.03 | 22.32 | 1.78 | $6.228 | -$15.353 | 88 |
| $2,000 | 1.0× | $607.12 | 28.11 | 2.04 | $13.560 | -$29.308 | 88 |
| $2,000 | 1.5× | $544.59 | 25.21 | 1.91 | $13.008 | -$30.006 | 88 |
| $2,000 | 2.0× | $482.06 | 22.32 | 1.78 | $12.456 | -$30.705 | 88 |

Wins stay at 88/108 even at 2× costs. Net bps: 28.1 → 25.2 → 22.3. PF: 2.04 → 1.91 → 1.79. Identical in bps at every size.

**Public fee tier (what a normal customer pays, confirmed on disk):** Yellow tier 0 is maker 1 bp / taker 4 bps. The book's 1.5/0 is the owner's negotiated VIP5 rate.

| Notional | Net P&L VIP5 | Net P&L public tier 0 | bps VIP5 | bps tier 0 | PF tier 0 | Avg win tier 0 | Avg loss tier 0 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| $10 | $3.04 | $2.63 | 28.11 | 24.32 | 1.87 | $0.064 | -$0.152 |
| $20 | $6.07 | $5.25 | 28.11 | 24.32 | 1.87 | $0.129 | -$0.303 |
| $50 | $15.18 | $13.13 | 28.11 | 24.32 | 1.87 | $0.321 | -$0.758 |
| $100 | $30.36 | $26.26 | 28.11 | 24.32 | 1.87 | $0.643 | -$1.515 |
| $200 | $60.71 | $52.52 | 28.11 | 24.32 | 1.87 | $1.286 | -$3.031 |
| $500 | $151.78 | $131.31 | 28.11 | 24.32 | 1.87 | $3.214 | -$7.577 |
| $1,000 | $303.56 | $262.61 | 28.11 | 24.32 | 1.87 | $6.428 | -$15.154 |
| $2,000 | $607.12 | $525.22 | 28.11 | 24.32 | 1.87 | $12.857 | -$30.308 |

## 9. Integrity

| Check | Result |
|---|---|
| Journal sha256 before = after | `d7737c8b4a1144de4f57c0593da350ba652cce8c5a92475197178e20aed7a6cb` (identical) |
| Trade-set hash (open/close ts, market, side, strategy, entry, exit, reason) | `86e0a0abd5279056c23d6c9843c5c4be9c79862241166f0bebd6001e5743d2da`, identical at all 18 sizes, 3 cost multipliers and every sleeve slice (asserted in code) |
| Trade count | 108 in, 108 out at every pure-scaling size; none added, none removed |
| Timestamps / markets / directions / entry / exit prices / exit reasons / strategy labels | unchanged (part of the identity tuple) |
| What changed | only notional-proportional arithmetic: gross, fees, spread cost, net |
| Real-rounding section | the *only* place trades drop out (non-executable BTC/LINK), reported explicitly per size and market |

Files: `yellow_sizing.py`, `YELLOW_SIZING_RESULTS.csv` (sections PURE, SLEEVE, COST_SENS, PUBLIC_TIER0_FEES, REAL_ROUNDING, EXEC_MARKET), `results.json` (everything), `source_manifest.json`, `YELLOW_RISK_MODEL.md`.
