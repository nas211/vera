#!/usr/bin/env python3
"""END-TO-END lifecycle on REAL historical Bybit bars, SCRATCH state dir, fake Qwen
transport. Nothing here touches the running service's state or the live system.

  real WY signal -> candidate -> book A trade -> Qwen verdict -> policy -> book B / Q ->
  chronological mark path -> TARGET/STOP/TIMEOUT -> closed records -> report metrics

    python3 tests/test_e2e.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["SHADOW_STATE_DIR"] = tempfile.mkdtemp(prefix="shadow_e2e_")
os.environ["AGENT_QUOTA_FILE"] = os.path.join(os.environ["SHADOW_STATE_DIR"], "q.json")
os.environ["GROQ_API_KEY"] = "test-not-a-key"

import shadow as S             # noqa: E402
import qwen_agent as QA        # noqa: E402
import report as REP           # noqa: E402
from wy_signals import wy_eval  # noqa: E402


def hist(sym, n=1000):
    u = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=5&limit={n}"
    rows = json.load(urllib.request.urlopen(u, timeout=20))["result"]["list"]
    rows.sort(key=lambda r: int(r[0]))
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows[:-1]]


def find_signals(limit=6):
    out = []
    for m in S.MARKETS:
        h = hist(S.BYBIT[m]); time.sleep(0.15)
        S.CANDLES[m] = h
        i = 250
        while i < len(h) - 200 and len(out) < limit:
            r = wy_eval(h[:i])
            if r and r["fired"]:
                out.append((m, i, r["fired"])); i += 200
            i += 1
    return out


def drive_marks(m, bars_after):
    """Chronological path from REAL later bars: o -> (l,h ordered by bar direction) -> c, then bar close."""
    for b in bars_after:
        path = [b[1], b[3], b[2], b[4]] if b[4] >= b[1] else [b[1], b[2], b[3], b[4]]
        for p in path:
            S.MARK[m] = (p, time.time()); S.on_mark(m, p)
        S.CANDLES[m].append(b); S.on_bar_close(m)
        if not any(t["market"] == m for t in S.TRADES.values()):
            return


def run_case(m, i, fired, qwen_direction):
    h = S.CANDLES[m] if False else None
    return None


def main():
    S.CFG["qwen"]["decision_deadline_sec"] = 60
    sigs = find_signals(6)
    assert len(sigs) >= 4, f"need real signals, found {len(sigs)}"
    verdicts = ["agree", "opposite", "no_trade", "agree", "error", "opposite"]
    real_chat = QA.AT.chat_json
    for (m, i, fired), how in zip(sigs, verdicts):
        H = hist(S.BYBIT[m]); time.sleep(0.15)
        S.CANDLES.update({k: (H if k == m else S.CANDLES[k]) for k in S.MARKETS})
        S.TRADES.clear(); S.PENDING.clear()
        while not S.JOBS.empty():
            S.JOBS.get_nowait()
        S.CANDLES[m] = H[:i]
        for k in S.MARKETS:
            if k != m:
                S.CANDLES[k] = S.CANDLES[k][: i]
        S.LAST_BAR.clear()
        S.MARK[m] = (H[i - 1][4], time.time())
        side, strat = fired
        want = {"agree": side, "opposite": "short" if side == "long" else "long", "no_trade": "no_trade"}
        if how == "error":
            QA.AT.chat_json = lambda **kw: dict(ok=False, kind="rate_limit", quota_scope="tpd", retry_after_sec=900.0, error="429 tpd")
        else:
            d = want[how]
            QA.AT.chat_json = lambda d=d, **kw: dict(ok=True, parsed=dict(direction=d, confidence=0.62, regime="trend_up", reasoning="fake", key_risk="fake"),
                                                      tokens={"in": 1200, "out": 150}, model="fake")
        S.on_bar_close(m)                                            # real signal fires -> candidate + book A
        assert S.PENDING, f"no candidate created for {m} {fired}"
        cid = S.JOBS.get_nowait()
        c = dict(S.PENDING[cid])
        uni = S.snapshot_universe(c["bar_ms"])
        tp, sl = ((S.W["rv_tp_pct"], S.W["rv_sl_pct"]) if strat == "WY-REVERT" else (S.W["pb_tp_pct"], S.W["pb_sl_pct"]))
        ev, meta = QA.build_evidence(m, c["bar_ms"], c["price_at_decision"], uni, tp, sl, 144, dict(taker=5.5, maker=2.0, spread=2.0))
        res = QA.ask_until(ev, S.QC, c["deadline"], sleep=lambda s: None)
        S.finalize(cid, res, ev, meta)
        cand = [json.loads(l) for l in open(S.P["cands"])][-1]
        print(f"{m:14s} {strat:11s} {side:5s} qwen={how:8s} -> {cand['policy_decision']:24s} b={cand['b_status']:12s} books={sorted(t['book'] for t in S.TRADES.values())}")
        exp = {"agree": ("APPROVED", ["A", "B"]), "opposite": ("VETOED_OPPOSITE", ["A", "Q"]), "no_trade": ("VETOED_NO_TRADE", ["A"]),
               "error": ("NO_APPROVAL_AGENT_ERROR", ["A"])}[how]
        assert cand["policy_decision"] == exp[0] and sorted(t["book"] for t in S.TRADES.values()) == exp[1]
        drive_marks(m, H[i:])                                        # REAL later bars, chronological, no peeking ahead
    QA.AT.chat_json = real_chat
    closes = [json.loads(l) for l in open(S.P["trades"]) if '"CLOSE"' in l]
    assert closes, "no trade ever closed"
    for r in closes:
        assert r["exit_type"] in ("TARGET", "STOP", "TIMEOUT") and abs(r["net_usd"] - (r["gross_usd"] - r["fees_usd"] - r["funding_usd"])) < 2e-4
    m_ = REP.compute(S.STATE)
    print("closed trades:", {b: sum(1 for r in closes if r["book"] == b) for b in "ABQ"})
    print(REP.render(m_)[:1800])
    assert m_["counts"]["candidates"] >= 4
    print("E2E OK")


if __name__ == "__main__":
    main()
