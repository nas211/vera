#!/usr/bin/env python3
"""WY candidate generation — an EXACT, side-effect-free copy of the live Bybit
engine's signal logic (/opt/why-bybit/bybit_paper.py: ema_last / rsi_last /
atr_last / the first-match chain in on_bar_close).

PURE FUNCTIONS. No I/O, no globals, no imports beyond the stdlib. This module
knows nothing about orders, positions or credentials. tests/test_parity.py
proves it returns the same decision as the live on_bar_close on real bars.

Chain (first match wins, same as live):
    RSI(14) < rv_rsi_lo  and up      -> WY-REVERT   long
    RSI(14) > rv_rsi_hi  and not up  -> WY-REVERT   short
    up  and c < EMA20 - pb_depth*ATR -> WY-PULLBACK long
    not up and c > EMA20 + pb_depth*ATR -> WY-PULLBACK short
where up = EMA20 > EMA50 on the last 250 closes, RSI over the last 60 closes,
ATR over the last 60 bars. Warmup: >= 250 closed bars.
"""

WARMUP_BARS = 250


def ema_last(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
    return e


def rsi_last(closes, n=14):
    g = l = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain, loss = max(d, 0), max(-d, 0)
        if i <= n:
            g += gain / n
            l += loss / n
        else:
            g = (g * (n - 1) + gain) / n
            l = (l * (n - 1) + loss) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def atr_last(bars, n=14):
    a = bars[0][2] - bars[0][3]
    for i in range(1, len(bars)):
        pc = bars[i - 1][4]
        tr = max(bars[i][2] - bars[i][3], abs(bars[i][2] - pc), abs(bars[i][3] - pc))
        a = (a * (n - 1) + tr) / n
    return a


def wy_eval(bars, rv_rsi_lo=30.0, rv_rsi_hi=70.0, pb_depth=2.0):
    """bars: closed bars [start_ms, o, h, l, c], oldest first.

    Returns None while warming up, else a dict with the indicator values and
    `fired` = (side, strategy) or None, `det` = the live journal detail dict.
    """
    if len(bars) < WARMUP_BARS:
        return None
    closes = [b[4] for b in bars[-250:]]
    c = closes[-1]
    e20 = ema_last(closes, 20)
    e50 = ema_last(closes, 50)
    r = rsi_last(closes[-60:])
    a = atr_last(bars[-60:])
    up = e20 > e50
    depth = (e20 - c) / a if up else (c - e20) / a
    fired = None
    det = None
    if r < rv_rsi_lo and up:
        fired = ("long", "WY-REVERT")
        det = dict(rsi=round(r, 1), trend="up")
    elif r > rv_rsi_hi and not up:
        fired = ("short", "WY-REVERT")
        det = dict(rsi=round(r, 1), trend="down")
    elif up and c < e20 - pb_depth * a:
        fired = ("long", "WY-PULLBACK")
        det = dict(depth_atr=round((e20 - c) / a, 2), trend="up")
    elif (not up) and c > e20 + pb_depth * a:
        fired = ("short", "WY-PULLBACK")
        det = dict(depth_atr=round((c - e20) / a, 2), trend="down")
    return dict(close=c, ema20=e20, ema50=e50, rsi=r, atr=a, up=up, depth_atr=depth,
                fired=fired, det=det, bar_ms=bars[-1][0])
