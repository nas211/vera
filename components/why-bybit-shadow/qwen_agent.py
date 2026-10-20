#!/usr/bin/env python3
"""Qwen directional agent for the SHADOW book — adapted from the existing
direction agent (/opt/alpha/v2-trade/direction_agent.py), not a new design.

REUSED unchanged in spirit and code:
  * transport      agent_transport.py, vendored byte-for-byte (OpenAI-compatible
                   /chat/completions, strict json_schema, failure classification,
                   token governor). Same vendor (Groq), same model FAMILY (Qwen).
  * evidence       the same four reads direction.py builds — BREADTH, TREND
                   across lookbacks (medians), FOLLOW-THROUGH (autocorrelation),
                   MEASURED EDGE (naive long vs naive short, real exits, real fees)
  * prompt         the same principles: reason over evidence not noise, neutral is
                   a valid answer, signals agreeing for the wrong reason, thin
                   evidence, confidence>0.7 is rare, never invent data
  * output         bias/confidence/reasoning/key_risk (+ a regime label)
  * safety         fails CLOSED: any error is a recorded non-answer, never a
                   default direction; out-of-scale numbers are untrusted

ADAPTED (the existing agent answers "which way should the whole BOOK lean" from
a Binance $10/$5 book; the shadow question is per-market and per-decision):
  * evidence is computed from BYBIT completed 5m bars of the 10-market WY
    universe, using ONLY bars whose start <= the decision bar (no lookahead)
  * the question is per market: LONG / SHORT / NO_TRADE over the WY bracket
  * the agent is BLIND to the WY proposal (direction and sleeve name are not in
    the prompt) so agreement is an independent read, not an echo
  * the existing agent's MIN_CONFIDENCE->neutral clamp is NOT applied: the frozen
    policy has no confidence threshold, the raw answer is what gets recorded

This module has no order, position or credential code. Its only network path is
agent_transport.chat_json -> the configured Qwen endpoint.
"""
import json
import statistics
import time

import agent_transport as AT
from wy_signals import ema_last, rsi_last, atr_last

REGIMES = ["trend_up", "trend_down", "range_chop", "volatile_breakout", "unclear"]
DIRECTIONS = ["long", "short", "no_trade"]

SYSTEM = """You are the direction reader for a crypto perpetual-futures book on Bybit (10 liquid USDT perps, 5-minute bars).

Your ONLY job: for ONE market at ONE decision time, decide whether the next ~12 hours favour LONG, SHORT, or NO_TRADE. You never place orders. You are shown evidence only; nobody else's opinion is provided.

The trade being judged is a fixed bracket, identical for either direction: enter at the current mark; exit at +TP% (take profit) or -SL% (stop), or at market after 12 hours (144 five-minute bars), whichever comes first. Costs are real: about 15 bps round trip (taker 5.5 bps, maker 2.0 bps, 2 bps slippage each side). The bracket for this decision is in the evidence.

How to think about the evidence:

BREADTH tells you participation. 90% of the universe below EMA20 is a different market from 55%, even at the same index level. Broad agreement is what makes a lean worth taking.

TREND across four lookbacks (1h, 2h, 4h, 8h) tells you whether direction is real or an artifact of one horizon. Disagreement between lookbacks is a reason for LOW confidence, not for splitting the difference.

FOLLOW-THROUGH is the trap. A market can move all day and pay nothing: if every move is faded within a few bars, brackets get stopped both ways and fees do the rest. Negative autocorrelation means NO_TRADE almost regardless of what the trend says.

MEASURED EDGE is what a naive long and a naive short actually paid over the last 12 hours under THIS bracket and THESE costs, on the universe and on this market. It is what happened, not a view, but interrogate it: unresolved trades dominate a short window, and a gap driven by one or two coins will not repeat.

THIS MARKET versus the universe: is it leading, lagging or stretched? A stretched move is where the evidence looks strongest right before it snaps. Indicators (RSI, distance from EMA20 in ATR units, EMA20 vs EMA50) describe state; they are not instructions.

Judgement you are expected to apply: signals agreeing for the WRONG reason (all lookbacks derived from one big move); measured edge contradicting breadth and trend (usually a regime change); thin evidence dressed up as a strong signal.

Be genuinely willing to answer NO_TRADE. A market that pays neither side under this bracket and these costs is the correct outcome, not a failure to have an opinion. Confidence above 0.7 should be rare and should require independent reads to agree.

regime: classify the current state of THIS market as one of trend_up, trend_down, range_chop, volatile_breakout, unclear.

Reason from the numbers you are given. Never invent data you were not shown."""

SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": DIRECTIONS,
                      "description": "long, short, or no_trade for this market over the next ~12h."},
        "confidence": {"type": "number",
                       "description": "0.0-1.0. Above 0.7 requires independent reads to agree. "
                                      "Use low values freely."},
        "regime": {"type": "string", "enum": REGIMES,
                   "description": "Regime of THIS market right now."},
        "reasoning": {"type": "string",
                      "description": "2-3 sentences: what the evidence says and what would change your mind."},
        "key_risk": {"type": "string",
                     "description": "The single thing most likely to make this read wrong."},
    },
    "required": ["direction", "confidence", "regime", "reasoning", "key_risk"],
}


# ---------------------------------------------------------------- evidence
def _ret(closes, L):
    return closes[-1] / closes[-1 - L] - 1 if len(closes) > L else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _autocorr(closes, bars=288):
    c = closes[-bars:]
    if len(c) < 50:
        return None
    r = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    a, b = r[:-1], r[1:]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


def naive_bracket(bars, side, tp_pct, sl_pct, cost, window=144, stride=6, timeout=144):
    """Naive entries every `stride` bars over the trailing `window` bars, resolved
    ONLY on bars that already exist (entry i uses bars i+1.. n-1). STOP wins a
    tie inside a bar. Returns (net_bps list, unresolved count)."""
    n = len(bars)
    res, unresolved = [], 0
    for i in range(max(1, n - window), n - 1, stride):
        entry = bars[i][4]
        if side == "long":
            tp, sl = entry * (1 + tp_pct / 100), entry * (1 - sl_pct / 100)
        else:
            tp, sl = entry * (1 - tp_pct / 100), entry * (1 + sl_pct / 100)
        exit_px, kind = None, None
        for j in range(i + 1, min(i + 1 + timeout, n)):
            hi, lo = bars[j][2], bars[j][3]
            if side == "long":
                if lo <= sl:
                    exit_px, kind = sl, "STOP"; break
                if hi >= tp:
                    exit_px, kind = tp, "TARGET"; break
            else:
                if hi >= sl:
                    exit_px, kind = sl, "STOP"; break
                if lo <= tp:
                    exit_px, kind = tp, "TARGET"; break
        if exit_px is None:
            exit_px, kind = bars[-1][4], "OPEN"
            unresolved += 1
        g = ((exit_px - entry) / entry if side == "long" else (entry - exit_px) / entry) * 1e4
        c = cost["taker"] + (cost["maker"] if kind == "TARGET" else cost["taker"]) + 2 * cost["spread"]
        res.append(g - c)
    return res, unresolved


def _edge_line(label, bars_list, tp, sl, cost):
    out = {}
    for side in ("long", "short"):
        allr, allu = [], 0
        for bars in bars_list:
            r, u = naive_bracket(bars, side, tp, sl, cost)
            allr += r; allu += u
        out[side] = (len(allr), allu,
                     (sum(allr) / len(allr)) if allr else 0.0,
                     (100 * sum(1 for x in allr if x > 0) / len(allr)) if allr else 0.0)
    return (f"  {label}: naive LONG {out['long'][2]:+.1f} bps/trade ({out['long'][3]:.0f}% win, "
            f"{out['long'][0]} entries, {out['long'][1]} unresolved) | naive SHORT "
            f"{out['short'][2]:+.1f} bps/trade ({out['short'][3]:.0f}% win, {out['short'][0]} entries, "
            f"{out['short'][1]} unresolved)")


