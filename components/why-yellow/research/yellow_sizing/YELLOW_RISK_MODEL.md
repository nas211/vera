# WHY × Yellow — Risk-Level Sizing Model (proposal, not implemented)

Derived from `YELLOW_SIZING_REPORT.md`. Nothing here is wired into any system.

## What the audit established (the inputs)

1. **The strategy scales linearly, with no minimum.** Net is 28.11 bps/trade, PF 2.04 and win rate 81.5% at every notional from $10 to $2,000. Every cost (fees, spread, funding) is proportional to notional. There is no fixed per-trade cost.
2. **The exchange imposes the floor, not the economics.**
   - BTC's qty step is 0.001 (≈$77), so BTC cannot trade below about $78 and is 24–49% undersized from $100 to $300.
   - LINK needs a $20 minimum notional.
   - From **$100** up, all 10 markets and all 108 trades are executable. Real net is 26.5–28 bps vs 28.1 pure.
3. **Concurrency hits the cap.** The historical book held **8** positions at once (= max_open). Size for 8.
4. **Per-trade worst loss** L = SL + 2×spread + 2×taker = 2.00% + 0.04% + 0.03% = **2.07% of notional** at VIP5 (2.12% at the public tier). Correlated majors can stop together, so 8 × L is the tail to plan for.

## Model comparison across balances

Hist. net $/trade = the week's 28.11 bps × notional (VIP5).

**Balance $100**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $8.00 | $0.80 | 6% | yes | 0.17% | 1.3% | $0.022 | 8/10 (no BTC) |
| A fixed $200 notional | $200.00 | $20.00 | 160% | **NO** | 4.14% | 33.1% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 160% | **NO** | 4.14% | 33.1% | $0.562 | 10/10 |
| C 5% equity margin/trade | $50.00 | $5.00 | 40% | yes | 1.04% | 8.3% | $0.141 | 9/10 (no BTC) |
| D equity/max_open, 50% reserve | $62.50 | $6.25 | 50% | yes | 1.29% | 10.4% | $0.176 | 9/10 (no BTC) |
| PROPOSED LOW (0.5% equity at risk/trade) | $24.15 | $2.42 | 19% | yes | 0.50% | 4.0% | $0.068 | 9/10 (no BTC) |
| PROPOSED MEDIUM (1% equity at risk/trade) | $48.31 | $4.83 | 39% | yes | 1.00% | 8.0% | $0.136 | 9/10 (no BTC) |
| PROPOSED HIGH (1.25% equity at risk/trade) | $60.39 | $6.04 | 48% | yes | 1.25% | 10.0% | $0.170 | 9/10 (no BTC) |

**Balance $250**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $20.00 | $2.00 | 6% | yes | 0.17% | 1.3% | $0.056 | 8/10 (no BTC) |
| A fixed $200 notional | $200.00 | $20.00 | 64% | yes | 1.66% | 13.2% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 64% | yes | 1.66% | 13.2% | $0.562 | 10/10 |
| C 5% equity margin/trade | $125.00 | $12.50 | 40% | yes | 1.04% | 8.3% | $0.351 | 10/10 |
| D equity/max_open, 50% reserve | $156.25 | $15.62 | 50% | yes | 1.29% | 10.4% | $0.439 | 10/10 |
| PROPOSED LOW (0.5% equity at risk/trade) | $60.39 | $6.04 | 19% | yes | 0.50% | 4.0% | $0.170 | 9/10 (no BTC) |
| PROPOSED MEDIUM (1% equity at risk/trade) | $120.77 | $12.08 | 39% | yes | 1.00% | 8.0% | $0.339 | 10/10 |
| PROPOSED HIGH (1.25% equity at risk/trade) | $150.97 | $15.10 | 48% | yes | 1.25% | 10.0% | $0.424 | 10/10 |

