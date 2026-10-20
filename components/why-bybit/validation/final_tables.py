import sys, os, json, time, math, collections, statistics
os.environ["WB_MODE"] = "paper"; sys.path.insert(0, "/opt/why-bybit/validation"); sys.path.insert(0, "/opt/why-bybit")
exec(open("/opt/why-bybit/validation/decompose.py").read().split("# ---------- 0. baseline gate")[0])   # imports, SP, FUND, helpers only
import harness_exact as H
# rebuild trades + helpers from the saved decomposition module state
src = open("/opt/why-bybit/validation/decompose.py").read()
pre = src.split('print("\\n=== 1. FROZEN')[0]
g = {}; import io, contextlib
with contextlib.redirect_stdout(io.StringIO()): exec(pre, g)
stats, ALL, TRS, DAYS, MODES, components, FR, ix, ts0, SYMS = (g[k] for k in ("stats", "ALL", "TRS", "DAYS", "MODES", "components", "FR", "ix", "ts0", "SYMS"))
fmt = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t / 1000))

def show(tag, tr, days, clip=50.0):
    S = {m: stats(tr, m, days, clip) for m in MODES}; r = S["breal"]; y10 = S["y10"]; yv = S["yvip5"]
    print(f"\n### {tag} | {days:.1f} days | trades {r['n']} ({r['per_day']:.1f}/day) | win {r['win']:.1f}% | avg hold {r['hold_h']:.1f}h")
    print(f"  per trade (bps of notional): GROSS {r['gross']:+.2f} | fees -{r['fees']:.2f} | spread -{r['spread']:.2f} | slippage -{r['slip']:.2f} | funding {-r['fund']:+.3f}  =>  NET BYBIT REALISTIC {r['net']:+.2f}   (95% CI, day-bootstrap: {r['ci'][0]:+.2f} .. {r['ci'][1]:+.2f})")
    print(f"  NET/day (bps-of-trade-notional x trades/day): {r['perday_bps']:+.1f} | at $50/trade: ${r['perday_bps']*50/1e4:+.4f}/day | PF {r['pf']:.2f} | max DD {r['dd_pct']:.1f}% of trade-notional-units = ${r['dd_usd']:.2f} at $50/trade")
    print(f"  same trades: Yellow accepted 10bps {y10['net']:+.2f} | Yellow accepted 3bps {S['y3']['net']:+.2f} | Yellow deployed VIP5 {yv['net']:+.2f} | Bybit fees-only(taker) {S['bfees']['net']:+.2f} | Bybit fees maker-on-target {S['bfees_engine']['net']:+.2f} | +funding {S['bfund']['net']:+.2f} | REALISTIC {r['net']:+.2f} | STRESS {S['bstress']['net']:+.2f}")
    return S
cal = "CAL-orig (accepted series)"; pooled = "POOLED OUT-OF-WINDOW (OOS-1..4 + FWD)"
fwd = [k for k in TRS if k.startswith("FWD")][0]
allwin = {n: TRS[cal][n] + TRS[pooled][n] for n in TRS[cal]}; alldays = DAYS[cal] + DAYS[pooled]
S1 = show("IN-SAMPLE calibration window (params were selected here)", ALL(TRS[cal]), DAYS[cal])
S2 = show("OUT-OF-WINDOW pooled: 4 prior 31-day blocks + Yellow live week", ALL(TRS[pooled]), DAYS[pooled])
S3 = show("ALL 180 DAYS pooled (calibration + out-of-window)", ALL(allwin), alldays)
print("\n--- per sleeve, ALL 180 days / out-of-window only (bps per trade; REALISTIC Bybit net) ---")
for n in ("WY-REVERT", "WY-PULLBACK"):
    for lab, tr, d in (("all-180d", allwin[n], alldays), ("out-of-window", TRS[pooled][n], DAYS[pooled])):
        s = stats(tr, "breal", d); y = stats(tr, "y10", d)
        print(f"  {n:12} {lab:14} n={s['n']:4d} ({s['per_day']:.1f}/d) win {s['win']:.1f}% gross {s['gross']:+.2f} | Yellow-10bps {y['net']:+.2f} | Bybit REALISTIC {s['net']:+.2f}  CI[{s['ci'][0]:+.1f},{s['ci'][1]:+.1f}] PF {s['pf']:.2f}")

