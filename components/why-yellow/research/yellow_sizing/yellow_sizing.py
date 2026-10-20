#!/usr/bin/env python3
"""OFFLINE sizing audit of the WHY x Yellow WY book (measurement only; reads files, writes this directory only).

Sample   : /opt/why-yellow/state/journal.jsonl  (108 OPEN + 108 CLOSE, frozen since 09-18 01:18 UTC)
Engine   : /opt/why-yellow/yellow_paper.py       (fill + fee formulas replicated verbatim below)
Costs    : why-yellow.service env  WY_TAKER_BPS=1.5  WY_MAKER_BPS=0.0  WY_SPREAD_BPS=2.0  (VIP5, owner-confirmed 09-17)
           entry: taker on notional; exit: maker if TARGET else taker; spread applied to BOTH fill prices; no funding.
Metadata : Yellow public /perpetual/exchangeInfo captured by `yellow_exec.py selftest` 2026-09-12 06:47 UTC
           (tick / step / min_notional; min_qty NOT printed -> assumed == step, flagged).
Funding  : NOT in the accepted model. Shown separately as an informational proxy from Bybit 8h prints
           (/opt/why-bybit/validation/data_bybit/fund_*.json) - Yellow funding history is not on disk.
MTM DD   : informational proxy using Bybit 5m closes (/opt/why-bybit/validation/data_bybit/k5_*.json).

Nothing in here places orders, opens sockets, or touches a service.
"""
import csv, hashlib, json, math, os, statistics as st, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = "/opt/why-yellow/state/journal.jsonl"
JOURNAL_PRE = "/opt/why-yellow/state/journal.jsonl.bak.pre-vip5"
SNAPSHOT = "/opt/why-yellow/state/snapshot.json"
UNIT = "/etc/systemd/system/why-yellow.service"
ENGINE = "/opt/why-yellow/yellow_paper.py"
EXEC = "/opt/why-yellow/yellow_exec.py"
BYBIT = "/opt/why-bybit/validation/data_bybit"

REF_NOTIONAL = 2000.0
LEV = 10.0
MAX_OPEN = 8
TAKER_BPS, MAKER_BPS, SPREAD_BPS = 1.5, 0.0, 2.0          # deployed VIP5 (unit file)
TIER0_TAKER, TIER0_MAKER = 4.0, 1.0                        # Yellow PUBLIC tier 0 (exchangeInfo mk/tk 1.0/4.0, fee_schedule tier 0) - what a non-VIP customer pays
SL_PCT = {"WY-PULLBACK": 2.00, "WY-REVERT": 1.60}          # engine CFG
TP_PCT = {"WY-PULLBACK": 0.80, "WY-REVERT": 0.30}

SIZES = [10, 20, 30, 40, 50, 75, 100, 150, 200, 300, 400, 500, 750, 1000, 1250, 1500, 1750, 2000]
SENS_SIZES = [20, 50, 100, 200, 500, 1000, 2000]
SMALL = [10, 20, 30, 40, 50, 75, 100, 150, 200]
BALANCES = [100, 250, 500, 1000, 2500, 5000, 10000]
HEADROOM_MARGIN_UTIL = 0.50   # practical rule: margin in use at the peak must stay <= 50% of equity

# Yellow exchangeInfo, 2026-09-12 06:47 UTC selftest (verbatim from the printed table)
YMETA = {
    "BTCUSDT-PERP":  dict(tick=0.1,      step=0.001, min_notional=1),
    "ETHUSDT-PERP":  dict(tick=0.01,     step=0.001, min_notional=1),
    "SOLUSDT-PERP":  dict(tick=0.01,     step=0.01,  min_notional=5),
    "XRPUSDT-PERP":  dict(tick=0.0001,   step=0.001, min_notional=5),
    "DOGEUSDT-PERP": dict(tick=0.00001,  step=1,     min_notional=5),
    "BNBUSDT-PERP":  dict(tick=0.01,     step=0.01,  min_notional=5),
    "LINKUSDT-PERP": dict(tick=0.001,    step=0.01,  min_notional=20),
    "SUIUSDT-PERP":  dict(tick=0.0001,   step=0.1,   min_notional=5),
    "TAOUSDT-PERP":  dict(tick=0.01,     step=0.001, min_notional=5),
    "HYPEUSDT-PERP": dict(tick=0.001,    step=0.01,  min_notional=5),
}

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f: h.update(f.read())
    return h.hexdigest()

