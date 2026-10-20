#!/usr/bin/env python3
"""Frozen-policy + FAIL-CLOSED tests. No network, no live imports. Scratch state dir.

    python3 tests/test_policy.py
"""
import json
import os
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["SHADOW_STATE_DIR"] = tempfile.mkdtemp(prefix="shadow_policy_")
os.environ["AGENT_QUOTA_FILE"] = os.path.join(os.environ["SHADOW_STATE_DIR"], "q.json")
os.environ["GROQ_API_KEY"] = "test-not-a-key"

import shadow as S                # noqa: E402
import qwen_agent as QA           # noqa: E402


def cand(direction="long", m="SOLUSDT-PERP"):
    cid = f"{int(time.time()*1000)}-{m}-{direction}"
    S.MARK[m] = (150.0, time.time())
    S.PENDING[cid] = dict(cand_id=cid, ts=int(time.time() * 1000), bar_ms=0, market=m, strategy="WY-PULLBACK",
                          wy_direction=direction, wy_detail={}, price_at_decision=150.0, a_entry=150.0, deadline=time.time() + 90)
    return cid


def last_cand():
    return [json.loads(l) for l in open(S.P["cands"])][-1]


def books():
    return sorted(t["book"] for t in S.TRADES.values())


def test_decide_table():
    ok = lambda d: dict(status="OK", direction=d, confidence=0.9, regime="trend_up")
    assert S.decide(dict(wy_direction="long"), ok("long")) == ("APPROVED", True)
    assert S.decide(dict(wy_direction="short"), ok("short")) == ("APPROVED", True)
    assert S.decide(dict(wy_direction="long"), ok("no_trade")) == ("VETOED_NO_TRADE", False)
    assert S.decide(dict(wy_direction="long"), ok("short")) == ("VETOED_OPPOSITE", False)
    assert S.decide(dict(wy_direction="short"), ok("long")) == ("VETOED_OPPOSITE", False)
    for kind in ("timeout", "rate_limit", "budget_deferred", "provider_error", "invalid_output", "config_error",
                 "provider_unreachable", "truncated", "worker_exception", "restart_lost", "exception"):
        assert S.decide(dict(wy_direction="long"), dict(status="AGENT_ERROR", kind=kind)) == ("NO_APPROVAL_AGENT_ERROR", False)
    assert S.decide(dict(wy_direction="long"), {}) == ("NO_APPROVAL_AGENT_ERROR", False)   # empty result never approves
    print("decide(): approve only on exact agreement; every error kind and an empty result -> no approval")


def test_finalize_paths():
    S.TRADES.clear()
    cid = cand("long"); S.finalize(cid, dict(status="OK", direction="long", confidence=0.6, regime="trend_up"))
    r = last_cand(); assert r["approved"] and r["b_status"] == "ENTERED" and books() == ["B"], (r["b_status"], books())
    S.TRADES.clear()
    cid = cand("long", "ETHUSDT-PERP"); S.finalize(cid, dict(status="OK", direction="short", confidence=0.6, regime="trend_down"))
    r = last_cand(); assert r["policy_decision"] == "VETOED_OPPOSITE" and not r["approved"] and books() == ["Q"]
    assert [t for t in S.TRADES.values()][0]["side"] == "short"      # the OPPOSITE call is simulated as Q, never reversed into B
    S.TRADES.clear()
    cid = cand("long", "XRPUSDT-PERP"); S.finalize(cid, dict(status="OK", direction="no_trade", confidence=0.3, regime="range_chop"))
    r = last_cand(); assert r["policy_decision"] == "VETOED_NO_TRADE" and books() == []
    print("finalize(): agree->B enters | opposite->veto + Q counterfactual (opposite side) | no_trade->veto, nothing opened")


def test_agent_errors_never_approve():
    S.TRADES.clear()
    cases = {
        "provider_error": dict(ok=False, kind="provider_error", error="HTTP 404"),
        "rate_limit_tpd": dict(ok=False, kind="rate_limit", quota_scope="tpd", retry_after_sec=600.0, error="429"),
        "unreachable": dict(ok=False, kind="provider_unreachable", error="URLError"),
        "config": dict(ok=False, kind="config_error", error="GROQ_API_KEY not set"),
        "malformed_missing": dict(ok=True, parsed={"confidence": 0.9}),
        "bad_direction": dict(ok=True, parsed={"direction": "buy", "confidence": 0.9, "regime": "trend_up"}),
        "off_scale_conf": dict(ok=True, parsed={"direction": "long", "confidence": 95, "regime": "trend_up"}),
        "bad_regime": dict(ok=True, parsed={"direction": "long", "confidence": 0.9, "regime": "moon"}),
        "not_dict": dict(ok=True, parsed=["long"]),
    }
    real = QA.AT.chat_json
    for name, ret in cases.items():
        QA.AT.chat_json = lambda **kw: ret
        res = QA.ask_until("ev", dict(S.QC), time.time() + 5, sleep=lambda s: None)
        assert res["status"] == "AGENT_ERROR", (name, res)
        cid = cand("long"); S.finalize(cid, res)
        r = last_cand()
        assert not r["approved"] and r["b_status"] == "NOT_APPROVED" and books() == [], (name, r["policy_decision"])
    QA.AT.chat_json = real
    print(f"{len(cases)} malformed/failed provider responses: every one is AGENT_ERROR, never approved, no trade opened")