print("\n=== fee headroom (bps per round trip; break-even = gross edge) ===")
for tag, S in (("calibration (in-sample)", S1), ("out-of-window pooled", S2), ("all 180d", S3)):
    r = S["breal"]; cost = r["fees"] + r["spread"] + r["slip"] + r["fund"]; gross = r["gross"]
    be_taker = (gross - r["spread"] - r["slip"] - r["fund"]) / 2
    print(f"  {tag:26} gross {gross:+6.2f} | actual Bybit cost {cost:5.2f} | headroom {gross-cost:+6.2f} bps | break-even taker fee (entry+exit both taker) = {be_taker:5.2f} bps vs actual 5.50")

print("\n=== METHODOLOGY LADDER on the calibration window, identical data: accepted harness -> engine (bybit_paper.on_bar_close/on_mark) ===")
import bybit_paper as bp
class FT:
    t = 0.0
    def time(self): return self.t
    def strftime(self, *a): return time.strftime(*a)
    def sleep(self, *a): pass
CLK = FT(); bp.time = CLK; bp.log = lambda *a, **k: None; bp.atomic_write = lambda *a, **k: None; bp.evals_append = lambda *a, **k: None
REC = []; bp.journal_append = lambda r: REC.append(dict(r)); bp.load_fees(); bp.load_fund_intervals(); bp.funding_rate_at = lambda m, ts: 0.0001
def engine(K, cap, entry_bar_checks=True):
    global REC; REC = []; bp.POS.clear(); bp.CFG["max_open"] = cap
    idx = {m: {b[0]: i for i, b in enumerate(K[m])} for m in bp.MARKETS}; times = sorted({b[0] for m in bp.MARKETS for b in K[m]})
    for T in times:
        lm = []
        for m in bp.MARKETS:
            j = idx[m].get(T)
            if j is None or j < 300: continue
            bp.CANDLES[m] = K[m][j - 300:j]; bp.MARK[m] = (K[m][j][1], T / 1000); CLK.t = T / 1000; bp.on_bar_close(m); lm.append((m, K[m][j]))
        CLK.t = (T + 299000) / 1000
        for m, bar in lm:
            pos = bp.POS.get(m)
            if pos:
                for px in ((bar[3], bar[2]) if pos["side"] == "long" else (bar[2], bar[3])):
                    if m in bp.POS: bp.on_mark(m, px)
    cl = [r for r in REC if r["event"] == "CLOSE"]
    return cl
Kcal = {m: H.load_rows(json.load(open(f"/opt/why-bybit/validation/data_yellow_calib/{bp.BYBIT[m]}.json"))) for m in bp.MARKETS}
hb = S1["gross"]
print(f"  accepted harness (independent books, uncapped)          : {hb['n']:4d} trades  win {hb['win']:.1f}%  gross {hb['gross']:+.2f} bps/trade (before the engine's own 2bps/side modeled spread)")
for cap in (10, 8, 3):
    cl = engine(Kcal, cap); n = len(cl)
    gx = statistics.mean(r["gross_usd"] / r["notional"] * 1e4 for r in cl) + 4.0      # add back the engine's modeled 2bps/side spread to compare on the harness's gross basis
    win = 100 * sum(1 for r in cl if r["gross_usd"] > 0) / n
    print(f"  engine, shared slot per market, cap {cap:2d}                    : {n:4d} trades  gross-basis-comparable {gx:+.2f} bps/trade | engine-native gross (after 4bps modeled spread) {gx-4:+.2f}")
