"""EXACT Yellow calibration harness (2026-09-11, session 62b6d0fc, call 248) with ONE addition: every resolved trade is
recorded (gross return + timing) so any cost model can be applied afterwards. Control flow, indicators, warm-up (250 bars),
entry at NEXT bar open, exit checks starting the bar AFTER the entry bar, stop-before-target within a bar, timeout at bar close,
one position per symbol per sleeve, no cross-symbol cap -- all verbatim from ORIGINAL_call248_verbatim.py.

Original cost handling was:  pnl = (-sl/100 - cost) | (tp/100 - cost) | ((c-entry)/entry - cost)   with cost a flat round-trip constant.
Here: gross = pnl + cost (same three cases with cost=0); net = gross - cost_fn(trade).  With cost_fn == const this is identical.
"""
import json

def ema(vals, n):
    k = 2/(n+1); out = []; e = vals[0]
    for v in vals: e = v*k + e*(1-k); out.append(e)
    return out

def rsi(closes, n=14):
    out = [50.0]*len(closes); g = 0.0; l = 0.0
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]; gain = max(d, 0); loss = max(-d, 0)
        if i <= n: g += gain/n; l += loss/n
        else: g = (g*(n-1)+gain)/n; l = (l*(n-1)+loss)/n
        out[i] = 100.0 if l == 0 else 100-100/(1+g/l)
    return out

def atr(bars, n=14):
    out = [0.0]*len(bars); a = bars[0][2]-bars[0][3]
    for i in range(1, len(bars)):
        t, o, h, lo, c = bars[i]; pc = bars[i-1][4]
        a = (a*(n-1)+max(h-lo, abs(h-pc), abs(lo-pc)))/n; out[i] = a
    return out

def load_rows(rows):
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]

def prep(bars):
    closes = [x[4] for x in bars]
    return dict(bars=bars, e20=ema(closes, 20), e50=ema(closes, 50), rsi=rsi(closes), atr=atr(bars))

# accepted sleeve parameters (frozen 2026-09-11; unchanged in yellow_paper.py CFG)
PB = dict(strat="PB", tp=0.8, sl=2.0, timeout=144, depth=2.0)
RV = dict(strat="RV", tp=0.3, sl=1.6, timeout=144, rlo=30, rhi=70, tg=True)
SLEEVES = {"WY-PULLBACK": PB, "WY-REVERT": RV}

def signal(d, i, strat, depth=0.5, rlo=28, rhi=72, tg=True):
    """verbatim signal block"""
    sig = None; c = d["bars"][i][4]; up = d["e20"][i] > d["e50"][i]
    if strat == "PB":
        if up and c < d["e20"][i]-depth*d["atr"][i]: sig = "L"
        elif (not up) and c > d["e20"][i]+depth*d["atr"][i]: sig = "S"
    else:
        if d["rsi"][i] < rlo and (up or not tg): sig = "L"
        elif d["rsi"][i] > rhi and ((not up) or not tg): sig = "S"
    return sig

def resolve(pos, bar, i, tp, sl, timeout):
    """verbatim exit block, cost=0. returns (gross, reason, exit_price) or None"""
    side, entry, ei = pos; t, o, h, lo, c = bar
    if side == "L":
        hit_s = lo <= entry*(1-sl/100); hit_t = h >= entry*(1+tp/100)
        if hit_s: return (-sl/100, "STOP", entry*(1-sl/100))
        if hit_t: return (tp/100, "TARGET", entry*(1+tp/100))
        if i-ei >= timeout: return ((c-entry)/entry, "TIMEOUT", c)
    else:
        hit_s = h >= entry*(1+sl/100); hit_t = lo <= entry*(1-tp/100)
        if hit_s: return (-sl/100, "STOP", entry*(1+sl/100))
        if hit_t: return (tp/100, "TARGET", entry*(1-tp/100))
        if i-ei >= timeout: return ((entry-c)/entry, "TIMEOUT", c)
    return None

def simulate(sym, d, strat, tp, sl, timeout, depth=0.5, rlo=28, rhi=72, tg=True, start_i=250):
    """one symbol, one sleeve -- verbatim control flow of run() in the original."""
    b = d["bars"]; i = start_i; pos = None; out = []
    while i < len(b)-1:
        if pos:
            r = resolve(pos, b[i], i, tp, sl, timeout)
            if r is not None:
                g, reason, xpx = r; side, entry, ei = pos
                out.append(dict(sym=sym, strat=strat, side=side, ei=ei, xi=i, t_entry=b[ei][0], t_exit=b[i][0]+300000,
                                entry=entry, exit=xpx, reason=reason, gross=g))
                pos = None
            i += 1; continue
        sig = signal(d, i, strat, depth, rlo, rhi, tg)
        if sig: pos = (sig, b[i+1][1], i+1); i += 1
        i += 1
    return out

def run_sleeve(data, name):
    p = SLEEVES[name]; kw = {k: v for k, v in p.items() if k != "strat"}
    trades = []
    for sym, d in data.items():
        for t in simulate(sym, d, p["strat"], **kw): t["sleeve"] = name; trades.append(t)
    return trades

def score_flat(trades, cost, days):
    n = len(trades); w = sum(1 for t in trades if t["gross"]-cost > 0); net = sum(t["gross"]-cost for t in trades)*100
    return n, w/max(n, 1)*100, net, n/days