**Balance $500**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $40.00 | $4.00 | 6% | yes | 0.17% | 1.3% | $0.112 | 9/10 (no BTC) |
| A fixed $200 notional | $200.00 | $20.00 | 32% | yes | 0.83% | 6.6% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 32% | yes | 0.83% | 6.6% | $0.562 | 10/10 |
| C 5% equity margin/trade | $250.00 | $25.00 | 40% | yes | 1.04% | 8.3% | $0.703 | 10/10 |
| D equity/max_open, 50% reserve | $312.50 | $31.25 | 50% | yes | 1.29% | 10.4% | $0.878 | 10/10 |
| PROPOSED LOW (0.5% equity at risk/trade) | $120.77 | $12.08 | 19% | yes | 0.50% | 4.0% | $0.339 | 10/10 |
| PROPOSED MEDIUM (1% equity at risk/trade) | $241.55 | $24.15 | 39% | yes | 1.00% | 8.0% | $0.679 | 10/10 |
| PROPOSED HIGH (1.25% equity at risk/trade) | $301.93 | $30.19 | 48% | yes | 1.25% | 10.0% | $0.849 | 10/10 |

**Balance $1,000**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $80.00 | $8.00 | 6% | yes | 0.17% | 1.3% | $0.225 | 10/10 |
| A fixed $200 notional | $200.00 | $20.00 | 16% | yes | 0.41% | 3.3% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 16% | yes | 0.41% | 3.3% | $0.562 | 10/10 |
| C 5% equity margin/trade | $500.00 | $50.00 | 40% | yes | 1.04% | 8.3% | $1.405 | 10/10 |
| D equity/max_open, 50% reserve | $625.00 | $62.50 | 50% | yes | 1.29% | 10.4% | $1.757 | 10/10 |
| PROPOSED LOW (0.5% equity at risk/trade) | $241.55 | $24.15 | 19% | yes | 0.50% | 4.0% | $0.679 | 10/10 |
| PROPOSED MEDIUM (1% equity at risk/trade) | $483.09 | $48.31 | 39% | yes | 1.00% | 8.0% | $1.358 | 10/10 |
| PROPOSED HIGH (1.25% equity at risk/trade) | $603.86 | $60.39 | 48% | yes | 1.25% | 10.0% | $1.697 | 10/10 |

**Balance $2,500**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $200.00 | $20.00 | 6% | yes | 0.17% | 1.3% | $0.562 | 10/10 |
| A fixed $200 notional | $200.00 | $20.00 | 6% | yes | 0.17% | 1.3% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 6% | yes | 0.17% | 1.3% | $0.562 | 10/10 |
| C 5% equity margin/trade | $1,250.00 | $125.00 | 40% | yes | 1.04% | 8.3% | $3.513 | 10/10 |
| D equity/max_open, 50% reserve | $1,562.50 | $156.25 | 50% | yes | 1.29% | 10.4% | $4.392 | 10/10 |
| PROPOSED LOW (0.5% equity at risk/trade) | $603.86 | $60.39 | 19% | yes | 0.50% | 4.0% | $1.697 | 10/10 |
| PROPOSED MEDIUM (1% equity at risk/trade) | $1,207.73 | $120.77 | 39% | yes | 1.00% | 8.0% | $3.395 | 10/10 |
| PROPOSED HIGH (1.25% equity at risk/trade) | $1,509.66 | $150.97 | 48% | yes | 1.25% | 10.0% | $4.243 | 10/10 |

