#!/usr/bin/env python3
"""PARITY: the shadow's WY candidate logic and trade pricing must equal the live
engine's, on REAL Bybit bars. TEST-ONLY: this file imports the live module
read-only, in paper mode, with WB_STATE_DIR pointed at a scratch dir and
bytecode writing disabled. The shadow SERVICE never imports it (see
test_isolation.py).

    python3 tests/test_parity.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="wy_parity_")
os.environ.update(WB_MODE="paper", WB_STATE_DIR=TMP, WB_TAKER_BPS="5.5", WB_MAKER_BPS="2.0", WB_SPREAD_BPS="2.0",
                  WB_CLIP_USD="200", WB_HTTP_PORT="0")

spec = importlib.util.spec_from_file_location("bp_live_readonly", "/opt/why-bybit/bybit_paper.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)                       # module top-level starts no threads / no network

import sim as SIM                                  # noqa: E402
from wy_signals import wy_eval                     # noqa: E402

CFG = json.load(open(os.path.join(os.path.dirname(HERE), "config.json")))
W = CFG["wy"]


def bars(sym, n=1000):
    u = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=5&limit={n}"
    rows = json.load(urllib.request.urlopen(u, timeout=20))["result"]["list"]
    rows.sort(key=lambda r: int(r[0]))
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows[:-1]]


def test_signal_parity():
    captured = []
    bp.open_position = lambda m, side, strat, price, detail: captured.append((m, side, strat, dict(detail)))
    bp.log = lambda *a, **k: None
    n_eval = n_fire = n_mismatch = 0
    kinds = {}
    for m in bp.MARKETS:
        hist = bars(bp.BYBIT[m])
        time.sleep(0.15)
        for i in range(250, len(hist)):
            window = hist[:i]
            captured.clear()
            bp.CANDLES[m] = list(window)
            bp.POS.clear()
            bp.MARK[m] = (window[-1][4], time.time())
            bp.on_bar_close(m)
            live = captured[0] if captured else None
            mine = wy_eval(window, W["rv_rsi_lo"], W["rv_rsi_hi"], W["pb_depth_atr"])
            mine_f = (m, mine["fired"][0], mine["fired"][1], {k: v for k, v in mine["det"].items()}) if mine["fired"] else None
            live_cmp = (live[0], live[1], live[2], {k: v for k, v in live[3].items() if k != "bar_ms"}) if live else None
            n_eval += 1
            if mine_f:
                n_fire += 1
                kinds[f"{mine_f[2]}:{mine_f[1]}"] = kinds.get(f"{mine_f[2]}:{mine_f[1]}", 0) + 1
            if live_cmp != mine_f:
                n_mismatch += 1
                print("MISMATCH", m, window[-1][0], live_cmp, mine_f)
    # the live engine also wrote one evals.jsonl row per evaluation: compare indicators
    rows = [json.loads(l) for l in open(bp.EVALS)]
    assert len(rows) == n_eval, (len(rows), n_eval)
    print(f"signal parity: {n_eval} evaluations across {len(bp.MARKETS)} markets, {n_fire} WY signals {kinds}, mismatches={n_mismatch}")
    assert n_mismatch == 0
    assert n_fire >= 5, "too few signals to be a meaningful parity sample"


def test_indicator_parity():
    m = bp.MARKETS[0]
    hist = bars(bp.BYBIT[m], 400)[:-1]
    closes = [b[4] for b in hist[-250:]]
    from wy_signals import ema_last, rsi_last, atr_last
    assert ema_last(closes, 20) == bp.ema_last(closes, 20)
    assert ema_last(closes, 50) == bp.ema_last(closes, 50)
    assert rsi_last(closes[-60:]) == bp.rsi_last(closes[-60:])
    assert atr_last(hist[-60:]) == bp.atr_last(hist[-60:])
    print("indicator parity: exact equality on EMA20/EMA50/RSI/ATR")


def test_pricing_parity():
    """Same entry/exit/fees/PnL as live open_position+close_position for each exit type."""
    bp.funding_cost = lambda m, pos, close_ms: (0.0, 0)
    bp.log = lambda *a, **k: None
    bp.atomic_write = lambda *a, **k: None
    journal = []
    bp.journal_append = lambda rec: journal.append(rec)
    bp.CFG["clip_usd"] = 200.0
    m = "SOLUSDT-PERP"
    checked = 0
    for side in ("long", "short"):
        for strat in ("WY-REVERT", "WY-PULLBACK"):
            for reason, frac in (("TARGET", 1.0), ("STOP", 1.0), ("TIMEOUT", 0.4)):
                bp.POS.clear(); journal.clear()
                ref = 150.123
                bp.CANDLES[m] = [[0, 1, 1, 1, 1]] * 250
                bp.open_position(m, side, strat, ref, dict(rsi=25.0, trend="up"))
                pos = bp.POS[m]
                t = SIM.new_trade("x", "A", m, side, strat, ref, 1_000_000, CFG)
                for k in ("entry", "target", "stop", "notional", "fee_in", "tp_pct", "sl_pct"):
                    assert abs(pos[k] - t[k]) < 1e-9, (k, pos[k], t[k])
                level = {"TARGET": t["target"], "STOP": t["stop"], "TIMEOUT": ref * 1.001}[reason]
                bp.close_position(m, level, reason)
                live = [j for j in journal if j["event"] == "CLOSE"][0]
                mine = SIM.close_record(dict(t, opened_ms=live["ts"] - int(live["hold_min"] * 60000)), level, reason, live["ts"], CFG, 0.0, 0)
                for a, b in (("pnl_usd", "net_usd"), ("gross_usd", "gross_usd"), ("fees_usd", "fees_usd"), ("exit", "exit")):
                    assert abs(live[a] - mine[b]) < 2e-4, (side, strat, reason, a, live[a], mine[b])
                checked += 1
    print(f"pricing parity: {checked} open->close cycles match live entry/exit/fees/net (funding excluded on both sides)")


if __name__ == "__main__":
    test_indicator_parity()
    test_pricing_parity()
    test_signal_parity()
    print("PARITY OK")
