#!/usr/bin/env python3
"""Replay the UNCHANGED engine (bybit_paper.on_bar_close / on_mark / open / close) over Bybit history.
No strategy code is re-implemented: this only feeds candles/marks and captures journal records in memory.
Conservative bar model: entry at next bar's open (mark); inside a bar the ADVERSE extreme is tested first (stop before target)."""
import json, os, sys, time, urllib.request, collections, statistics, math
os.environ["WB_MODE"] = "paper"
sys.path.insert(0, "/opt/why-bybit")
import bybit_paper as bp

DAYS = int(os.environ.get("REPLAY_DAYS", 60)); DATA = "/opt/why-bybit/data"; os.makedirs(DATA, exist_ok=True)
def get(u): return json.load(urllib.request.urlopen(u, timeout=20))["result"]

def klines(sym):
    f = f"{DATA}/k5_{sym}_{DAYS}d.json"
    if os.path.exists(f): return json.load(open(f))
    end = int(time.time() * 1000); start = end - DAYS * 86400 * 1000; rows = {}
    while end > start:
        l = get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=5&end={end}&limit=1000")["list"]
        if not l: break
        for r in l: rows[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])]
        end = min(int(r[0]) for r in l) - 1; time.sleep(0.12)
    out = sorted(rows.values()); out = out[:-1]          # drop forming bar
    json.dump(out, open(f, "w")); return out

