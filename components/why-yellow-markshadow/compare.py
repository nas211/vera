#!/usr/bin/env python3
"""Compare two signal books (native vs shadow) on exact (sleeve, market, side, 5m entry bar)
and compute performance (trades, W/L, WR, net, PF, max DD) for the shadow and for the exact
intersection. Inputs are journal.jsonl files (production journal is read-only here).
Entry bar key: OPEN records carry bar_ms when written by the shadow engine; otherwise the
bar is floor(open ts / 5m) minus one bar (the signal fires on the first tick/kline after the
bar it was computed from has closed) — the same rule is applied to BOTH books so the key is
symmetric. Reports the exact figures the owner asked for; nothing is tuned."""
import argparse, json, os, sys
BAR_MS = 300_000

def load(path, t0=None, t1=None):
    opens, closes = [], []
    for line in open(path):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: continue
        if t0 and r["ts"] < t0: continue
        if t1 and r["ts"] >= t1: continue
        (opens if r.get("event") == "OPEN" else closes if r.get("event") == "CLOSE" else []).append(r)
    return opens, closes

def key(r):
    bar = (r["ts"] // BAR_MS) * BAR_MS - BAR_MS      # bar whose close produced the signal (symmetric rule)
    return (r["strategy"], r["market"], r["side"], bar)

def perf(closes):
    if not closes: return dict(trades=0, wins=0, losses=0, wr=None, net=0.0, pf=None, max_dd=0.0, fees=0.0)
    cl = sorted(closes, key=lambda r: r["ts"])
    wins = [r["pnl_usd"] for r in cl if r["pnl_usd"] > 0]; losses = [r["pnl_usd"] for r in cl if r["pnl_usd"] <= 0]
    gw, gl = sum(wins), -sum(losses)
    eq = peak = dd = 0.0
    for r in cl:
        eq += r["pnl_usd"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    return dict(trades=len(cl), wins=len(wins), losses=len(losses), wr=round(len(wins) / len(cl) * 100, 1),
                net=round(sum(r["pnl_usd"] for r in cl), 2), pf=(round(gw / gl, 2) if gl > 0 else None),
                max_dd=round(dd, 2), fees=round(sum(r.get("fees_usd", 0) for r in cl), 2),
                by_reason={k: sum(1 for r in cl if r["reason"] == k) for k in ("TARGET", "STOP", "TIMEOUT")})

def breakdown(nk, sk, fn):
    groups = sorted(set(fn(k) for k in nk | sk))
    rows = []
    for g in groups:
        n = {k for k in nk if fn(k) == g}; s = {k for k in sk if fn(k) == g}
        rows.append(dict(group=g, native=len(n), shadow=len(s), exact=len(n & s),
                         native_only=len(n - s), shadow_only=len(s - n),
                         match_rate=round(len(n & s) / len(n) * 100, 1) if n else None))
    return rows

def compare(native_path, shadow_path, t0=None, t1=None):
    no, nc = load(native_path, t0, t1); so, sc = load(shadow_path, t0, t1)
    nk = {key(r): r for r in no}; sk = {key(r): r for r in so}
    NK, SK = set(nk), set(sk)
    inter = NK & SK
    # link shadow CLOSE to its OPEN key (one open per market at a time -> pair by market + order)
    def pair(opens, closes):
        by_m = {}
        for r in sorted(opens, key=lambda r: r["ts"]): by_m.setdefault(r["market"], []).append(r)
        out = []
        for c in sorted(closes, key=lambda r: r["ts"]):
            cands = [o for o in by_m.get(c["market"], []) if o["ts"] <= c["ts"]]
            if cands:
                o = cands[-1]; by_m[c["market"]].remove(o); out.append((key(o), c))
        return out
    shadow_pairs = pair(so, sc); native_pairs = pair(no, nc)
    res = dict(window=[t0, t1], native_signals=len(NK), shadow_signals=len(SK), exact_matches=len(inter),
               exact_match_rate=round(len(inter) / len(NK) * 100, 1) if NK else None,
               native_only=len(NK - SK), shadow_only=len(SK - NK),
               by_sleeve=breakdown(NK, SK, lambda k: k[0]), by_market=breakdown(NK, SK, lambda k: k[1]),
               by_side=breakdown(NK, SK, lambda k: k[2]),
               native_perf=perf(nc), shadow_perf=perf(sc),
               intersection_shadow_perf=perf([c for k, c in shadow_pairs if k in inter]),
               intersection_native_perf=perf([c for k, c in native_pairs if k in inter]),
               native_only_keys=sorted(str(k) for k in NK - SK)[:50], shadow_only_keys=sorted(str(k) for k in SK - NK)[:50])
    return res

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", default="/opt/why-yellow/state/journal.jsonl")
    ap.add_argument("--shadow", required=True)
    ap.add_argument("--from", dest="t0", type=int, default=None); ap.add_argument("--to", dest="t1", type=int, default=None)
    a = ap.parse_args()
    print(json.dumps(compare(a.native, a.shadow, a.t0, a.t1), indent=1))