# ---------------------------------------------------------------- load + pair
def load_trades(path):
    rows = [json.loads(l) for l in open(path)]
    openq = defaultdict(list); trades = []
    for r in rows:
        if r["event"] == "OPEN": openq[r["market"]].append(r)
        elif r["event"] == "CLOSE":
            o = openq[r["market"]].pop(0)
            assert (o["side"], o["strategy"], o["notional"]) == (r["side"], r["strategy"], r["notional"]), r
            assert abs(o["entry"] - r["entry"]) < 1e-12, r
            trades.append(dict(open_ts=o["ts"], close_ts=r["ts"], market=r["market"], side=r["side"], strategy=r["strategy"],
                               entry=r["entry"], exit=r["exit"], reason=r["reason"], notional=r["notional"],
                               j_pnl=r["pnl_usd"], j_fees=r["fees_usd"], j_pnl_pct=r["pnl_pct"], hold_min=r["hold_min"]))
    left = sum(len(v) for v in openq.values())
    return trades, left

def identity(t):  # everything the replay must NOT change
    return (t["open_ts"], t["close_ts"], t["market"], t["side"], t["strategy"], repr(t["entry"]), repr(t["exit"]), t["reason"])

def set_hash(trades):
    return hashlib.sha256(json.dumps([identity(t) for t in trades]).encode()).hexdigest()

# ---------------------------------------------------------------- engine arithmetic (verbatim from yellow_paper.py)
def pnl_pct(t):
    return ((t["exit"] - t["entry"]) if t["side"] == "long" else (t["entry"] - t["exit"])) / t["entry"] * 100

def engine_pnl(t, notional, taker, maker):
    fees = notional * taker / 1e4 + notional * (maker if t["reason"] == "TARGET" else taker) / 1e4
    return notional * pnl_pct(t) / 100 - fees, fees

def decompose(t):
    """Per-$1-of-notional cost decomposition at the deployed model. Mid prices recovered by undoing the 2bps spread."""
    s = SPREAD_BPS / 1e4
    if t["side"] == "long": e_mid, x_mid = t["entry"] / (1 + s), t["exit"] / (1 - s)
    else:                   e_mid, x_mid = t["entry"] / (1 - s), t["exit"] / (1 + s)
    gross = ((x_mid - e_mid) if t["side"] == "long" else (e_mid - x_mid)) / t["entry"]   # per $ notional (engine sizes on fill entry)
    after_spread = pnl_pct(t) / 100
    fee_in = TAKER_BPS / 1e4
    fee_out = (MAKER_BPS if t["reason"] == "TARGET" else TAKER_BPS) / 1e4
    return dict(gross=gross, slip=gross - after_spread, fee_in=fee_in, fee_out=fee_out,
                net=after_spread - fee_in - fee_out, e_mid=e_mid, x_mid=x_mid)

# ---------------------------------------------------------------- funding proxy (NOT in accepted model)
def load_funding():
    F = {}
    for m in YMETA:
        p = f"{BYBIT}/fund_{m.replace('-PERP','')}.json"
        F[m] = sorted((int(k), float(v)) for k, v in json.load(open(p)).items()) if os.path.exists(p) else []
    return F

def funding_frac(t, F):
    """fraction of notional PAID (positive = cost) over the hold; longs pay positive rates."""
    tot = sum(r for ts, r in F[t["market"]] if t["open_ts"] < ts <= t["close_ts"])
    return tot if t["side"] == "long" else -tot

# ---------------------------------------------------------------- metrics
def max_dd(pnls_in_order):
    eq = pk = dd = 0.0
    for p in pnls_in_order:
        eq += p; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dd