def build_evidence(market, bar_ms, price, universe_bars, tp_pct, sl_pct, timeout_bars, cost):
    """The evidence brief. `universe_bars` = {market: closed bars}, ALREADY
    filtered to start <= bar_ms by the caller; re-filtered here as a guard.

    Returns (text, meta). meta lists the last bar start used per market so the
    record proves nothing newer than the decision bar was consumed."""
    U = {m: [b for b in bs if b[0] <= bar_ms] for m, bs in universe_bars.items()}
    U = {m: bs for m, bs in U.items() if len(bs) >= 250}
    last_bar = {m: bs[-1][0] for m, bs in U.items()}
    feats = {}
    for m, bs in U.items():
        cl = [b[4] for b in bs[-250:]]
        e20, e50 = ema_last(cl, 20), ema_last(cl, 50)
        feats[m] = dict(c=cl[-1], e20=e20, e50=e50, cl=[b[4] for b in bs[-300:]],
                        R={L: _ret([b[4] for b in bs], L) for L in (12, 24, 48, 96, 288)})
    n = len(feats)
    above20 = sum(f["c"] > f["e20"] for f in feats.values()) / max(1, n)
    above50 = sum(f["c"] > f["e50"] for f in feats.values()) / max(1, n)
    up24 = sum((f["R"][24] or 0) > 0 for f in feats.values()) / max(1, n)
    medR = {L: _med([f["R"][L] for f in feats.values()]) for L in (12, 24, 48, 96)}
    agree = sum(1 if (v or 0) > 0 else (-1 if (v or 0) < 0 else 0) for v in medR.values()) / 4
    ac = _med([_autocorr(f["cl"]) for f in feats.values()])

    bs = U.get(market) or []
    if len(bs) < 250:
        raise ValueError(f"insufficient bars for {market}")
    f = feats[market]
    closes = [b[4] for b in bs[-250:]]
    rsi = rsi_last(closes[-60:])
    atr = atr_last(bs[-60:])
    hi24 = max(b[2] for b in bs[-288:]); lo24 = min(b[3] for b in bs[-288:])
    rng_pos = (f["c"] - lo24) / (hi24 - lo24) if hi24 > lo24 else 0.5
    last6 = [f"{(bs[-i][4] / bs[-i - 1][4] - 1) * 1e4:+.0f}" for i in range(6, 0, -1)]
    btc = feats.get("BTCUSDT-PERP")

    def pc(x, d=2):
        return "n/a" if x is None else f"{100 * x:+.{d}f}%"

    t = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime((bar_ms + 300000) / 1000))
    lines = [
        f"DECISION — market {market} — bar closed {t} — current mark {price:.6g}",
        f"BRACKET (either direction): TP {tp_pct:.2f}% / SL {sl_pct:.2f}% / timeout {timeout_bars} bars "
        f"({timeout_bars * 5 / 60:.0f}h). Round-trip cost ≈ "
        f"{cost['taker'] + cost['maker'] + 2 * cost['spread']:.1f}–{2 * cost['taker'] + 2 * cost['spread']:.1f} bps.",
        "",
        f"UNIVERSE BREADTH ({n} markets)",
        f"  above EMA20: {100 * above20:.0f}%   above EMA50: {100 * above50:.0f}%   up over 24 bars (2h): {100 * up24:.0f}%",
        "UNIVERSE TREND (median return across markets)",
        f"  1h {pc(medR[12])}   2h {pc(medR[24])}   4h {pc(medR[48])}   8h {pc(medR[96])}   lookback agreement {agree:+.2f} (-1 all down, +1 all up)",
    ]
    if btc:
        lines.append(f"  BTC: 1h {pc(btc['R'][12])}  2h {pc(btc['R'][24])}  4h {pc(btc['R'][48])}  8h {pc(btc['R'][96])}")
    lines += [
        "FOLLOW-THROUGH",
        f"  bar-return autocorrelation (median, last 288 bars): "
        f"{'n/a' if ac is None else f'{ac:+.3f}'} -> "
        f"{'moves PERSIST' if (ac or 0) > 0 else 'moves GET FADED (chop)'}",
        "MEASURED EDGE — naive entries every 6 bars over the last 12h, THIS bracket, real costs",
        _edge_line("universe", list(U.values()), tp_pct, sl_pct, cost),
        _edge_line(market, [bs], tp_pct, sl_pct, cost),
        "",
        f"THIS MARKET ({market})",
        f"  return: 1h {pc(f['R'][12])}  2h {pc(f['R'][24])}  4h {pc(f['R'][48])}  8h {pc(f['R'][96])}  24h {pc(f['R'][288])}",
        f"  EMA20 {'>' if f['e20'] > f['e50'] else '<'} EMA50 (short-term trend {'UP' if f['e20'] > f['e50'] else 'DOWN'});  "
        f"close vs EMA20: {(f['c'] - f['e20']) / atr:+.2f} ATR ({pc(f['c'] / f['e20'] - 1)})",
        f"  RSI(14): {rsi:.1f}   ATR(14): {100 * atr / f['c']:.2f}% of price   position in 24h range: {100 * rng_pos:.0f}%",
        f"  last 6 bar returns (bps): {' '.join(last6)}",
    ]
    meta = dict(n_universe=n, last_bar_ms=last_bar, decision_bar_ms=bar_ms,
                max_bar_used_ms=max(last_bar.values()) if last_bar else None)
    return "\n".join(lines), meta