**Balance $5,000**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $400.00 | $40.00 | 6% | yes | 0.17% | 1.3% | $1.124 | 10/10 |
| A fixed $200 notional | $200.00 | $20.00 | 3% | yes | 0.08% | 0.7% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 3% | yes | 0.08% | 0.7% | $0.562 | 10/10 |
| C 5% equity margin/trade | $2,500.00 | $250.00 | 40% | yes | 1.04% | 8.3% | $7.027 | 10/10 |
| D equity/max_open, 50% reserve | $3,125.00 | $312.50 | 50% | yes | 1.29% | 10.4% | $8.784 | 10/10 |
| PROPOSED LOW (0.5% equity at risk/trade) | $1,207.73 | $120.77 | 19% | yes | 0.50% | 4.0% | $3.395 | 10/10 |
| PROPOSED MEDIUM (1% equity at risk/trade) | $2,415.46 | $241.55 | 39% | yes | 1.00% | 8.0% | $6.789 | 10/10 |
| PROPOSED HIGH (1.25% equity at risk/trade) | $3,019.32 | $301.93 | 48% | yes | 1.25% | 10.0% | $8.487 | 10/10 |

**Balance $10,000**

| Model | Position notional | Margin/trade | 8-slot margin % equity | Fits? | Risk/trade % equity | All-8-stopped % equity | Hist. net $/trade | Markets executable |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| REF book intensity ($2k clip / $25k) | $800.00 | $80.00 | 6% | yes | 0.17% | 1.3% | $2.249 | 10/10 |
| A fixed $200 notional | $200.00 | $20.00 | 2% | yes | 0.04% | 0.3% | $0.562 | 10/10 |
| B fixed $20 margin @10x | $200.00 | $20.00 | 2% | yes | 0.04% | 0.3% | $0.562 | 10/10 |
| C 5% equity margin/trade | $5,000.00 | $500.00 | 40% | yes | 1.04% | 8.3% | $14.054 | 10/10 |
| D equity/max_open, 50% reserve | $6,250.00 | $625.00 | 50% | yes | 1.29% | 10.4% | $17.567 | 10/10 |
| PROPOSED LOW (0.5% equity at risk/trade) | $2,415.46 | $241.55 | 19% | yes | 0.50% | 4.0% | $6.789 | 10/10 |
| PROPOSED MEDIUM (1% equity at risk/trade) | $4,830.92 | $483.09 | 39% | yes | 1.00% | 8.0% | $13.578 | 10/10 |
| PROPOSED HIGH (1.25% equity at risk/trade) | $6,038.65 | $603.86 | 48% | yes | 1.25% | 10.0% | $16.973 | 10/10 |

Why each model does or does not work:
- **A (fixed notional) and B (fixed margin) are the same model at fixed 10x.** They ignore equity.
  - At $200 they cannot fit a $100 account (8 slots need 160% of equity as margin), and an all-8 stop would cost 33% of it.
  - On a $10k account the same $200 risks 0.04% per trade.
  - **Reject.**
- **C (fixed % of equity as margin)** scales properly, but the percentage is arbitrary: it says nothing about the stop distance, so the risk it takes is implicit.
- **D (equity ÷ max_open with a reserve)** is the right *ceiling*: it guarantees free collateral when every slot is full. Used alone it is always the most aggressive setting.
- **Recommended: C sized by stop-risk, capped by D, floored by the exchange.** That is the formula below.

## Proposed LOW / MEDIUM / HIGH

Inputs read from the user's Yellow account and the venue:
- E = account equity (total_account_equity, cross-margin).
- LEV = 10, MAX_OPEN = 8.
- L = SL_PULLBACK + 2 × spread + 2 × taker_fee_of_THIS_account (2.07% VIP5, 2.12% public tier 0).
- u = 0.50 (max share of equity used as margin when all 8 slots are full).

```
r        = 0.005 (LOW) | 0.010 (MEDIUM) | 0.0125 (HIGH)      # equity lost if ONE position hits its stop
S_risk   = r × E / L
S_margin = u × E × LEV / MAX_OPEN          # = 0.625 × E  (free-collateral ceiling)
S        = floor( min(S_risk, S_margin) / 5 ) × 5            # position notional, USD
margin   = S / LEV

per market at order time:
  qty = floor( S / price / step ) × step
  skip the market if qty × price < min_notional  (or qty < min_qty)
level unavailable if S < S_MIN = $100            # the smallest size where all 10 markets execute
```

