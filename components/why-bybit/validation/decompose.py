import json, sys, math, random, statistics, time, collections
sys.path.insert(0, "/opt/why-bybit/validation"); import harness_exact as H
SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HYPEUSDT"]
V = "/opt/why-bybit/validation/"
SP = json.load(open(V + "spread_measured.json"))
FUND = {s: {int(k): v for k, v in json.load(open(V + f"data_bybit/fund_{s}.json")).items()} for s in SYMS}
FINT = {s: (4 if s == "TAOUSDT" else 8) * 3600000 for s in SYMS}
fmt = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t / 1000))

# ---------- 0. baseline gate: instrumented harness must equal the accepted numbers exactly ----------
CAL = {s: H.prep(H.load_rows(json.load(open(V + f"data_yellow_calib/{s}.json")))) for s in SYMS}
print("=== 0. BASELINE GATE (instrumented harness vs accepted 2026-09-11 numbers, original calib files) ===")
ACC = {"WY-PULLBACK": (479, 68.3, 10.7, 44.2), "WY-REVERT": (133, 91.0, 6.0, 15.3)}
CALTR = {}
ok = True
for name in ACC:
    tr = H.run_sleeve(CAL, name); CALTR[name] = tr
    n10 = H.score_flat(tr, 0.0010, 31); n3 = H.score_flat(tr, 0.0003, 31)
    got = (n10[0], round(n10[1], 1), round(n10[2], 1), round(n3[2], 1)); good = got == ACC[name]; ok &= good
    print(f"{name:12} accepted (trades, win%, net10%, net3%) = {ACC[name]} | reproduced = {got} | {'MATCH' if good else 'MISMATCH'}")
assert ok, "baseline not reproduced - STOP"

