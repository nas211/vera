"""Causal price features, adapted from the existing lab numerical kernel.
No model calls, credentials, network or writes to the source recorder.
"""
import math
import time
from pathlib import Path
import json
import numpy as np
BAR = 300_000
LOOKBACKS = (6, 12, 24, 48, 72, 96)
REGIMES = ("long", "sideways", "short")
def clock_ms(): return int(time.time()*1000)
def rolling_mean(x, period):
    out = np.full(len(x), np.nan)
    if len(x) >= period:
        c = np.concatenate(([0.0], np.cumsum(x)))
        out[period-1:] = (c[period:] - c[:-period]) / period
    return out

def load_bars(source, limit=4000, now=None):
    """Only completed, covered bars; retain gaps so validation can reject them."""
    data = {}
    cutoff = (clock_ms() if now is None else now) // BAR * BAR
    for path in sorted((Path(source) / "mark_candles").glob("*.jsonl")):
        rows = {}
        with path.open() as f:
            for line in f:
                try:
                    r = json.loads(line); b = r["bar"]
                    if int(b[0]) + BAR > cutoff or len(b) < 5: continue
                    a = np.array(b[:5], dtype=float)
                    good = not r.get("partial", True) and r.get("gap_max_s", 999) <= 15
                    if not np.isfinite(a).all() or min(a[1:]) <= 0: continue
                    if a[2] < max(a[1], a[4]) or a[3] > min(a[1], a[4]): continue
                    rows[int(b[0])] = (*a, float(good))
                except (ValueError, TypeError, KeyError): continue
        if len(rows) >= 200:
            data[path.stem] = np.array([rows[t] for t in sorted(rows)[-limit:]], dtype=float)
    return data

def features(bars):
    c = bars[:, 4]; n = len(c)
    ret = np.zeros(n); ret[1:] = np.log(c[1:] / c[:-1]) * 1e4
    vol = np.sqrt(np.maximum(rolling_mean(ret * ret, 24), 1.0))
    z = {}
    for look in LOOKBACKS:
        past = np.full(n, np.nan); past[look:] = c[:-look]
        mean = rolling_mean(c, look)
        std = np.sqrt(np.maximum(rolling_mean(c*c, look) - mean*mean, 1e-12))
        momentum = np.log(c / past) * 1e4 / (vol * math.sqrt(look))
        high = np.full(n, np.nan); low = np.full(n, np.nan)
        for i in range(look, n):
            high[i] = np.max(bars[i-look:i, 2]); low[i] = np.min(bars[i-look:i, 3])
        breakout = np.where(c > high, (c / high - 1)*1e4/vol,
                            np.where(c < low, (c/low-1)*1e4/vol, 0.0))
        reject = (2*c - bars[:, 2] - bars[:, 3]) / np.maximum(bars[:, 2]-bars[:, 3], c*1e-6)
        reject *= np.abs((c-mean)/std)
        lag = np.full(n, np.nan); lag[look:] = momentum[:-look]
        z[("momentum", look)] = momentum
        z[("breakout", look)] = breakout
        z[("mean_reversion", look)] = -(c-mean)/std
        z[("range_rejection", look)] = reject
        z[("acceleration", look)] = momentum-lag
    trend = np.zeros(n); trend[48:] = np.log(c[48:]/c[:-48])*1e4 / np.maximum(vol[48:]*math.sqrt(48), 1)
    regime = np.where(trend > .6, 0, np.where(trend < -.6, 2, 1))
    good = bars[:, 5].astype(bool)
    gaps = np.concatenate(([True], np.diff(bars[:,0]) != BAR)) | ~good
    # Any hole in the longest feature window invalidates the observation.
    bad = np.convolve(gaps.astype(int), np.ones(193,dtype=int), mode="full")[:n] > 0
    usable = good & ~bad
    return dict(signals=z, regime=regime, vol=vol, usable=usable)

def summarize(pnls):
    x = np.array(pnls, dtype=float); n = len(x)
    if not n: return dict(trades=0, pnl=0.0, win_rate=0.0, pf=0.0, expectancy=0.0, t=0.0, max_drawdown=0.0)
    wins=float(x[x>0].sum()); losses=float(-x[x<=0].sum())
    eq=np.concatenate(([0.0],np.cumsum(x)))
    t=float(x.mean() / (x.std(ddof=1) / math.sqrt(n))) if n>1 and x.std(ddof=1)>1e-9 else 0.0
    return dict(trades=n,pnl=round(float(x.sum()),6),win_rate=round(float((x>0).mean()),4),
                pf=round(min(wins/losses if losses else (99.0 if wins else 0.0),99),4),
                expectancy=round(float(x.mean()),6),t=round(t,4),max_drawdown=round(float((np.maximum.accumulate(eq)-eq).max()),6))