def stats(trades, nets, grosses=None, notional_each=None, fees=None, slips=None, fund=None):
    n = len(nets)
    if n == 0: return dict(trades=0)
    w = [x for x in nets if x > 0]; l = [x for x in nets if x <= 0]
    order = sorted(range(n), key=lambda i: trades[i]["close_ts"])
    notl = notional_each if notional_each is not None else [REF_NOTIONAL] * n
    tot_notl = sum(notl)
    out = dict(trades=n, wins=len(w), losses=len(l), win_rate=100 * len(w) / n,
               net=sum(nets), avg=sum(nets) / n, median=st.median(nets),
               avg_win=(sum(w) / len(w)) if w else 0.0, avg_loss=(sum(l) / len(l)) if l else 0.0,
               best=max(nets), worst=min(nets),
               pf=(sum(w) / -sum(l)) if l and sum(l) < 0 else float("inf"),
               max_dd=max_dd([nets[i] for i in order]),
               net_bps=(sum(nets) / tot_notl * 1e4) if tot_notl else 0.0)
    out["wl_ratio"] = (out["avg_win"] / -out["avg_loss"]) if out["avg_loss"] < 0 else float("inf")
    if grosses is not None:
        out["gross"] = sum(grosses)
    if fees is not None:
        out["fee_in"] = sum(f[0] for f in fees); out["fee_out"] = sum(f[1] for f in fees)
        out["fees"] = out["fee_in"] + out["fee_out"]
    if slips is not None: out["slip"] = sum(slips)
    if fund is not None: out["funding_proxy"] = sum(fund)
    out["total_cost"] = out.get("fees", 0) + out.get("slip", 0)
    return out

# ---------------------------------------------------------------- concurrency
def concurrency(trades):
    ev = []
    for i, t in enumerate(trades):
        ev.append((t["open_ts"], 1, i)); ev.append((t["close_ts"], -1, i))
    ev.sort(key=lambda e: (e[0], e[1]))       # closes before opens at identical ms
    cur = set(); peak = 0; peak_risk = 0.0; peak_at = None; hist = defaultdict(float); last = None
    for ts, d, i in ev:
        if last is not None and cur: hist[len(cur)] += ts - last
        last = ts
        if d == 1: cur.add(i)
        else: cur.discard(i)
        if len(cur) > peak: peak, peak_at = len(cur), ts
        risk = sum(SL_PCT[trades[j]["strategy"]] / 100 + 2 * SPREAD_BPS / 1e4 + 2 * TAKER_BPS / 1e4 for j in cur)
        peak_risk = max(peak_risk, risk)
    span = trades and (max(t["close_ts"] for t in trades) - min(t["open_ts"] for t in trades))
    return dict(max_simul=peak, peak_at=peak_at, peak_stop_risk_frac=peak_risk,
                time_at_n={k: v / span for k, v in sorted(hist.items())})

def mtm_dd_proxy(trades):
    """Mark-to-market drawdown at $REF using Bybit 5m closes as the mark proxy. Realized P&L booked at close_ts."""
    K = {}
    for m in YMETA:
        p = f"{BYBIT}/k5_{m.replace('-PERP','')}.json"
        if not os.path.exists(p): return None
        K[m] = {int(r[0]) + 300000: float(r[4]) for r in json.load(open(p))}   # key = bar CLOSE time
    t0 = min(t["open_ts"] for t in trades); t1 = max(t["close_ts"] for t in trades)
    grid = sorted({ts for ts in K["BTCUSDT-PERP"] if t0 <= ts <= t1 + 300000})
    realized = sorted((t["close_ts"], engine_pnl(t, REF_NOTIONAL, TAKER_BPS, MAKER_BPS)[0]) for t in trades)
    eq_pts = []; ri = 0; racc = 0.0
    for g in grid:
        while ri < len(realized) and realized[ri][0] <= g: racc += realized[ri][1]; ri += 1
        u = 0.0
        for t in trades:
            if t["open_ts"] <= g < t["close_ts"]:
                c = K[t["market"]].get(g)
                if c is None: continue
                mv = (c - t["entry"]) / t["entry"] if t["side"] == "long" else (t["entry"] - c) / t["entry"]
                u += REF_NOTIONAL * mv - REF_NOTIONAL * TAKER_BPS / 1e4
        eq_pts.append(racc + u)
    pk = dd = 0.0
    for e in eq_pts: pk = max(pk, e); dd = max(dd, pk - e)
    return dict(mtm_dd_ref=dd, grid_bars=len(grid))