def test_exception_in_transport_fails_closed():
    real = QA.AT.chat_json
    def boom(**kw): raise RuntimeError("transport bug")
    QA.AT.chat_json = boom
    res = QA.ask_until("ev", dict(S.QC), time.time() + 5)
    QA.AT.chat_json = real
    assert res["status"] == "AGENT_ERROR" and res["kind"] == "exception"
    print("exception inside the transport -> AGENT_ERROR (no default direction)")


def test_stale_answer_cannot_approve():
    real = QA.AT.chat_json
    QA.AT.chat_json = lambda **kw: dict(ok=True, parsed=dict(direction="long", confidence=0.8, regime="trend_up",
                                                             reasoning="x", key_risk="y"), tokens={"in": 1, "out": 1})
    # answer arrives AFTER the decision deadline
    clock = [1000.0]
    res = QA.ask_until("ev", dict(S.QC), 1000.5, now=lambda: clock.__setitem__(0, clock[0] + 0.6) or clock[0] - 0.55)
    QA.AT.chat_json = real
    assert res["status"] == "AGENT_ERROR" and res["kind"] == "timeout", res
    assert S.decide(dict(wy_direction="long"), res) == ("NO_APPROVAL_AGENT_ERROR", False)
    # deadline already passed -> the provider is never even called
    called = []
    QA.AT.chat_json = lambda **kw: called.append(1) or {}
    res = QA.ask_until("ev", dict(S.QC), time.time() - 1)
    QA.AT.chat_json = real
    assert res["kind"] == "timeout" and not called
    print("late answer -> TIMEOUT non-answer; expired deadline -> provider not called; a stale read can never approve a later signal")


def test_pacing_retry_then_deadline():
    real = QA.AT.chat_json
    seq = [dict(ok=False, kind="rate_limit", quota_scope="tpm", retry_after_sec=2.0, error="tpm"),
           dict(ok=True, parsed=dict(direction="long", confidence=0.5, regime="trend_up", reasoning="", key_risk=""), tokens={})]
    slept = []
    QA.AT.chat_json = lambda **kw: seq.pop(0)
    res = QA.ask_until("ev", dict(S.QC), time.time() + 60, sleep=lambda s: slept.append(s))
    QA.AT.chat_json = real
    assert res["status"] == "OK" and slept == [2.5] and res["attempts"] == 2
    # a TPD 429 is NOT waited out inside a 90s decision window
    QA.AT.chat_json = lambda **kw: dict(ok=False, kind="rate_limit", quota_scope="tpd", retry_after_sec=600.0, error="tpd")
    res = QA.ask_until("ev", dict(S.QC), time.time() + 60, sleep=lambda s: slept.append(s))
    QA.AT.chat_json = real
    assert res["status"] == "AGENT_ERROR" and res["kind"] == "rate_limit"
    print("tpm 429 waited out inside the deadline (real answer); tpd 429 fails closed immediately")


def test_restart_loses_inflight_as_nonanswer():
    S.TRADES.clear(); S.PENDING.clear()
    cid = cand("long")
    S.persist()
    S.PENDING.clear()
    S.load_state()                       # what a restart does
    r = last_cand()
    assert r["cand_id"] == cid and r["qwen"]["kind"] == "restart_lost" and not r["approved"]
    print("restart with an in-flight Qwen call -> recorded as restart_lost, not approved")


def test_no_lookahead_in_evidence():
    """Bars newer than the decision bar must never reach the agent."""
    import random
    random.seed(1)
    px = 100.0
    bars = []
    for i in range(400):
        o = px; px *= 1 + random.uniform(-0.004, 0.004)
        bars.append([1_700_000_000_000 + i * 300000, o, max(o, px) * 1.001, min(o, px) * 0.999, px])
    decision_bar = bars[300][0]
    future_poison = [b[:] for b in bars]
    for b in future_poison[301:]:                    # wildly different FUTURE
        b[1:] = [b[4] * 10] * 4
    U1 = {"X-PERP": bars, "BTCUSDT-PERP": bars}
    U2 = {"X-PERP": future_poison, "BTCUSDT-PERP": future_poison}
    cost = dict(taker=5.5, maker=2.0, spread=2.0)
    e1, m1 = QA.build_evidence("X-PERP", decision_bar, bars[300][4], U1, 0.8, 2.0, 144, cost)
    e2, m2 = QA.build_evidence("X-PERP", decision_bar, bars[300][4], U2, 0.8, 2.0, 144, cost)
    assert e1 == e2, "evidence changed when only FUTURE bars changed -> lookahead"
    assert m1["max_bar_used_ms"] == decision_bar
    print("evidence is byte-identical when only future bars are altered; max bar used == decision bar (no lookahead)")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("POLICY / FAIL-CLOSED OK")
