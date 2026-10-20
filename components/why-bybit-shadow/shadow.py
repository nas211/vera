#!/usr/bin/env python3
"""WHY x Bybit — QWEN SHADOW BOOK. Forward SHADOW experiment. NO ORDERS. NO KEYS.

Runs beside the live why-bybit engine and shares NOTHING with it: its own public
Bybit websocket, its own state dir, its own credentials (a Groq key only). There
is no import of, file path into, or IPC channel to the live system. Whatever this
process does — crash, hang, be deleted — the live book cannot notice.

Books (all simulated, priced like the live engine's paper accounting):
  A  baseline WY: every candidate the WY logic fires (own one-per-market + max_open)
  B  WY + Qwen agreement filter: enters only candidates Qwen APPROVES
  Q  counterfactual: Qwen's opposite-direction call, simulated, never traded

Frozen policy (config.json): Qwen (blind to WY's direction) reads LONG/SHORT/NO_TRADE.
  agrees -> approve | NO_TRADE -> veto | opposite -> veto (logged + simulated as Q)
  any error/timeout/malformed/late answer -> no approval (fail closed, shadow only)

    shadow.py run       the service
    shadow.py report    metrics from the files
    shadow.py probe     one Qwen call on current real data; NOT written to the dataset
"""
import json
import os
import queue
import signal
import sys
import threading
import time
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qwen_agent as QA          # noqa: E402
import report as REP             # noqa: E402
import sim as SIM                # noqa: E402
from wy_signals import wy_eval   # noqa: E402

CFG = json.load(open(os.path.join(HERE, "config.json")))
STATE = os.environ.get("SHADOW_STATE_DIR") or os.path.join(HERE, "state")
os.makedirs(STATE, exist_ok=True)
P = {k: os.path.join(STATE, v) for k, v in dict(
    cands="candidates.jsonl", trades="trades.jsonl", evals="evals.jsonl", events="events.jsonl",
    pos="positions.json", snap="snapshot.json", metrics="metrics.json", probe="probe.jsonl").items()}

MARKETS = CFG["markets"]
BYBIT = {m: m.replace("-PERP", "") for m in MARKETS}
FROM_BYBIT = {v: k for k, v in BYBIT.items()}
WS_URL = os.environ.get("SHADOW_WS_URL", "wss://stream.bybit.com/v5/public/linear")
W, FEES, SIZ, QC = CFG["wy"], CFG["fees"], CFG["sizing"], CFG["qwen"]
BAR_MS = 300000
MARK_MAX_AGE_S = 30.0

LOCK = threading.RLock()
CANDLES = {m: [] for m in MARKETS}
CURBAR = {m: None for m in MARKETS}     # forming bar [ms,o,h,l,c]
MARK = {m: None for m in MARKETS}       # (price, recv_ts)
TRADES = {}                              # id -> open sim trade (books A/B/Q)
PENDING = {}                             # cand_id -> candidate awaiting Qwen
LAST_BAR = {}                            # market -> last closed bar start processed
SEEN = set()                             # candidate ids ever created
LAST_EVAL = {}                           # market -> last evaluation record
JOBS = queue.Queue()
STARTED = time.time()
WS_OK = dict(connected=False, last_msg=0, last_exch_ts=0, latency_ms=None, reconnects=0, connects=0, last_error=None, msgs=0)
CNT = dict(bars_evaluated=0, candidates=0, skipped_open=0, skipped_capacity=0, stale_mark=0,
           qwen_calls=0, qwen_ok=0, qwen_errors=0, gap_fills=0)