# ---------------------------------------------------------------- real rounding
def real_fill(t, S):
    m = YMETA[t["market"]]; d = decompose(t)
    px = d["e_mid"]                                   # executor sizes off top-of-book, i.e. ~mid
    q = math.floor(S / px / m["step"] + 1e-9) * m["step"]
    actual = q * t["entry"]                           # filled notional at the (spread-adjusted) fill price
    ok = q >= m["step"] - 1e-12 and actual >= m["min_notional"]
    return q, actual, ok

# ---------------------------------------------------------------- main
def main():
    out_rows = []; R = {}
    h_before = sha(JOURNAL)
    trades, orphans = load_trades(JOURNAL)
    trades.sort(key=lambda t: t["close_ts"])
    ids0 = [identity(t) for t in trades]; H0 = set_hash(trades)
    snap = json.load(open(SNAPSHOT))

    # ---- STEP 1: reproduce the accepted reference
    rec = [engine_pnl(t, t["notional"], TAKER_BPS, MAKER_BPS) for t in trades]
    mism_pnl = [t for t, (p, f) in zip(trades, rec) if round(p, 2) != t["j_pnl"]]
    mism_fee = [t for t, (p, f) in zip(trades, rec) if round(f, 2) != t["j_fees"]]
    mism_pct = [t for t in trades if round(pnl_pct(t), 4) != t["j_pnl_pct"]]
    j_net = sum(t["j_pnl"] for t in trades); j_fees = sum(t["j_fees"] for t in trades)
    j_wins = sum(t["j_pnl"] > 0 for t in trades)
    exact_net = sum(p for p, _ in rec); exact_wins = sum(p > 0 for p, _ in rec)
    pre, pre_orph = load_trades(JOURNAL_PRE)
    # backup = history only: it mixes fee eras (4/1 bps then 1/0 bps under the old "VIP6" label); the 09-17 re-stamp put every row on VIP5.
    pre_fee_eras = sorted({(t["reason"], t["j_fees"]) for t in pre})
    pre_prices_equal = all(identity(a) == identity(b) for a, b in zip(sorted(pre, key=lambda t: t["close_ts"]), sorted(trades, key=lambda t: t["close_ts"])))
    reproduced = (not mism_pnl and not mism_fee and not mism_pct and len(trades) == 108 and j_wins == 88
                  and abs(j_net - exact_net) < 0.02 and abs(snap["stats"]["net_usd"] - exact_net) < 0.02 and snap["stats"]["trades"] == 108 and snap["stats"]["wins"] == 88)
    R["reference"] = dict(trades=len(trades), orphans_open=orphans, wins=j_wins, losses=len(trades) - j_wins,
                          win_rate=100 * j_wins / len(trades), journal_net=j_net, journal_fees=j_fees,
                          snapshot_net=snap["stats"]["net_usd"], snapshot_fees=snap["stats"]["fees_usd"],
                          snapshot_trades=snap["stats"]["trades"], snapshot_wins=snap["stats"]["wins"],
                          recomputed_exact_net=exact_net, recomputed_exact_wins=exact_wins,
                          row_mismatch_pnl=len(mism_pnl), row_mismatch_fees=len(mism_fee), row_mismatch_pct=len(mism_pct),
                          pre_vip5_rows=len(pre), pre_vip5_fee_eras=pre_fee_eras, pre_vip5_identity_equal_on_overlap=pre_prices_equal, reproduced=reproduced,
                          by_strat=snap["stats"]["by_strat"])
    if not reproduced:
        print("REFERENCE NOT REPRODUCED - STOP"); print(json.dumps(R["reference"], indent=1, default=str)); sys.exit(2)

    D = [decompose(t) for t in trades]
    F = load_funding()
    FU = [funding_frac(t, F) for t in trades]
    for t, d in zip(trades, D):   # decomposition must re-add to the engine net at $2000 to the cent
        assert abs(d["net"] * t["notional"] - engine_pnl(t, t["notional"], TAKER_BPS, MAKER_BPS)[0]) < 1e-9

    # sample facts
    R["sample"] = dict(
        first_open=min(t["open_ts"] for t in trades), last_close=max(t["close_ts"] for t in trades),
        markets=sorted({t["market"] for t in trades}),
        by_market={m: sum(t["market"] == m for t in trades) for m in YMETA},
        by_reason={r: sum(t["reason"] == r for t in trades) for r in ("TARGET", "STOP", "TIMEOUT")},
        by_side={s: sum(t["side"] == s for t in trades) for s in ("long", "short")},
        by_strategy={s: sum(t["strategy"] == s for t in trades) for s in SL_PCT},
        notional_all=sorted({t["notional"] for t in trades}),
        hold_min_median=st.median(t["hold_min"] for t in trades), hold_min_max=max(t["hold_min"] for t in trades))
    R["concurrency"] = concurrency(trades)
    R["mtm"] = mtm_dd_proxy(trades)

    # ---- STEP 3/4/5/6: pure scaling, per sleeve
    def run(S, sel=lambda t: True, cost_mult=1.0):
        idx = [i for i, t in enumerate(trades) if sel(t)]
        sub = [trades[i] for i in idx]
        g = [D[i]["gross"] * S for i in idx]
        sl = [D[i]["slip"] * S * cost_mult for i in idx]
        fe = [(D[i]["fee_in"] * S * cost_mult, D[i]["fee_out"] * S * cost_mult) for i in idx]
        nets = [gg - ss - a - b for gg, ss, (a, b) in zip(g, sl, fe)]
        fu = [FU[i] * S for i in idx]
        o = stats(sub, nets, g, [S] * len(idx), fe, sl, fu)
        w_idx = [k for k, x in enumerate(nets) if x > 0]
        o["avg_gross_winner"] = (sum(g[k] for k in w_idx) / len(w_idx)) if w_idx else 0
        o["avg_fees_winner"] = (sum(fe[k][0] + fe[k][1] for k in w_idx) / len(w_idx)) if w_idx else 0
        o["avg_cost_winner"] = (sum(fe[k][0] + fe[k][1] + sl[k] for k in w_idx) / len(w_idx)) if w_idx else 0
        o["winners_under_1usd"] = sum(0 < x < 1 for x in nets)
        o["winners_under_10c"] = sum(0 < x < 0.10 for x in nets)
        o["target"] = sum(t["reason"] == "TARGET" for t in sub); o["stop"] = sum(t["reason"] == "STOP" for t in sub)
        o["timeout"] = sum(t["reason"] == "TIMEOUT" for t in sub)
        o["median_win"] = st.median([x for x in nets if x > 0]) if w_idx else 0
        o["median_loss"] = st.median([x for x in nets if x <= 0]) if len(w_idx) < len(nets) else 0
        o["net_incl_funding_proxy"] = o["net"] - o["funding_proxy"]
        return o, [identity(t) for t in sub]

    PURE = {}; SLEEVE = {}; SENS = {}
    for S in SIZES:
        o, ids = run(S); assert ids == ids0, "trade set changed"
        PURE[S] = o
        for sname in SL_PCT:
            SLEEVE[(S, sname)], _ = run(S, lambda t, s=sname: t["strategy"] == s)
    for S in SENS_SIZES:
        for cm in (1.0, 1.5, 2.0):
            SENS[(S, cm)], ids = run(S, cost_mult=cm); assert ids == ids0
    TIER0 = {}
    for S in SIZES:   # same trades, same spread; only the fee schedule swapped to Yellow's public tier 0
        nets = []; fees_t = []
        for t in trades:
            p, f = engine_pnl(t, S, TIER0_TAKER, TIER0_MAKER); nets.append(p); fees_t.append(f)
        o = stats(trades, nets, notional_each=[S] * len(trades)); o["fees"] = sum(fees_t); TIER0[S] = o

    # ---- STEP 7: capital
    conc = R["concurrency"]; CAP = {}
    for S in SIZES:
        peak_notl = conc["max_simul"] * S; peak_margin = peak_notl / LEV
        dd = PURE[S]["max_dd"]; mtm = R["mtm"]["mtm_dd_ref"] * S / REF_NOTIONAL if R["mtm"] else None
        full_margin = MAX_OPEN * S / LEV
        full_stop = MAX_OPEN * S * (SL_PCT["WY-PULLBACK"] / 100 + 2 * SPREAD_BPS / 1e4 + 2 * TAKER_BPS / 1e4)
        CAP[S] = dict(max_simul=conc["max_simul"], peak_notional=peak_notl, peak_margin=peak_margin,
                      max_dd=dd, mtm_dd_proxy=mtm, peak_stop_risk=conc["peak_stop_risk_frac"] * S,
                      min_math=peak_margin + max(dd, mtm or 0),
                      practical_hist=peak_margin / HEADROOM_MARGIN_UTIL + max(dd, mtm or 0),
                      full_margin=full_margin, full_stop_loss=full_stop,
                      full_min=full_margin + full_stop,
                      full_practical=full_margin / HEADROOM_MARGIN_UTIL + full_stop)

    # ---- STEP 9: real rounding
    REAL = {}; EXEC_MKT = {}
    for S in SIZES:
        nets = []; sub = []; skipped = defaultdict(int); act_tot = 0.0
        for i, t in enumerate(trades):
            q, actual, ok = real_fill(t, S)
            key = (S, t["market"])
            e = EXEC_MKT.setdefault(key, dict(n=0, ok=0, req=0.0, act=0.0, errs=[]))
            e["n"] += 1; e["req"] += S
            if not ok:
                skipped[t["market"]] += 1; e["errs"].append(-100.0); continue
            e["ok"] += 1; e["act"] += actual; e["errs"].append((actual / S - 1) * 100)
            fees = actual * TAKER_BPS / 1e4 + actual * (MAKER_BPS if t["reason"] == "TARGET" else TAKER_BPS) / 1e4
            nets.append(actual * pnl_pct(t) / 100 - fees); sub.append(t); act_tot += actual
        o = stats(sub, nets, notional_each=[1] * len(nets)) if nets else dict(trades=0, net=0, wins=0)
        o["net_bps_on_filled"] = (sum(nets) / act_tot * 1e4) if act_tot else 0
        o["net_bps_on_requested_108"] = sum(nets) / (S * len(trades)) * 1e4
        o["skipped"] = dict(skipped); o["filled_notional_ratio"] = act_tot / (S * len(trades))
        o["pure_net"] = PURE[S]["net"]
        REAL[S] = o

    # ---- STEP 11: sizing models across balances
    L_PB = SL_PCT["WY-PULLBACK"] / 100 + 2 * SPREAD_BPS / 1e4 + 2 * TAKER_BPS / 1e4     # 2.07% worst per-trade stop loss
    bps_hist = PURE[REF_NOTIONAL]["net_bps"]
    trades_per_day = len(trades) / ((R["sample"]["last_close"] - R["sample"]["first_open"]) / 864e5)
    def model_rows(E):
        rows = {}
        rows["REF book intensity ($2k clip / $25k)"] = E * REF_NOTIONAL / 25000.0
        rows["A fixed $200 notional"] = 200.0
        rows["B fixed $20 margin @10x"] = 20.0 * LEV
        rows["C 5% equity margin/trade"] = 0.05 * E * LEV
        rows["D equity/max_open, 50% reserve"] = E * (1 - 0.5) * LEV / MAX_OPEN
        for lvl, r in (("LOW", 0.005), ("MEDIUM", 0.010), ("HIGH", 0.0125)):
            rows[f"PROPOSED {lvl} ({r*100:g}% equity at risk/trade)"] = min(r * E / L_PB, HEADROOM_MARGIN_UTIL * E * LEV / MAX_OPEN)
        out = {}
        for k, S in rows.items():
            fm = MAX_OPEN * S / LEV
            ex = {m: real_exec_ok(m, S, trades) for m in YMETA}
            out[k] = dict(S=S, margin=S / LEV, full_margin=fm, full_margin_pct=fm / E * 100,
                          feasible=fm <= E, worst8=MAX_OPEN * S * L_PB, worst8_pct=MAX_OPEN * S * L_PB / E * 100,
                          per_trade_risk_pct=S * L_PB / E * 100, exp_net_per_trade=S * bps_hist / 1e4,
                          exp_net_per_day=S * bps_hist / 1e4 * trades_per_day,
                          markets_executable=sum(ex.values()), btc_ok=ex["BTCUSDT-PERP"])
        return out
    def real_exec_ok(m, S, trades):
        ts = [t for t in trades if t["market"] == m]
        if not ts: return real_fill(dict(market=m, side="long", entry=1.0, exit=1.0), S)[2]
        return all(real_fill(t, S)[2] for t in ts)
    MODELS = {E: model_rows(E) for E in BALANCES}

    # ---- STEP 14: integrity
    ids_after = [identity(t) for t in trades]
    integ = dict(journal_sha256_before=h_before, journal_sha256_after=sha(JOURNAL), set_hash=H0, set_hash_after=set_hash(trades),
                 trades=len(trades), per_size_identity_equal=True, only_arithmetic_changed=True,
                 journal_untouched=h_before == sha(JOURNAL), ids_equal=ids_after == ids0)
    assert integ["journal_untouched"] and integ["ids_equal"] and H0 == integ["set_hash_after"]

    R.update(pure=PURE, sleeve={f"{k[0]}|{k[1]}": v for k, v in SLEEVE.items()},
             sens={f"{k[0]}|{k[1]}": v for k, v in SENS.items()}, tier0=TIER0, cap=CAP, real=REAL,
             exec_mkt={f"{k[0]}|{k[1]}": dict(n=v["n"], ok=v["ok"], req_avg=v["req"] / v["n"],
                       act_avg=(v["act"] / v["ok"]) if v["ok"] else 0,
                       err_avg=st.mean(v["errs"]), err_worst=min(v["errs"])) for k, v in EXEC_MKT.items()},
             models={str(E): v for E, v in MODELS.items()}, integrity=integ,
             params=dict(L_PB=L_PB, bps_hist=bps_hist, trades_per_day=trades_per_day, lev=LEV, max_open=MAX_OPEN,
                         headroom_margin_util=HEADROOM_MARGIN_UTIL, taker=TAKER_BPS, maker=MAKER_BPS, spread=SPREAD_BPS))

    # ---- CSV
    cols = ["section", "notional", "sleeve", "cost_mult", "market", "margin_10x", "trades", "wins", "losses", "win_rate",
            "gross", "fee_in", "fee_out", "fees", "slip", "total_cost", "funding_proxy_not_in_net", "net", "net_return_on_peak_margin_pct",
            "avg", "median", "avg_win", "avg_loss", "wl_ratio", "pf", "max_dd", "best", "worst", "net_bps",
            "target", "stop", "timeout", "peak_margin", "min_math_capital", "practical_capital_hist", "full_occupancy_practical",
            "exec_ok", "exec_n", "act_notional_avg", "rounding_err_avg_pct", "rounding_err_worst_pct"]
    def row(**kw): out_rows.append({c: kw.get(c, "") for c in cols})
    def r6(x): return round(x, 6) if isinstance(x, float) and math.isfinite(x) else x
    for S in SIZES:
        o = PURE[S]; c = CAP[S]
        row(section="PURE", notional=S, sleeve="ALL", cost_mult=1.0, margin_10x=S / LEV, trades=o["trades"], wins=o["wins"],
            losses=o["losses"], win_rate=r6(o["win_rate"]), gross=r6(o["gross"]), fee_in=r6(o["fee_in"]), fee_out=r6(o["fee_out"]),
            fees=r6(o["fees"]), slip=r6(o["slip"]), total_cost=r6(o["total_cost"]), funding_proxy_not_in_net=r6(o["funding_proxy"]),
            net=r6(o["net"]), net_return_on_peak_margin_pct=r6(o["net"] / c["peak_margin"] * 100), avg=r6(o["avg"]), median=r6(o["median"]),
            avg_win=r6(o["avg_win"]), avg_loss=r6(o["avg_loss"]), wl_ratio=r6(o["wl_ratio"]), pf=r6(o["pf"]), max_dd=r6(o["max_dd"]),
            best=r6(o["best"]), worst=r6(o["worst"]), net_bps=r6(o["net_bps"]), target=o["target"], stop=o["stop"], timeout=o["timeout"],
            peak_margin=c["peak_margin"], min_math_capital=r6(c["min_math"]), practical_capital_hist=r6(c["practical_hist"]),
            full_occupancy_practical=r6(c["full_practical"]))
        for sname in SL_PCT:
            o = SLEEVE[(S, sname)]
            row(section="SLEEVE", notional=S, sleeve=sname, cost_mult=1.0, margin_10x=S / LEV, trades=o["trades"], wins=o["wins"],
                losses=o["losses"], win_rate=r6(o["win_rate"]), gross=r6(o["gross"]), fees=r6(o["fees"]), slip=r6(o["slip"]),
                total_cost=r6(o["total_cost"]), net=r6(o["net"]), avg=r6(o["avg"]), avg_win=r6(o["avg_win"]), avg_loss=r6(o["avg_loss"]),
                wl_ratio=r6(o["wl_ratio"]), pf=r6(o["pf"]), max_dd=r6(o["max_dd"]), net_bps=r6(o["net_bps"]),
                target=o["target"], stop=o["stop"], timeout=o["timeout"])
    for (S, cm), o in SENS.items():
        row(section="COST_SENS", notional=S, sleeve="ALL", cost_mult=cm, trades=o["trades"], wins=o["wins"], losses=o["losses"],
            win_rate=r6(o["win_rate"]), gross=r6(o["gross"]), fees=r6(o["fees"]), slip=r6(o["slip"]), total_cost=r6(o["total_cost"]),
            net=r6(o["net"]), avg_win=r6(o["avg_win"]), avg_loss=r6(o["avg_loss"]), pf=r6(o["pf"]), net_bps=r6(o["net_bps"]))
    for S in SIZES:
        o = TIER0[S]
        row(section="PUBLIC_TIER0_FEES", notional=S, sleeve="ALL", cost_mult="tier0 1/4bps", trades=o["trades"], wins=o["wins"],
            losses=o["losses"], win_rate=r6(o["win_rate"]), fees=r6(o["fees"]), net=r6(o["net"]), avg=r6(o["avg"]), avg_win=r6(o["avg_win"]),
            avg_loss=r6(o["avg_loss"]), pf=r6(o["pf"]), max_dd=r6(o["max_dd"]), net_bps=r6(o["net_bps"]))
    for S in SIZES:
        o = REAL[S]
        row(section="REAL_ROUNDING", notional=S, sleeve="ALL", cost_mult=1.0, trades=o["trades"], wins=o.get("wins", 0),
            win_rate=r6(o.get("win_rate", 0.0)), net=r6(o["net"]), net_bps=r6(o["net_bps_on_requested_108"]),
            exec_n=len(trades), exec_ok=o["trades"], act_notional_avg=r6(o["filled_notional_ratio"] * S))
        for m in YMETA:
            v = R["exec_mkt"][f"{S}|{m}"]
            row(section="EXEC_MARKET", notional=S, market=m, exec_n=v["n"], exec_ok=v["ok"], act_notional_avg=r6(v["act_avg"]),
                rounding_err_avg_pct=r6(v["err_avg"]), rounding_err_worst_pct=r6(v["err_worst"]))
    with open(os.path.join(HERE, "YELLOW_SIZING_RESULTS.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(out_rows)

    json.dump(R, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=str)

    manifest = dict(
        generated_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        mode="OFFLINE, read-only inputs; writes only this directory",
        inputs={p: dict(sha256=sha(p), bytes=os.path.getsize(p)) for p in (JOURNAL, JOURNAL_PRE, SNAPSHOT, UNIT, ENGINE, EXEC)},
        proxy_inputs={f"{BYBIT}/{k}_{m.replace('-PERP','')}.json": "informational only (funding / MTM proxy), not in accepted net"
                      for m in YMETA for k in ("fund", "k5")},
        instrument_metadata=dict(source="yellow_exec.py selftest stdout, 2026-09-12T06:47:19Z, session 95645aab (public /perpetual/exchangeInfo)",
                                 min_notional_cross_check="2026-09-11T15:04Z exchangeInfo listing, session 62b6d0fc (identical min_notional)",
                                 min_qty="NOT captured - assumed equal to step", contract_multiplier="1 (linear USDT; qty=0.025 BTC for $2000 @ ~77.3k confirms base-unit qty)",
                                 markets=YMETA),
        cost_model=dict(taker_bps=TAKER_BPS, maker_bps=MAKER_BPS, spread_bps_per_side=SPREAD_BPS,
                        entry="taker on notional", exit="maker if TARGET else taker", funding="not modelled in accepted book",
                        source="why-yellow.service Environment= lines; yellow_paper.py open_position/close_position"),
        reference=R["reference"], integrity=integ)
    json.dump(manifest, open(os.path.join(HERE, "source_manifest.json"), "w"), indent=1, default=str)
    print("OK", json.dumps(dict(reproduced=reproduced, trades=len(trades), net=j_net, set_hash=H0)))

if __name__ == "__main__":
    main()
