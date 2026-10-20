#!/usr/bin/env python3
"""Shadow experiment metrics — pure function of candidates.jsonl + trades.jsonl
(+ evals.jsonl for bar counts). Read-only. `python3 report.py [--json]`.

Books:
  A  baseline WY, every candidate (what the live-engine logic would have traded)
  B  WY + Qwen agreement filter (approved candidates only)
  V  the vetoed/unapproved subset of A (what Qwen kept us out of)
  Q  counterfactual: Qwen's OPPOSITE direction, simulated (never a real trade)
"""
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("SHADOW_STATE_DIR") or os.path.join(HERE, "state")


def _jl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return out


def stats(trades, bankroll, span_days=None):
    t = sorted(trades, key=lambda r: r["ts"])
    n = len(t)
    if not n:
        return dict(n=0)
    net = [r["net_usd"] for r in t]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    gp, gl = sum(wins), -sum(x for x in net if x < 0)
    cum = peak = dd = 0.0
    for x in net:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    by = defaultdict(int)
    for r in t:
        by[r["exit_type"]] += 1
    holds = [r["hold_min"] for r in t]
    span = span_days
    if span is None:
        span = max((t[-1]["ts"] - t[0]["opened_ms"]) / 86400000, 1e-9)
    return dict(n=n, wins=len(wins), losses=len(losses), win_rate=round(100 * len(wins) / n, 1),
                gross_usd=round(sum(r["gross_usd"] for r in t), 3), net_usd=round(sum(net), 3),
                fees_usd=round(sum(r["fees_usd"] for r in t), 3),
                funding_usd=round(sum(r["funding_usd"] for r in t), 3),
                expectancy_usd=round(sum(net) / n, 4),
                expectancy_bps=round(sum(r["net_bps"] for r in t) / n, 2),
                gross_bps=round(sum(r["gross_bps"] for r in t) / n, 2),
                profit_factor=(round(gp / gl, 3) if gl > 0 else None),
                max_dd_usd=round(dd, 3), max_dd_pct_bankroll=round(100 * dd / bankroll, 2),
                targets=by["TARGET"], stops=by["STOP"], timeouts=by["TIMEOUT"],
                avg_hold_min=round(sum(holds) / n, 1),
                avg_mfe_pct=round(sum(r["mfe_pct"] for r in t) / n, 3),
                avg_mae_pct=round(sum(r["mae_pct"] for r in t) / n, 3),
                trades_per_day=(round(n / span, 2) if span_days else None))