FUND_INTERVAL_MS, FUND_LAST, FUND_CACHE = {}, {}, {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def atomic_write(path, obj):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def append(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def event(kind, **kw):
    append(P["events"], dict(ts=int(time.time() * 1000), event=kind, **kw))


def persist():
    with LOCK:
        atomic_write(P["pos"], dict(saved_ms=int(time.time() * 1000), trades=TRADES, pending=PENDING, last_bar_ms=LAST_BAR))


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "why-bybit-qwen-shadow/1.0"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


# ----------------------------------------------------------------- data (PUBLIC Bybit only)
def fetch_bars(m, limit=400):
    d = http_json(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={BYBIT[m]}&interval=5&limit={limit}")
    rows = d.get("result", {}).get("list", [])
    rows.sort(key=lambda r: int(r[0]))
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows[:-1]]   # drop the forming bar


def seed_candles():
    for m in MARKETS:
        try:
            CANDLES[m] = fetch_bars(m)
            log(f"seeded {m}: {len(CANDLES[m])} bars")
        except Exception as e:
            log(f"seed {m} FAILED: {e}")
        time.sleep(0.12)


def load_fund_intervals():
    try:
        d = http_json("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")
        by = {x["symbol"]: x for x in d["result"]["list"]}
        for m in MARKETS:
            FUND_INTERVAL_MS[m] = int(by[BYBIT[m]].get("fundingInterval", 480)) * 60000
    except Exception as e:
        log("funding interval load failed, defaulting to 8h:", e)


def funding_rate_at(m, ts_ms):
    key = (BYBIT[m], ts_ms)
    if key in FUND_CACHE:
        return FUND_CACHE[key]
    try:
        l = http_json(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={BYBIT[m]}"
                      f"&startTime={ts_ms}&endTime={ts_ms}&limit=1", timeout=3)["result"]["list"]
        if l:
            FUND_CACHE[key] = float(l[0]["fundingRate"])
            return FUND_CACHE[key]
    except Exception:
        pass
    return FUND_LAST.get(m, 0.0001)


def funding_cost(m, t, close_ms):
    iv = FUND_INTERVAL_MS.get(m, 8 * 3600 * 1000)
    ts = (t["opened_ms"] // iv + 1) * iv
    cost, n = 0.0, 0
    while ts <= close_ms:
        cost += t["notional"] * funding_rate_at(m, ts) * (1 if t["side"] == "long" else -1)
        n += 1
        ts += iv
    return cost, n


# ----------------------------------------------------------------- simulated books
def open_sim(cand_id, book, market, side, strat, ref_price, now_ms):
    t = SIM.new_trade(cand_id, book, market, side, strat, ref_price, now_ms, CFG)
    TRADES[t["id"]] = t
    append(P["trades"], dict(event="OPEN", ts=int(now_ms), cand_id=cand_id, book=book, market=market, side=side,
                             strategy=strat, entry=t["entry"], target=t["target"], stop=t["stop"], notional=t["notional"]))
    return t


def close_sim(t, level, reason, now_ms):
    with LOCK:
        if TRADES.pop(t["id"], None) is None:
            return
        fund, n = funding_cost(t["market"], t, now_ms)
        rec = SIM.close_record(t, level, reason, now_ms, CFG, fund, n)
        append(P["trades"], rec)
        persist()
    log(f"CLOSE[{t['book']}] {t['strategy']} {t['side']} {t['market']} {reason} net ${rec['net_usd']:+.3f} ({rec['net_bps']:+.1f}bps)")


def mark_fresh(m):
    mk = MARK[m]
    return mk is not None and time.time() - mk[1] <= MARK_MAX_AGE_S


def on_mark(m, price):
    with LOCK:
        for t in [t for t in TRADES.values() if t["market"] == m]:
            r = SIM.mark_step(t, price)
            if r:
                close_sim(t, t["stop"] if r == "STOP" else t["target"], r, int(time.time() * 1000))


def replay_bar(m, bar):
    """Historical bar for OPEN trades only (restart/gap catch-up). Pessimistic ties."""
    with LOCK:
        for t in [t for t in TRADES.values() if t["market"] == m and bar[0] >= t["opened_ms"] - 60000]:
            r, lvl = SIM.bar_step(t, bar)
            if r:
                close_sim(t, lvl, r, bar[0] + BAR_MS)
                continue
            t["bars_held"] += 1
            if t["bars_held"] >= W["timeout_bars"]:
                close_sim(t, bar[4], "TIMEOUT", bar[0] + BAR_MS)
        LAST_BAR[m] = max(LAST_BAR.get(m, 0), bar[0])


def fill_gap(m, upto_ms):
    """Feed dropped bars (WS reconnect): fetch the missing CLOSED bars from public
    REST, replay them for open trades, and keep indicators contiguous."""
    try:
        have = CANDLES[m][-1][0] if CANDLES[m] else 0
        new = [b for b in fetch_bars(m, 200) if have < b[0] < upto_ms]
    except Exception as e:
        log(f"gap fill {m} FAILED: {e!r}")
        return
    with LOCK:
        for b in new:
            CANDLES[m].append(b)
            replay_bar(m, b)
        CNT["gap_fills"] += len(new)
    if new:
        event("GAP_FILLED", market=m, bars=len(new))
        log(f"gap filled {m}: {len(new)} bars")


def on_bar_close(m):
    """Called when a new bar start arrives for `m` (same trigger as the live engine)."""
    t0 = time.time()
    bars = CANDLES[m]
    bar_ms = bars[-1][0]
    with LOCK:
        LAST_BAR[m] = bar_ms
        if len(bars) < W["warmup_bars"]:
            return
        held = [t for t in TRADES.values() if t["market"] == m and t["book"] == "A"]
        for t in [t for t in TRADES.values() if t["market"] == m]:
            t["bars_held"] += 1
            if t["bars_held"] >= W["timeout_bars"]:
                px = MARK[m][0] if MARK[m] else bars[-1][4]
                close_sim(t, px, "TIMEOUT", int(t0 * 1000))
        persist()
        if held:                                                     # live semantics: no evaluation while a position is open
            CNT["skipped_open"] += 1
            return
        if sum(1 for t in TRADES.values() if t["book"] == "A") >= SIZ["max_open"]:
            CNT["skipped_capacity"] += 1
            return
        r = wy_eval(bars, W["rv_rsi_lo"], W["rv_rsi_hi"], W["pb_depth_atr"])
        CNT["bars_evaluated"] += 1
        ev = dict(ts=int(t0 * 1000), market=m, bar_ms=bar_ms, close=r["close"], rsi=round(r["rsi"], 1),
                  depth_atr=round(r["depth_atr"], 2), trend="up" if r["up"] else "down",
                  wy_candidate=(f"{r['fired'][1]}:{r['fired'][0]}" if r["fired"] else "NONE"),
                  qwen_invoked=bool(r["fired"]),
                  qwen_status=("QUEUED (candidate-only policy: WY fired)" if r["fired"]
                               else "NOT_INVOKED (candidate-only policy: no WY candidate)"))
        cand_id = f"{bar_ms}-{m}"
        if r["fired"] and (cand_id in SEEN):
            return
        if r["fired"] and not mark_fresh(m):
            ev.update(qwen_invoked=False, qwen_status="NOT_INVOKED (stale/absent mark >30s: candidate skipped)")
            CNT["stale_mark"] += 1
            append(P["evals"], ev)
            LAST_EVAL[m] = ev
            event("CANDIDATE_SKIPPED_STALE_MARK", market=m, bar_ms=bar_ms)
            return
        append(P["evals"], ev)
        LAST_EVAL[m] = ev
        if not r["fired"]:
            return
        side, strat = r["fired"]
        SEEN.add(cand_id)
        px = MARK[m][0]
        CNT["candidates"] += 1
        a = open_sim(cand_id, "A", m, side, strat, px, t0 * 1000)
        PENDING[cand_id] = dict(cand_id=cand_id, ts=int(t0 * 1000), bar_ms=bar_ms, market=m, strategy=strat,
                                wy_direction=side, wy_detail=dict(r["det"], rsi=round(r["rsi"], 1), depth_atr=round(r["depth_atr"], 2)),
                                price_at_decision=px, a_entry=a["entry"],
                                deadline=t0 + QC["decision_deadline_sec"])
        persist()
        JOBS.put(cand_id)
    log(f"CANDIDATE {strat} {side} {m} @ {px:.6g} -> Qwen queued")


# ----------------------------------------------------------------- Qwen worker + frozen policy
def snapshot_universe(bar_ms):
    """Wait (<=3s) for the other markets' bar_ms bar to land, then copy. Only bars
    with start <= bar_ms are ever handed to the agent."""
    end = time.time() + 3.0
    while time.time() < end:
        with LOCK:
            if all(CANDLES[m] and CANDLES[m][-1][0] >= bar_ms for m in MARKETS):
                break
        time.sleep(0.2)
    with LOCK:
        return {m: [b for b in CANDLES[m] if b[0] <= bar_ms] for m in MARKETS}


def decide(c, res):
    """THE FROZEN POLICY. Nothing else in this file decides an approval."""
    if res.get("status") != "OK":
        return "NO_APPROVAL_AGENT_ERROR", False
    d = res["direction"]
    if d == c["wy_direction"]:
        return "APPROVED", True
    if d == "no_trade":
        return "VETOED_NO_TRADE", False
    return "VETOED_OPPOSITE", False


def finalize(cand_id, res, evidence=None, meta=None):
    with LOCK:
        c = PENDING.pop(cand_id, None)
        if c is None:
            return
        now = time.time()
        m = c["market"]
        decision, approved = decide(c, res)
        rec = dict(event="CANDIDATE", cand_id=cand_id, ts=c["ts"], bar_ms=c["bar_ms"], market=m,
                   strategy=c["strategy"], wy_direction=c["wy_direction"], wy_detail=c["wy_detail"],
                   price_at_decision=c["price_at_decision"], input_max_bar_ms=(meta or {}).get("max_bar_used_ms"),
                   qwen={k: v for k, v in res.items()}, decision_latency_s=round(now - c["ts"] / 1000, 3),
                   verdict_ts=int(now * 1000), policy_decision=decision, approved=approved,
                   b_status="NOT_APPROVED", sizing_ref=dict(clip=SIZ["clip_usd"], max_open=SIZ["max_open"], source="post-transition target"),
                   evidence=evidence, evidence_meta=meta)
        if approved:
            b_open = [t for t in TRADES.values() if t["book"] == "B"]
            if any(t["market"] == m for t in b_open):
                rec["b_status"] = "SLOT_BUSY"
            elif len(b_open) >= SIZ["max_open"]:
                rec["b_status"] = "CAPACITY"
            elif not mark_fresh(m):
                rec["b_status"] = "STALE_MARK"
            else:
                b = open_sim(cand_id, "B", m, c["wy_direction"], c["strategy"], MARK[m][0], now * 1000)
                rec.update(b_status="ENTERED", b_entry=b["entry"], entry_delay_s=round(now - c["ts"] / 1000, 3))
        elif decision == "VETOED_OPPOSITE" and mark_fresh(m):
            q = open_sim(cand_id, "Q", m, res["direction"], c["strategy"], MARK[m][0], now * 1000)
            rec["q_entry"] = q["entry"]
        append(P["cands"], rec)
        persist()
    log(f"VERDICT {c['strategy']} {c['wy_direction']} {m}: qwen={res.get('direction') or res.get('kind')} "
        f"conf={res.get('confidence')} -> {decision} (b={rec['b_status']}, {rec['decision_latency_s']}s)")


def worker():
    while True:
        cand_id = JOBS.get()
        try:
            with LOCK:
                c = PENDING.get(cand_id)
            if not c:
                continue
            CNT["qwen_calls"] += 1
            uni = snapshot_universe(c["bar_ms"])
            tp, sl = ((W["rv_tp_pct"], W["rv_sl_pct"]) if c["strategy"] == "WY-REVERT" else (W["pb_tp_pct"], W["pb_sl_pct"]))
            evidence, meta = QA.build_evidence(c["market"], c["bar_ms"], c["price_at_decision"], uni, tp, sl, W["timeout_bars"],
                                               dict(taker=FEES["taker_bps"], maker=FEES["maker_bps"], spread=FEES["spread_bps"]))
            res = QA.ask_until(evidence, QC, c["deadline"])
            CNT["qwen_ok" if res.get("status") == "OK" else "qwen_errors"] += 1
            finalize(cand_id, res, evidence, meta)
        except Exception as e:                               # fail CLOSED: a bug is a recorded non-answer
            traceback.print_exc()
            CNT["qwen_errors"] += 1
            finalize(cand_id, dict(status="AGENT_ERROR", kind="worker_exception", error=f"{type(e).__name__}: {e}"[:300]))


# ----------------------------------------------------------------- websocket (own PUBLIC feed)
def ws_loop():
    import websocket
    backoff = 1
    topics = []
    for m in MARKETS:
        topics += [f"tickers.{BYBIT[m]}", f"kline.5.{BYBIT[m]}"]
    while True:
        try:
            ws = websocket.create_connection(WS_URL, timeout=20)
            for i in range(0, len(topics), 10):
                ws.send(json.dumps({"op": "subscribe", "args": topics[i:i + 10]}))
            WS_OK["connects"] += 1
            if WS_OK["connects"] > 1:
                WS_OK["reconnects"] += 1
            WS_OK.update(connected=True, last_error=None)
            backoff = 1
            log("bybit public ws connected,", len(topics), "topics")
            ws.settimeout(15)
            last_ping = time.time()
            while True:
                if time.time() - last_ping > 20:
                    ws.send(json.dumps({"op": "ping"}))
                    last_ping = time.time()
                if WS_OK["last_msg"] and time.time() - WS_OK["last_msg"] > 60:
                    raise RuntimeError("feed stale >60s, forcing reconnect")
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                now = time.time()
                WS_OK["last_msg"] = now
                WS_OK["msgs"] += 1
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                topic = msg.get("topic")
                if not topic:
                    continue
                if msg.get("ts"):
                    WS_OK["last_exch_ts"] = msg["ts"]
                    WS_OK["latency_ms"] = round(now * 1000 - msg["ts"], 1)
                data = msg.get("data")
                if topic.startswith("tickers."):
                    m = FROM_BYBIT.get(topic.split(".", 1)[1])
                    if not m or not isinstance(data, dict):
                        continue
                    if data.get("fundingRate") not in (None, ""):
                        FUND_LAST[m] = float(data["fundingRate"])
                    mp = data.get("markPrice")
                    if mp is not None:
                        p = float(mp)
                        changed = MARK[m] is None or MARK[m][0] != p
                        MARK[m] = (p, now)
                        if changed:
                            on_mark(m, p)
                    elif MARK[m] is not None:
                        MARK[m] = (MARK[m][0], now)
                elif topic.startswith("kline."):
                    m = FROM_BYBIT.get(topic.split(".")[2])
                    if not m or not isinstance(data, list):
                        continue
                    for k in data:
                        bar = [int(k["start"]), float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"])]
                        cur = CURBAR[m]
                        if cur is not None and bar[0] > cur[0]:
                            if CANDLES[m] and cur[0] - CANDLES[m][-1][0] > BAR_MS:
                                fill_gap(m, cur[0])            # feed dropped bars: refill from public REST
                            if not CANDLES[m] or CANDLES[m][-1][0] < cur[0]:
                                CANDLES[m].append(cur)
                                if len(CANDLES[m]) > 600:
                                    CANDLES[m] = CANDLES[m][-500:]
                                on_bar_close(m)
                        CURBAR[m] = bar
        except Exception as e:
            WS_OK["connected"] = False
            WS_OK["last_error"] = repr(e)[:200]
            log("ws error:", repr(e))
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ----------------------------------------------------------------- housekeeping
def snapshot_loop():
    while True:
        try:
            with LOCK:
                now = time.time()
                open_rows = []
                for t in TRADES.values():
                    mk = MARK[t["market"]][0] if MARK[t["market"]] else None
                    up = None
                    if mk:
                        d = (mk - t["entry"]) / t["entry"] if t["side"] == "long" else (t["entry"] - mk) / t["entry"]
                        up = round(t["notional"] * d - t["fee_in"], 3)
                    open_rows.append(dict(id=t["id"], book=t["book"], market=t["market"], side=t["side"], strategy=t["strategy"],
                                          entry=t["entry"], target=t["target"], stop=t["stop"], bars_held=t["bars_held"], upnl_usd=up))
                marks = [v[1] for v in MARK.values() if v]
                snap = dict(ts=int(now * 1000), mode="SHADOW", orders_possible=False, counters=dict(CNT),
                            uptime_s=int(now - STARTED), open=open_rows, pending=list(PENDING),
                            last_eval=LAST_EVAL,
                            feed=dict(connected=WS_OK["connected"], url=WS_URL, last_msg_age=round(now - WS_OK["last_msg"], 1) if WS_OK["last_msg"] else None,
                                      latency_ms=WS_OK["latency_ms"], reconnects=WS_OK["reconnects"], connects=WS_OK["connects"],
                                      markets_fresh=sum(1 for t in marks if now - t < MARK_MAX_AGE_S), markets_total=len(MARKETS),
                                      last_error=WS_OK["last_error"]),
                            bars={m: (CANDLES[m][-1][0] if CANDLES[m] else None) for m in MARKETS},
                            model=QC["model"], sizing=SIZ["source"], clip_usd=SIZ["clip_usd"], max_open=SIZ["max_open"])
            atomic_write(P["snap"], snap)
        except Exception:
            traceback.print_exc()
        time.sleep(5)


def metrics_loop():
    while True:
        try:
            atomic_write(P["metrics"], REP.compute(STATE))
        except Exception:
            traceback.print_exc()
        time.sleep(60)


def load_state():
    global SEEN
    try:
        d = json.load(open(P["pos"]))
        TRADES.update(d.get("trades") or {})
        LAST_BAR.update(d.get("last_bar_ms") or {})
        lost = list((d.get("pending") or {}).values())
    except Exception:
        lost = []
    for r in REP._jl(P["cands"]):
        if r.get("cand_id"):
            SEEN.add(r["cand_id"])
    for c in lost:                                            # verdict never landed before the restart: NON-ANSWER, no approval
        SEEN.add(c["cand_id"])
        PENDING[c["cand_id"]] = c
        finalize(c["cand_id"], dict(status="AGENT_ERROR", kind="restart_lost", error="process restarted before the Qwen verdict"))
    log(f"state loaded: {len(TRADES)} open sim trades, {len(SEEN)} candidates seen, {len(lost)} in-flight resolved as restart_lost")


def catch_up():
    for m in MARKETS:
        last = LAST_BAR.get(m)
        if not last or not CANDLES[m]:
            continue
        missed = [b for b in CANDLES[m] if b[0] > last]
        if missed:
            event("RESTART_GAP", market=m, bars=len(missed), from_ms=missed[0][0], to_ms=missed[-1][0])
        for b in missed:
            replay_bar(m, b)
        LAST_BAR[m] = CANDLES[m][-1][0]
    persist()


def main_run():
    log("WHY x Bybit QWEN SHADOW starting — SHADOW ONLY, no orders, no exchange keys")
    log(f"policy: {CFG['policy']['name']} | model {QC['model']} | sizing clip ${SIZ['clip_usd']} max_open {SIZ['max_open']} ({SIZ['source']})")
    load_fund_intervals()
    seed_candles()
    load_state()
    catch_up()
    event("SERVICE_START", model=QC["model"], pid=os.getpid())

    def _term(*_):
        try:
            persist()
            event("SERVICE_STOP", pid=os.getpid())
        finally:
            os._exit(0)
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    for fn in (worker, snapshot_loop, metrics_loop):
        threading.Thread(target=fn, daemon=True).start()
    ws_loop()


def main_probe():
    """One REAL Qwen call on current real bars (last closed bar of BTC). It is a
    connectivity/format probe and is written to probe.jsonl ONLY — never to the
    dataset, never traded, never counted."""
    seed_candles()
    m = os.environ.get("PROBE_MARKET", "BTCUSDT-PERP")
    bar_ms = CANDLES[m][-1][0]
    mark = float(http_json(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={BYBIT[m]}")["result"]["list"][0]["markPrice"])
    ev, meta = QA.build_evidence(m, bar_ms, mark, CANDLES, W["pb_tp_pct"], W["pb_sl_pct"], W["timeout_bars"],
                                 dict(taker=FEES["taker_bps"], maker=FEES["maker_bps"], spread=FEES["spread_bps"]))
    res = QA.ask_until(ev, QC, time.time() + QC["decision_deadline_sec"])
    out = dict(ts=int(time.time() * 1000), kind="PROBE_NOT_A_CANDIDATE", market=m, evidence=ev, evidence_meta=meta, result=res)
    append(P["probe"], out)
    print(ev, "\n\n--- QWEN RESULT ---")
    print(json.dumps({k: v for k, v in res.items() if k != "qwen_raw"}, indent=1))
    return 0 if res.get("status") == "OK" else 2


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        main_run()
    elif cmd == "report":
        m = REP.compute(STATE)
        print(json.dumps(m, indent=1) if "--json" in sys.argv else REP.render(m))
    elif cmd == "probe":
        raise SystemExit(main_probe())
    else:
        print(__doc__)
        raise SystemExit(2)