The constants come from the stop geometry, the max_open cap and the exchange's step sizes. **None was tuned to this week's P&L.** Only the "hist." columns below use the sample.

| Level | Risk/trade (% equity) | Notional formula | 8-slot margin use | All 8 stopped at once | Hist. max DD (realized / MTM proxy) | Hist. net per trade | Min balance for $100 floor |
|---|---:|---|---:|---:|---:|---:|---:|
| LOW | 0.5% | S = 0.242 × E | 19% | −4.0% | −2.6% / −2.7% | 6.8 bps of equity ($0.68 per $1k) | $414 |
| MEDIUM | 1% | S = 0.483 × E | 39% | −8.0% | −5.2% / −5.4% | 13.6 bps of equity ($1.36 per $1k) | $207 |
| HIGH | 1.25% | S = 0.604 × E | 48% | −10.0% | −6.4% / −6.8% | 17.0 bps of equity ($1.70 per $1k) | $166 |

For reference, the $25k/$2k book the owner ran is S = 0.080 × E (risk 0.17%/trade, all-8-stopped −1.3%). It sits well below LOW.

### What each balance gets (VIP5 L; floor $100)

| Balance | LOW notional (margin) | MEDIUM notional (margin) | HIGH notional (margin) |
|---:|---:|---:|---:|
| $100 | $20 ($2.0) → **below $100 floor: unavailable** | $45 ($4.5) → **below $100 floor: unavailable** | $60 ($6.0) → **below $100 floor: unavailable** |
| $250 | $60 ($6.0) → **below $100 floor: unavailable** | $120 ($12.0) | $150 ($15.0) |
| $500 | $120 ($12.0) | $240 ($24.0) | $300 ($30.0) |
| $1,000 | $240 ($24.0) | $480 ($48.0) | $600 ($60.0) |
| $2,500 | $600 ($60.0) | $1,205 ($120.5) | $1,505 ($150.5) |
| $5,000 | $1,205 ($120.5) | $2,415 ($241.5) | $3,015 ($301.5) |
| $10,000 | $2,415 ($241.5) | $4,830 ($483.0) | $6,035 ($603.5) |

- **Platform minimum: $250.** It enables MEDIUM ($120) and HIGH ($150).
- **LOW needs ≥ $500** ($414 exactly), or LOW positions would sit below the $100 floor.
- A $100 account is too small at any level: it cannot hold 8 positions of ≥ $100 with 50% free collateral.
- At $250–$1,000, BTC positions are rounded down 1–7 steps (up to −49%). The product must choose between skipping BTC, accepting undersized BTC, or rounding BTC to the nearest step *only when that stays inside S_risk*. That is a product decision, left open.

## Must be confirmed before customer money

1. **A fresh `/perpetual/exchangeInfo`.** The only capture is from 09-12, before the 09-18 infra outage. It is also the only source of the tick/step/min_notional values used above. **min_qty was never captured.**
2. **Maintenance-margin rates and the liquidation formula for cross-margin.** The u = 50% headroom is a margin-use rule, not a verified distance to liquidation.
3. **The customer's actual fee tier.** L and expected bps depend on it: VIP5 1.5/0 bps gives 28.1 bps; public tier 0 at 1/4 bps gives 24.3 bps. Read it per account (the adapter has `market_fee_rate`) instead of assuming VIP5.
4. **Yellow funding interval, rate history and caps.** Not on disk. The Bybit proxy is about 0.1 bps/trade.
5. **Whether reduce_only closes are exempt from min_notional,** and whether 10x can be set per market for the user's account (the API rejects leverage changes while that market has open orders).
6. **Venue trigger (stop) order semantics.** Still unvalidated; stops are engine-managed. During a venue outage like 09-18 (6h+), customer positions would have no protective orders.
