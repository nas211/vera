#!/usr/bin/env python3
"""Offline replay of the UNCHANGED production strategy over recorded data.

  --candles mark    bars come from local mark-candles rebuilt from recorded ticks
  --candles native  bars come from recorded native Yellow klines (state/native_klines),
                    closed at the moment the next native bar first appeared

Exits (TP/SL) always come from the recorded mark ticks, as in production. The replay clock
is the recorded tick timestamp, so journal ts / hold_min are replay-time, not wall-time.
Seed = the seed bars the live service stored at start (state/seed/<m>.jsonl, first entry
whose window precedes --from), i.e. the same Bybit 5m seed production uses.
Output: <out>/journal.jsonl + <out>/signals.json + <out>/summary.json. Never touches
production or the live shadow state.
"""
import argparse, glob, heapq, json, os, sys, time, datetime as dt
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
from wy_logic import Engine, MarkCandleBuilder, BAR_MS
STATE = os.path.join(BASE, "state")

def ts_arg(s):
    if s is None: return None
    if s.isdigit(): return int(s)
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)

def load_ticks(market, t0, t1, ticks_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(ticks_dir, market, "*.jsonl"))):
        for line in open(f):
            try: r = json.loads(line)
            except Exception: continue
            ts = r[0]
            if (t0 is None or ts >= t0) and (t1 is None or ts < t1):
                out.append((ts, r[1], float(r[2])))
    out.sort(key=lambda x: x[0])
    return out

def load_native(market, t0, t1, nk_dir):
    rows = []
    for r in (json.loads(l) for l in open(os.path.join(nk_dir, market + ".jsonl")) if l.strip()) if os.path.exists(os.path.join(nk_dir, market + ".jsonl")) else []:
        b = r["bar"]
        if (t0 is None or b[0] >= t0) and (t1 is None or b[0] < t1):
            rows.append((r["closed_recv_ms"], b, r.get("frozen")))
    rows.sort(key=lambda x: x[0])
    return rows

def load_seed(market, before_ms, seed_dir):
    best = None
    p = os.path.join(seed_dir, market + ".jsonl")
    if not os.path.exists(p): return []
    for line in open(p):
        r = json.loads(line)
        if before_ms is None or r["ts"] <= before_ms + 60_000:
            best = r
    if best is None:                      # first seed ever recorded; trim to bars before window
        best = json.loads(open(p).readline())
    bars = [b for b in best["bars"] if before_ms is None or b[0] < before_ms - BAR_MS]
    return bars

def run(candles, t0, t1, out, ticks_dir, nk_dir, seed_dir, quiet=False):
    os.makedirs(out, exist_ok=True)
    jp = os.path.join(out, "journal.jsonl")
    if os.path.exists(jp): os.remove(jp)
    clock = {"now": (t0 or 0) / 1000.0}
    eng = Engine(out, clock=lambda: clock["now"], logger=(lambda *a: None) if quiet else print, tag=f"replay-{candles}")
    events = []                                  # heap of (ts, seq, kind, market, payload)
    seq = 0
    nticks = {}
    for m in eng.MARKETS:
        seed = load_seed(m, t0, seed_dir)
        eng.seed(m, seed)
        tk = load_ticks(m, t0, t1, ticks_dir); nticks[m] = len(tk)
        for ts, recv, p in tk:
            events.append((ts, seq, "tick", m, p)); seq += 1
        if candles == "native":
            for closed_ms, bar, frozen in load_native(m, t0, t1, nk_dir):
                events.append((closed_ms, seq, "nbar", m, bar)); seq += 1
    heapq.heapify(events)
    builder = MarkCandleBuilder(lambda m, bar, meta: eng.bar_close(m, bar)) if candles == "mark" else None
    mark_bars = {m: [] for m in eng.MARKETS}
    if builder:
        builder.on_close = lambda m, bar, meta: (mark_bars[m].append(dict(bar=bar, **meta)), eng.bar_close(m, bar))
    n = 0
    while events:
        ts, _, kind, m, payload = heapq.heappop(events)
        clock["now"] = ts / 1000.0
        if kind == "tick":
            if builder: builder.tick(m, ts, payload)
            eng.mark(m, payload, ts / 1000.0)
        else:
            eng.bar_close(m, payload)
        n += 1
    summary = dict(candles=candles, window=[t0, t1], events=n, ticks=nticks,
                   signals=len(eng.signals), closes=len(eng.closes), stats=eng.STATS,
                   open_at_end=list(eng.POS.keys()), prod_sha256=eng.sha, prod_env=eng.env,
                   mark_bars={m: len(v) for m, v in mark_bars.items()},
                   mark_bars_partial={m: sum(1 for r in v if r["partial"]) for m, v in mark_bars.items()})
    json.dump(eng.signals, open(os.path.join(out, "signals.json"), "w"), indent=1)
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    if builder:
        json.dump(mark_bars, open(os.path.join(out, "mark_candles.json"), "w"))
    return eng, summary

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", choices=["mark", "native"], default="mark")
    ap.add_argument("--from", dest="t0", default=None); ap.add_argument("--to", dest="t1", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ticks", default=os.path.join(STATE, "ticks"))
    ap.add_argument("--native", default=os.path.join(STATE, "native_klines"))
    ap.add_argument("--seed", default=os.path.join(STATE, "seed"))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    eng, s = run(a.candles, ts_arg(a.t0), ts_arg(a.t1), a.out, a.ticks, a.native, a.seed, a.quiet)
    print(json.dumps(s, indent=1))