def _seg(cands, key, A_by, bankroll):
    groups = defaultdict(list)
    for c in cands:
        k = key(c)
        if k is not None and c["cand_id"] in A_by:
            groups[k].append(A_by[c["cand_id"]])
    return {k: stats(v, bankroll) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def _bucket(conf):
    if conf is None:
        return None
    return "conf<0.5" if conf < 0.5 else ("conf 0.5-0.7" if conf < 0.7 else "conf>=0.7")


def compute(state_dir=STATE):
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    bankroll = cfg["sizing"]["bankroll_usd"]
    cands = [r for r in _jl(os.path.join(state_dir, "candidates.jsonl")) if r.get("event") == "CANDIDATE"]
    closes = [r for r in _jl(os.path.join(state_dir, "trades.jsonl")) if r.get("event") == "CLOSE"]
    evals = _jl(os.path.join(state_dir, "evals.jsonl"))
    A_by = {r["cand_id"]: r for r in closes if r["book"] == "A"}
    B_by = {r["cand_id"]: r for r in closes if r["book"] == "B"}
    Q_by = {r["cand_id"]: r for r in closes if r["book"] == "Q"}
    now = time.time() * 1000
    first = min([c["ts"] for c in cands] + [e["ts"] for e in evals], default=now)
    span_days = max((now - first) / 86400000, 1e-9)

    def q(c):
        return c.get("qwen") or {}

    ok = [c for c in cands if q(c).get("status") == "OK"]
    agree = [c for c in ok if q(c)["direction"] == c["wy_direction"]]
    opp = [c for c in ok if q(c)["direction"] not in (c["wy_direction"], "no_trade")]
    nt = [c for c in ok if q(c)["direction"] == "no_trade"]
    err = [c for c in cands if q(c).get("status") != "OK"]
    approved = [c for c in cands if c.get("approved")]
    vetoed = [c for c in cands if not c.get("approved")]
    entered_B = [c for c in cands if c.get("b_status") == "ENTERED"]

    A = [A_by[c["cand_id"]] for c in cands if c["cand_id"] in A_by]
    B = list(B_by.values())
    V = [A_by[c["cand_id"]] for c in vetoed if c["cand_id"] in A_by]
    Q = list(Q_by.values())
    sA, sB, sV, sQ = (stats(x, bankroll, span_days) for x in (A, B, V, Q))
    # A restricted to approved candidates that actually resolved in A (like-for-like with B)
    A_appr = [A_by[c["cand_id"]] for c in approved if c["cand_id"] in A_by]

    # filter quality: confusion on candidates whose A outcome is known
    conf = dict(approved_win=0, approved_loss=0, vetoed_win=0, vetoed_loss=0)
    for c in cands:
        a = A_by.get(c["cand_id"])
        if not a:
            continue
        conf[("approved_" if c.get("approved") else "vetoed_") + ("win" if a["net_usd"] > 0 else "loss")] += 1
    nl = conf["approved_loss"] + conf["vetoed_loss"]
    nw = conf["approved_win"] + conf["vetoed_win"]
    filt = dict(confusion=conf,
                losers_filtered_pct=round(100 * conf["vetoed_loss"] / nl, 1) if nl else None,
                winners_filtered_pct=round(100 * conf["vetoed_win"] / nw, 1) if nw else None,
                base_win_rate_pct=round(100 * nw / (nw + nl), 1) if nw + nl else None,
                approved_win_rate_pct=round(100 * conf["approved_win"] / (conf["approved_win"] + conf["approved_loss"]), 1)
                if conf["approved_win"] + conf["approved_loss"] else None,
                vetoed_win_rate_pct=round(100 * conf["vetoed_win"] / (conf["vetoed_win"] + conf["vetoed_loss"]), 1)
                if conf["vetoed_win"] + conf["vetoed_loss"] else None)

    def d(k):
        a, b = sA.get(k), sB.get(k)
        return None if a is None or b is None else round(b - a, 4)

    incremental = dict(
        expectancy_bps_B_minus_A=d("expectancy_bps"), expectancy_usd_B_minus_A=d("expectancy_usd"),
        profit_factor_B_minus_A=d("profit_factor"), max_dd_usd_B_minus_A=d("max_dd_usd"),
        win_rate_B_minus_A=d("win_rate"),
        frequency_B_over_A=(round(len(B) / len(A), 3) if A else None),
        net_usd_B_minus_A=d("net_usd"),
        note="B and A are different-size samples; expectancy/PF deltas are per-trade, net_usd delta is total. "
             "Read them only once n is large (see samples).")

    return dict(
        generated_utc=time.strftime("%FT%TZ", time.gmtime()),
        config=dict(policy=cfg["policy"]["name"], model=cfg["qwen"]["model"], sizing=cfg["sizing"]["source"],
                    bankroll=bankroll, margin=cfg["sizing"]["margin_per_trade_usd"], leverage=cfg["sizing"]["leverage"],
                    clip=cfg["sizing"]["clip_usd"], max_open=cfg["sizing"]["max_open"]),
        span_days=round(span_days, 3), first_ts=first,
        counts=dict(bars_evaluated=len(evals), candidates=len(cands), qwen_ok=len(ok), qwen_errors=len(err),
                    approved=len(approved), vetoed=len(vetoed), vetoed_no_trade=len(nt), vetoed_opposite=len(opp),
                    b_entered=len(entered_B),
                    closed=dict(A=len(A), B=len(B), V=len(V), Q=len(Q)),
                    open_or_pending=len(cands) - len(A)),
        agreement=dict(
            agree_rate_all_ok_pct=round(100 * len(agree) / len(ok), 1) if ok else None,
            agree_rate_directional_pct=round(100 * len(agree) / (len(agree) + len(opp)), 1) if (agree or opp) else None,
            no_trade_pct=round(100 * len(nt) / len(ok), 1) if ok else None,
            opposite_pct=round(100 * len(opp) / len(ok), 1) if ok else None),
        book_A_baseline=sA, book_B_wy_plus_qwen=sB, vetoed_V=sV, counterfactual_Q_qwen_opposite=sQ,
        A_on_approved_only=stats(A_appr, bankroll, span_days),
        performance_when=dict(
            qwen_agrees=stats([A_by[c["cand_id"]] for c in agree if c["cand_id"] in A_by], bankroll),
            qwen_disagrees_opposite=stats([A_by[c["cand_id"]] for c in opp if c["cand_id"] in A_by], bankroll),
            qwen_no_trade=stats([A_by[c["cand_id"]] for c in nt if c["cand_id"] in A_by], bankroll),
            qwen_error=stats([A_by[c["cand_id"]] for c in err if c["cand_id"] in A_by], bankroll)),
        by_confidence=_seg(ok, lambda c: _bucket(q(c).get("confidence")), A_by, bankroll),
        by_market=_seg(cands, lambda c: c["market"], A_by, bankroll),
        by_strategy=_seg(cands, lambda c: c["strategy"], A_by, bankroll),
        by_regime=_seg(ok, lambda c: q(c).get("regime"), A_by, bankroll),
        filter_quality=filt, incremental_from_qwen_filter=incremental,
        latency=dict(
            qwen_latency_s_avg=round(sum(q(c).get("latency_s", 0) for c in ok) / len(ok), 2) if ok else None,
            decision_latency_s_avg=round(sum(c.get("decision_latency_s", 0) for c in cands) / len(cands), 2) if cands else None),
        samples_warning=("n<30 completed trades per book: everything above is anecdote, not evidence"
                         if len(A) < 30 else None))


def _line(name, s):
    if not s or not s.get("n"):
        return f"  {name:28s} n=0"
    return (f"  {name:28s} n={s['n']:<4d} win {s['win_rate']:>5.1f}%  net ${s['net_usd']:>+8.2f}  "
            f"exp {s['expectancy_bps']:>+7.1f}bps (${s['expectancy_usd']:+.3f})  PF {s['profit_factor']}  "
            f"maxDD ${s['max_dd_usd']:.2f}  T/S/TO {s['targets']}/{s['stops']}/{s['timeouts']}  hold {s['avg_hold_min']}m")


def render(m):
    L = [f"SHADOW EXPERIMENT — {m['generated_utc']} — {m['config']['policy']} — model {m['config']['model']}",
         f"  sizing: bankroll ${m['config']['bankroll']} margin ${m['config']['margin']} x{m['config']['leverage']} "
         f"clip ${m['config']['clip']} max_open {m['config']['max_open']}   observed {m['span_days']}d",
         f"  counts: {json.dumps(m['counts'])}",
         f"  agreement: {json.dumps(m['agreement'])}", ""]
    for k, lbl in (("book_A_baseline", "A baseline WY"), ("book_B_wy_plus_qwen", "B WY+Qwen"),
                   ("vetoed_V", "V vetoed/unapproved"), ("counterfactual_Q_qwen_opposite", "Q Qwen-opposite (cf)"),
                   ("A_on_approved_only", "A on approved only")):
        L.append(_line(lbl, m[k]))
    L.append("")
    for grp in ("performance_when", "by_confidence", "by_strategy", "by_market", "by_regime"):
        L.append(f" {grp}:")
        for k, s in m[grp].items():
            L.append(_line(str(k), s))
    L += ["", f" filter quality: {json.dumps(m['filter_quality'])}",
          f" incremental (B vs A): {json.dumps(m['incremental_from_qwen_filter'])}",
          f" latency: {json.dumps(m['latency'])}"]
    if m.get("samples_warning"):
        L.append(f" !! {m['samples_warning']}")
    return "\n".join(L)


if __name__ == "__main__":
    m = compute()
    print(json.dumps(m, indent=1) if "--json" in sys.argv else render(m))