# ---------- cost model: per-trade cost in FRACTION of notional, split into components ----------
def funding(t):
    s = t["sym"]; iv = FINT[s]; b = (t["t_entry"] // iv + 1) * iv; c = 0.0; fh = FUND[s]; ks = None
    while b <= t["t_exit"]:
        r = fh.get(b)
        if r is None:
            ks = ks or sorted(fh); prev = [k for k in ks if k <= b]; r = fh[prev[-1]] if prev else 0.0001
        c += r * (1 if t["side"] == "L" else -1); b += iv
    return c
def components(t, mode):
    """returns dict(fees, funding, spread, slip) fractions. mode: gross|y10|y3|yvip5|bfees_engine|bfees|bfund|breal|bstress"""
    tgt = t["reason"] == "TARGET"; s = t["sym"]; e = SP[s]; z = dict(fees=0.0, funding=0.0, spread=0.0, slip=0.0)
    if mode == "gross": return z
    if mode == "y10": z["fees"] = 0.0010; return z
    if mode == "y3": z["fees"] = 0.0003; return z
    if mode == "yvip5": z["fees"] = (1.5 + (0 if tgt else 1.5)) / 1e4; z["spread"] = 4.0 / 1e4; return z   # deployed Yellow paper economics
    if mode == "bfees_engine": z["fees"] = (5.5 + (2.0 if tgt else 5.5)) / 1e4; return z                  # engine's maker-on-target assumption
    z["fees"] = 11.0 / 1e4                                                                                  # executable: entry taker + exit taker (adapter exits at market)
    if mode == "bfees": return z
    z["funding"] = funding(t) * (2 if mode == "bstress" else 1)
    if mode == "bfund": return z
    if mode == "breal":
        z["spread"] = 2 * e["half_spread_bps"] / 1e4; z["slip"] = (2 * e["impact50_bps"] + (0.5 if t["reason"] == "STOP" else 0)) / 1e4
    if mode == "bstress":
        z["spread"] = 3 * 2 * e["half_spread_bps"] / 1e4; z["slip"] = (2 * 2 * e["impact2k_bps"] + (3.0 if t["reason"] != "TARGET" else 0)) / 1e4
    return z
MODES = ["gross", "y10", "y3", "yvip5", "bfees_engine", "bfees", "bfund", "breal", "bstress"]
LAB = {"gross": "A0 GROSS (no costs)", "y10": "A  Yellow accepted 10bps flat", "y3": "A' Yellow accepted 3bps flat", "yvip5": "Yellow deployed (VIP5 1.5/0 + 2bps/side)",
       "bfees_engine": "C  Bybit fees only, maker-on-target", "bfees": "C' Bybit fees only, all-taker (executable)", "bfund": "D  + funding", "breal": "E  + measured spread + slippage (REALISTIC)", "bstress": "F  STRESS"}

def stats(tr, mode, days, clip=50.0):
    if not tr: return None
    cs = [components(t, mode) for t in tr]; net = [t["gross"] - sum(c.values()) for t, c in zip(tr, cs)]
    n = len(tr); m = statistics.mean(net) * 1e4
    pos = sum(x for x in net if x > 0); neg = -sum(x for x in net if x < 0)
    order = sorted(range(n), key=lambda i: tr[i]["t_exit"]); cum = pk = dd = 0.0
    for i in order: cum += net[i]; pk = max(pk, cum); dd = max(dd, pk - cum)
    byd = collections.defaultdict(lambda: [0.0, 0])
    for t, x in zip(tr, net): d = t["t_exit"] // 86400000; byd[d][0] += x; byd[d][1] += 1
    random.seed(7); D = list(byd.values()); bs = []
    for _ in range(2000):
        s = [random.choice(D) for _ in D]; bs.append(sum(a for a, _ in s) / sum(b for _, b in s) * 1e4)
    bs.sort()
    return dict(n=n, per_day=n / days, win=100 * sum(1 for x in net if x > 0) / n, gross=statistics.mean(t["gross"] for t in tr) * 1e4, net=m,
                fees=statistics.mean(c["fees"] for c in cs) * 1e4, fund=statistics.mean(c["funding"] for c in cs) * 1e4,
                spread=statistics.mean(c["spread"] for c in cs) * 1e4, slip=statistics.mean(c["slip"] for c in cs) * 1e4,
                pf=(pos / neg if neg else float("inf")), dd_pct=dd * 100, dd_usd=dd * clip, ci=(bs[50], bs[1949]), perday_bps=m * n / days,
                hold_h=statistics.mean((t["t_exit"] - t["t_entry"]) for t in tr) / 3.6e6)

def ALL(trs): return [t for name in ("WY-PULLBACK", "WY-REVERT") for t in trs[name]]
# ---------- windows on fresh Bybit data ----------
FR = {s: json.load(open(V + f"data_bybit/k5_{s}.json")) for s in SYMS}; ts0 = [int(r[0]) for r in FR["BTCUSDT"]]
ix = {t: i for i, t in enumerate(ts0)}
cal_start, cal_end = int(json.load(open(V + "data_yellow_calib/BTCUSDT.json"))[0][0]), int(json.load(open(V + "data_yellow_calib/BTCUSDT.json"))[-1][0])
def window(a, b, warm=250):   # signals eligible from bar a (250 preceding bars = warm-up), through bar b
    return {s: H.prep(H.load_rows(FR[s][a - warm:b + 1])) for s in SYMS}, (b - a + 1) / 288
wins = collections.OrderedDict()
a0, b0 = ix[cal_start], ix[cal_end]
wins["CAL-fresh (same window, fresh Bybit data, orig series)"] = ({s: H.prep(H.load_rows(FR[s][a0:b0 + 1])) for s in SYMS}, 31.0)
for k in range(1, 5): wins[f"OOS-{k} ({fmt(ts0[a0-9000*k])}..{fmt(ts0[a0-9000*(k-1)-1])})"] = window(a0 - 9000 * k, a0 - 9000 * (k - 1) - 1)
wins[f"FWD ({fmt(ts0[b0+1])}..{fmt(ts0[-1])}, Yellow live week)"] = window(b0 + 1, len(ts0) - 1)
RES = {}; TRS = {}
def run_window(name, data, days):
    TRS[name] = {n: H.run_sleeve(data, n) for n in ACC}
run_window("CAL-orig (accepted series)", CAL, 31.0); DAYS = {"CAL-orig (accepted series)": 31.0}
for k, (d, days) in wins.items(): run_window(k, d, days); DAYS[k] = days
pool = {n: sum((TRS[k][n] for k in TRS if k.startswith("OOS") or k.startswith("FWD")), []) for n in ACC}; TRS["POOLED OUT-OF-WINDOW (OOS-1..4 + FWD)"] = pool
DAYS["POOLED OUT-OF-WINDOW (OOS-1..4 + FWD)"] = sum(DAYS[k] for k in DAYS if k.startswith("OOS") or k.startswith("FWD"))
json.dump({k: {n: v for n, v in x.items()} for k, x in TRS.items()}, open(V + "trades_by_window.json", "w"))

print("\n=== 1. FROZEN Yellow parameters, EXACT harness, per window: per-trade expectancy in bps of notional (sleeves as accepted: independent per-symbol books) ===")
print(f"{'window':70} {'days':>5} {'trades':>6} {'tr/d':>5} {'win%':>5} | {'GROSS':>6} | {'Y10':>6} {'Y3':>6} {'YVIP5':>6} | {'BYfee':>6} {'+fund':>6} {'REAL':>6} {'STRESS':>7}")
for k in TRS:
    tr = ALL(TRS[k]); S = {m: stats(tr, m, DAYS[k]) for m in MODES}
    print(f"{k[:70]:70} {DAYS[k]:5.1f} {S['gross']['n']:6d} {S['gross']['per_day']:5.1f} {S['breal']['win']:5.1f} | {S['gross']['net']:+6.2f} | {S['y10']['net']:+6.2f} {S['y3']['net']:+6.2f} {S['yvip5']['net']:+6.2f} | {S['bfees']['net']:+6.2f} {S['bfund']['net']:+6.2f} {S['breal']['net']:+6.2f} {S['bstress']['net']:+7.2f}")
print("\n--- per sleeve, gross / Yellow10 / Bybit REALISTIC (bps per trade), by window ---")
for k in TRS:
    row = []
    for n in ACC:
        tr = TRS[k][n]
        if tr: S = {m: stats(tr, m, DAYS[k]) for m in ("gross", "y10", "breal")}; row.append(f"{n} n={S['gross']['n']:3d} win={S['gross']['win']:4.1f}% gross {S['gross']['net']:+6.1f} y10 {S['y10']['net']:+6.1f} REAL {S['breal']['net']:+6.1f}")
    print(f"{k[:52]:52} | " + " | ".join(row))
json.dump({"DAYS": DAYS}, open(V + "days.json", "w"))