# ---------------------------------------------------------------- the call
def _validate(v):
    """(ok, fields | error). Same bar as direction_agent._validate: a reply that
    is missing fields, off-scale or out of enum is a NON-ANSWER, never a default."""
    miss = [k for k in ("direction", "confidence", "regime") if k not in v]
    if miss:
        return False, f"reply missing required fields: {','.join(miss)}"
    d = str(v.get("direction", "")).lower().strip()
    if d not in DIRECTIONS:
        return False, f"invalid direction {v.get('direction')!r}"
    try:
        conf = float(v.get("confidence"))
    except (TypeError, ValueError):
        return False, "non-numeric confidence"
    if not (0.0 <= conf <= 1.0):
        return False, f"off-scale confidence {conf} (model is not using the 0.0-1.0 scale)"
    reg = str(v.get("regime", "")).lower().strip()
    if reg not in REGIMES:
        return False, f"invalid regime {v.get('regime')!r}"
    return True, dict(direction=d, confidence=round(conf, 3), regime=reg,
                      reasoning=str(v.get("reasoning", "")).strip()[:800],
                      key_risk=str(v.get("key_risk", "")).strip()[:400])


def ask_once(evidence, qcfg):
    """One transport call. Returns a result dict; never raises; never defaults."""
    t0 = time.time()
    try:
        r = AT.chat_json(agent="direction", base_url=qcfg["base_url"], model=qcfg["model"],
                         key_env=qcfg["key_env"], user_agent=qcfg.get("user_agent", "why-bybit-qwen-shadow/1.0"),
                         system=SYSTEM, user=evidence, schema=SCHEMA, schema_name="shadow_direction_read",
                         max_tokens=int(qcfg["max_tokens"]), timeout=float(qcfg["http_timeout_sec"]),
                         reasoning_effort=qcfg.get("reasoning_effort", ""), json_schema_mode=True)
    except Exception as exc:                       # transport bug must not become a decision
        return dict(status="AGENT_ERROR", kind="exception", error=f"{type(exc).__name__}: {exc}"[:300],
                    latency_s=round(time.time() - t0, 3))
    lat = round(time.time() - t0, 3)
    if not r.get("ok"):
        return dict(status="AGENT_ERROR", kind=r.get("kind", "unknown"), error=str(r.get("error"))[:300],
                    quota_scope=r.get("quota_scope"), retry_after_sec=r.get("retry_after_sec"),
                    latency_s=lat)
    raw = r.get("parsed")
    ok, res = _validate(raw if isinstance(raw, dict) else {})
    if not ok:
        return dict(status="AGENT_ERROR", kind="invalid_output", error=res, qwen_raw=raw,
                    tokens=r.get("tokens"), latency_s=lat)
    return dict(status="OK", qwen_raw=raw, tokens=r.get("tokens"), model=r.get("model"),
                latency_s=lat, **res)


def ask_until(evidence, qcfg, deadline_ts, sleep=time.sleep, now=time.time):
    """Retry ONLY provider pacing (tpm 429 / governor deferral) while the decision
    deadline allows. A late answer is never returned as usable: past the
    deadline the result is a TIMEOUT non-answer."""
    attempts = 0
    while True:
        if now() >= deadline_ts:
            return dict(status="AGENT_ERROR", kind="timeout", error="decision deadline passed before an answer",
                        attempts=attempts)
        res = ask_once(evidence, qcfg)
        attempts += 1
        res["attempts"] = attempts
        if res["status"] == "OK":
            if now() > deadline_ts:          # stale answer must not approve
                res.update(status="AGENT_ERROR", kind="timeout", late_answer=True,
                           error="answer arrived after the decision deadline")
            return res
        wait = res.get("retry_after_sec")
        pace = res.get("kind") in ("budget_deferred", "rate_limit") and res.get("quota_scope") != "tpd"
        if pace and wait is not None and now() + float(wait) + 0.5 < deadline_ts:
            sleep(float(wait) + 0.5)
            continue
        return res