def fund_hist(sym):
    f = f"{DATA}/fund_{sym}_{DAYS}d.json"
    if os.path.exists(f): return json.load(open(f))
    end = int(time.time() * 1000); start = end - (DAYS + 2) * 86400 * 1000; rows = {}
    while end > start:
        l = get(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&endTime={end}&limit=200")["list"]
        if not l: break
        for r in l: rows[int(r["fundingRateTimestamp"])] = float(r["fundingRate"])
        end = min(int(r["fundingRateTimestamp"]) for r in l) - 1; time.sleep(0.12)
    json.dump(rows, open(f, "w")); return rows

print(f"loading {DAYS}d of Bybit 5m klines + funding history for {len(bp.MARKETS)} symbols ...", flush=True)
K = {m: klines(bp.BYBIT[m]) for m in bp.MARKETS}
FH = {m: {int(k): v for k, v in fund_hist(bp.BYBIT[m]).items()} for m in bp.MARKETS}
print({m: len(K[m]) for m in bp.MARKETS}, flush=True)

class FakeTime:
    t = 0.0
    def time(self): return self.t
    def strftime(self, *a): return time.strftime(*a)
    def sleep(self, *a): pass
CLK = FakeTime(); bp.time = CLK
bp.log = lambda *a, **k: None; bp.atomic_write = lambda *a, **k: None; bp.evals_append = lambda *a, **k: None
REC = []; bp.journal_append = lambda r: REC.append(dict(r))
bp.load_fees(); bp.load_fund_intervals()
def fr_at(m, ts):
    h = FH[m]
    if ts in h: return h[ts]
    prev = [k for k in h if k <= ts]; return h[max(prev)] if prev else 0.0001
bp.funding_rate_at = fr_at

def run(max_open):
    global REC
    REC = []; bp.POS.clear(); bp.CANDLES = {m: [] for m in bp.MARKETS}
    bp.CFG["max_open"] = max_open
    bp.CNT.update(evals=0, bars_evaluated=0, signals=0, skipped_open=0, skipped_capacity=0)
    for sname in bp.STRATS: bp.CNT["by_strat"][sname].update(evals=0, signals=0)
    idx = {m: {b[0]: i for i, b in enumerate(K[m])} for m in bp.MARKETS}
    times = sorted({b[0] for m in bp.MARKETS for b in K[m]})
    global PEAK; PEAK = 0
    for T in times:
        live_m = []
        for m in bp.MARKETS:                                   # phase 1: every market's bar-close evaluation at the boundary
            j = idx[m].get(T)
            if j is None or j < 300: continue
            bp.CANDLES[m] = K[m][j - 300:j]
            bp.MARK[m] = (K[m][j][1], T / 1000)
            CLK.t = T / 1000
            bp.on_bar_close(m); live_m.append((m, K[m][j]))
            PEAK = max(PEAK, len(bp.POS))
        CLK.t = (T + 299000) / 1000
        for m, bar in live_m:                                  # phase 2: intrabar ticks (adverse extreme first)
            pos = bp.POS.get(m)
            if pos:
                seq = (bar[3], bar[2]) if pos["side"] == "long" else (bar[2], bar[3])
                for px in seq:
                    if m in bp.POS: bp.on_mark(m, px)
    closes = [r for r in REC if r["event"] == "CLOSE"]
    print(f"   [max concurrent positions observed with max_open={max_open}: {PEAK}]")
    return closes, (times[-1] - times[300]) / 86400000.0

def bps(r, k): return r[k] / r["notional"] * 1e4
def summarize(closes, days, label):
    print(f"\n=== {label} | {days:.1f} days | closed trades {len(closes)} ({len(closes)/days:.1f}/day) ===")
    out = {}
    for name in ["ALL"] + bp.STRATS:
        c = [r for r in closes if name == "ALL" or r["strategy"] == name]
        if not c: print(f"{name:12} no trades"); continue
        n = len(c); w = sum(1 for r in c if r["pnl_usd"] > 0)
        g = sum(bps(r, "gross_usd") for r in c) / n; fe = sum(bps(r, "fees_usd") for r in c) / n
        fu = sum(bps(r, "funding_usd") for r in c) / n; ne = sum(bps(r, "pnl_usd") for r in c) / n
        ref = sum(bps(r, "pnl_usd_at_yellow_fees") for r in c) / n
        ex = collections.Counter(r["reason"] for r in c)
        out[name] = dict(n=n, per_day=n / days, win=100 * w / n, gross_bps=g, fee_bps=fe, fund_bps=fu, net_bps=ne, ref_bps=ref)
        print(f"{name:12} trades {n:4d} ({n/days:4.1f}/d) win {100*w/n:5.1f}% | per trade bps: gross {g:+6.2f}  fees {-fe:6.2f}  funding {-fu:+6.2f}  NET {ne:+6.2f} | @Yellow fees NET {ref:+6.2f} | exits {dict(ex)}")
    return out

res = {}
for mo, label in ((8, "PAPER SCALE: max_open 8 (as deployed)"), (3, "LIVE SCALE: max_open 3")):
    closes, days = run(mo); res[mo] = (closes, days); summarize(closes, days, label)
json.dump({str(k): v[0] for k, v in res.items()}, open(f"{DATA}/replay_trades_{DAYS}d.json", "w"))

# ---- $ view at candidate live clips, with Bybit lot constraints applied per trade (post-hoc, live-scale trade set)
inst = None
try: inst = bp.urllib.request.urlopen("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000", timeout=15); inst = {x["symbol"]: x for x in json.load(inst)["result"]["list"]}
except Exception as e: print("inst err", e)
def sized(r, clip, cap):
    sym = r["venue_symbol"]; x = inst[sym]; step = float(x["lotSizeFilter"]["qtyStep"]); mn = float(x["lotSizeFilter"]["minOrderQty"]); mnn = float(x["lotSizeFilter"].get("minNotionalValue", 5))
    px = r["entry"]; q = math.floor(clip / px / step + 1e-9) * step; lot = max(mn, math.ceil(mnn / px / step) * step)
    sl = 1.6 if r["strategy"] == "WY-REVERT" else 2.0
    if q < lot:
        planned = lot * px * (sl / 100 + (11 + 4) / 1e4)
        if planned > cap: return None
        q = lot
    return q * px
closes, days = res[3]
print("\n=== $ VIEW, live scale (max_open 3), Bybit lot constraints applied per trade ===")
print(f"{'clip':>5} {'cap':>5} | {'traded':>6} {'refused':>7} | {'avg notional':>12} | {'NET $/trade':>11} {'NET $/day':>9} | {'@Yellow fees $/day':>18} | {'worst day $':>11} {'max DD $':>9} {'max losing streak':>17} {'worst trade $':>13}")
for clip, cap in ((40, 2.0), (50, 2.0), (60, 2.0), (85, 2.5)):
    rows = []; ref_rows = []; refused = 0
    for r in closes:
        n = sized(r, clip, cap)
        if n is None: refused += 1; continue
        rows.append((r["ts"], r["pnl_usd"] / r["notional"] * n, n)); ref_rows.append(r["pnl_usd_at_yellow_fees"] / r["notional"] * n)
    if not rows: continue
    net = sum(x[1] for x in rows); daily = collections.defaultdict(float); cum = peak = dd = 0.0; streak = mx = 0
    for ts, p, n in rows:
        daily[int(ts // 86400000)] += p; cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
        streak = streak + 1 if p < 0 else 0; mx = max(mx, streak)
    print(f"{clip:5d} {cap:5.1f} | {len(rows):6d} {refused:7d} | {statistics.mean(x[2] for x in rows):12.1f} | {net/len(rows):+11.4f} {net/days:+9.3f} | {sum(ref_rows)/days:+18.3f} | {min(daily.values()):11.2f} {dd:9.2f} {mx:17d} {min(x[1] for x in rows):13.2f}")
