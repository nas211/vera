#!/usr/bin/env python3
"""Offline self-test (no network, no production writes):
 T1 candle builder: OHLC = first/max/min/last of real ticks, boundaries, partial + gap flags
 T2 plumbing equivalence: tick-driven (builder+heap) book == bar-driven book on the same data
 T3 production-logic fidelity: engine CFG/sha equal production file + unit env; signals fire only
    at bar boundaries; TP/SL/timeout geometry matches CFG; max_open respected
 T4 sanity: replaying real Bybit 5m history through the engine produces both sleeves."""
import json, os, sys, tempfile, heapq
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
from wy_logic import Engine, MarkCandleBuilder, BAR_MS, prod_sha, prod_env
CAL = "/opt/why-yellow/calib"
ok = True
def check(name, cond, info=""):
    global ok; ok &= bool(cond); print(("PASS" if cond else "FAIL"), name, info)

# T1
got = []
b = MarkCandleBuilder(lambda m, bar, meta: got.append((m, bar, meta)))
t = 1_790_000_000_000 // BAR_MS * BAR_MS
for i, p in enumerate([10, 12, 9, 11]): b.tick("X", t + 1000 * i, p)
b.tick("X", t + BAR_MS + 500, 20)                    # closes bar 1
for i in range(299): b.tick("X", t + BAR_MS + 500 + i * 1000, 20 + (i % 7))
b.tick("X", t + 3 * BAR_MS + 10, 30)                 # closes bar 2, bar 3 skipped entirely (gap)
b.flush()
check("T1 OHLC first/max/min/last", got[0][1] == [t, 10, 12, 9, 11], str(got[0][1]))
check("T1 sparse bar flagged partial", got[0][2]["partial"] and got[0][2]["n"] == 4)
check("T1 dense bar not partial", not got[1][2]["partial"] and got[1][2]["n"] == 300, json.dumps(got[1][2]))
check("T1 no synthetic bar for empty slot", [g[1][0] for g in got] == [t, t + BAR_MS, t + 3 * BAR_MS])

# T2 + T3 + T4
mk = ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP", "TAOUSDT-PERP", "LINKUSDT-PERP"]
bars = {m: [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in json.load(open(f"{CAL}/{m.replace('-PERP','')}.json"))] for m in mk}
N = 3000
def make(tag, clock):
    d = tempfile.mkdtemp(prefix=f"wys-{tag}-")
    e = Engine(d, clock=lambda: clock["now"], tag=tag)
    for m in mk: e.seed(m, bars[m][:399])
    return e
clockA = {"now": 0.0}; A = make("tick", clockA)
clockB = {"now": 0.0}; B = make("bar", clockB)
# tick-driven: 4 ticks per bar (o,h,l,c) through builder + global time heap across markets
ev = []; seq = 0
for m in mk:
    for bar in bars[m][399:399 + N]:
        for j, p in enumerate((bar[1], bar[2], bar[3], bar[4])):
            ev.append((bar[0] + 1000 + j * 90_000, seq, m, p)); seq += 1
heapq.heapify(ev)
bld = MarkCandleBuilder(lambda m, bar, meta: A.bar_close(m, bar))
rebuilt = {m: [] for m in mk}; bld.on_close = lambda m, bar, meta: (rebuilt[m].append(bar), A.bar_close(m, bar))
while ev:
    ts, _, m, p = heapq.heappop(ev); clockA["now"] = ts / 1000.0
    bld.tick(m, ts, p); A.mark(m, p, ts / 1000.0)
# bar-driven: same marks in the same global order, bar closes at the first tick of the next bar
ev = []; seq = 0
for m in mk:
    for i, bar in enumerate(bars[m][399:399 + N]):
        for j, p in enumerate((bar[1], bar[2], bar[3], bar[4])):
            ev.append((bar[0] + 1000 + j * 90_000, seq, m, p, i)); seq += 1
heapq.heapify(ev); lastbar = {m: None for m in mk}
while ev:
    ts, _, m, p, i = heapq.heappop(ev); clockB["now"] = ts / 1000.0
    if lastbar[m] is not None and i > lastbar[m]: B.bar_close(m, bars[m][399 + lastbar[m]])
    lastbar[m] = i
    B.mark(m, p, ts / 1000.0)
check("T2 rebuilt candles identical to source bars", all(rebuilt[m] == bars[m][399:399 + N - 1] for m in mk))
sa = [(s["strategy"], s["market"], s["side"], s["bar_ms"], round(s["entry"], 8)) for s in A.signals]
sb = [(s["strategy"], s["market"], s["side"], s["bar_ms"], round(s["entry"], 8)) for s in B.signals]
check("T2 tick-driven == bar-driven signals", sa == sb, f"{len(sa)} vs {len(sb)}")
check("T2 tick-driven == bar-driven closes", [(c["market"], c["reason"], c["pnl_usd"]) for c in A.closes] == [(c["market"], c["reason"], c["pnl_usd"]) for c in B.closes], f"{len(A.closes)} closes")
# T3
check("T3 engine sha == production file", A.sha == prod_sha(), A.sha[:16])
check("T3 fee env == production unit", A.CFG["taker_bps"] == float(prod_env()["WY_TAKER_BPS"]) and A.CFG["maker_bps"] == float(prod_env()["WY_MAKER_BPS"]) and A.CFG["spread_bps"] == float(prod_env()["WY_SPREAD_BPS"]))
check("T3 signals only at bar boundaries", all((s["ts"] - 1000) % BAR_MS == 0 for s in A.signals))
geo = True
for s in A.signals:
    tp, sl = (A.CFG["rv_tp"], A.CFG["rv_sl"]) if s["strategy"] == "WY-REVERT" else (A.CFG["pb_tp"], A.CFG["pb_sl"])
    e = s["entry"]; sg = 1 if s["side"] == "long" else -1
    geo &= abs(s["target"] - e * (1 + sg * tp / 100)) < 1e-9 * e and abs(s["stop"] - e * (1 - sg * sl / 100)) < 1e-9 * e
check("T3 TP/SL geometry == CFG", geo)
check("T3 timeout hold <= 144 bars", all(c["hold_min"] <= 144 * 5 + 5 for c in A.closes if c["reason"] == "TIMEOUT"))
# max_open: replay OPEN/CLOSE ordering
cur = 0; mx = 0
for r in sorted([("O", s["ts"]) for s in A.signals] + [("C", c["ts"]) for c in A.closes], key=lambda x: (x[1], x[0] == "O")):
    cur += 1 if r[0] == "O" else -1; mx = max(mx, cur)
check("T3 max_open respected", mx <= A.CFG["max_open"], f"peak {mx}")
# T4
st = A.STATS
check("T4 both sleeves fire on real history", set(st["by_strat"]) == {"WY-PULLBACK", "WY-REVERT"}, json.dumps(st))
print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
