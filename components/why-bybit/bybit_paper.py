#!/usr/bin/env python3
"""WHY x Bybit — the WHY Yellow strategy book, cloned onto Bybit linear perpetuals.

CLONE of /opt/why-yellow/yellow_paper.py. Trading logic, universe, sleeves,
thresholds, risk limits (clip / leverage / max_open / 1-per-market / 12h timeout)
and paper methodology are UNCHANGED. Only venue-specific parts differ:

  * market data  : Bybit v5 public WS (wss://stream.bybit.com/v5/public/linear)
                   tickers.{SYM} (markPrice) + kline.5.{SYM}, normalized into the
                   SAME MARK / CANDLES structures the engine already consumes
  * fees         : Bybit's own schedule (WB_TAKER_BPS / WB_MAKER_BPS), not Yellow's
                   negotiated VIP5 rate. Every CLOSE also records the PnL the same
                   trade would have made at Yellow's rates (pnl_usd_at_yellow_fees)
                   so the two venues can be compared fee-neutrally.
  * isolation    : own dir/state/port/unit; every record carries venue="bybit"
  * instrumentation added (no logic change): evaluation/signal counters, an
    evals.jsonl per-evaluation record, feed latency/age/reconnect tracking
  * execution seam: WB_MODE=paper (default) = simulated fills, exactly Yellow's
    behaviour. WB_MODE=live routes fills through bybit_exec.LiveExecutor AFTER
    preflight + reconcile pass. NO live order is ever sent in paper mode, and no
    credentials exist on this box yet.

Sleeves (identical to Yellow):
  WY-REVERT   RSI(14) extreme fade, trend-gated (EMA20 vs EMA50)  tp 0.30% / sl 1.60% / timeout 12h
  WY-PULLBACK 2.0-ATR pullback to EMA20 with the trend           tp 0.80% / sl 2.00% / timeout 12h
"""
import json, math, os, threading, time, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
MODE0 = os.environ.get("WB_MODE", "paper")
STATE = os.environ.get("WB_STATE_DIR") or os.path.join(BASE, "state" if MODE0 == "paper" else "state-live")
os.makedirs(STATE, exist_ok=True)
VENUE = "bybit"
JOURNAL = os.path.join(STATE, "journal.jsonl")
EVALS = os.path.join(STATE, "evals.jsonl")
FEES_FILE = os.path.join(STATE, "fees.json")     # written by `bybit_exec.py fees` (authenticated, read-only)
HALT_FLAG = os.path.join(STATE, "halt.flag")
RISK_FILE = os.path.join(BASE, "live_risk.json")  # separate LIVE risk config; only read when WB_MODE=live
POSFILE = os.path.join(STATE, "positions.json")
SNAPSHOT = os.path.join(STATE, "snapshot.json")

MARKETS = ["BTCUSDT-PERP","ETHUSDT-PERP","SOLUSDT-PERP","XRPUSDT-PERP","DOGEUSDT-PERP",
           "BNBUSDT-PERP","LINKUSDT-PERP","SUIUSDT-PERP","TAOUSDT-PERP","HYPEUSDT-PERP"]
# Yellow symbol -> Bybit linear symbol (all 10 verified LinearPerpetual/Trading, USDT-settled)
BYBIT = {m: m.replace("-PERP","") for m in MARKETS}
FROM_BYBIT = {v: k for k, v in BYBIT.items()}
WS_URL = os.environ.get("WB_WS_URL", "wss://stream.bybit.com/v5/public/linear")

def env_f(k, d): return float(os.environ.get(k, d))
def env_i(k, d): return int(os.environ.get(k, d))

CFG = dict(
    equity0    = env_f("WB_EQUITY0", 25000),
    clip_usd   = env_f("WB_CLIP_USD", 2000),
    leverage   = env_f("WB_LEVERAGE", 10),
    taker_bps  = env_f("WB_TAKER_BPS", 5.5),   # Bybit linear VIP0 taker 0.055%
    maker_bps  = env_f("WB_MAKER_BPS", 2.0),   # Bybit linear VIP0 maker 0.020%
    spread_bps = env_f("WB_SPREAD_BPS", 2.0),
    max_open   = env_i("WB_MAX_OPEN", 8),
    timeout_bars = env_i("WB_TIMEOUT_BARS", 144),   # 12h of 5m
    rv_tp = env_f("WB_RV_TP_PCT", 0.30), rv_sl = env_f("WB_RV_SL_PCT", 1.60),
    rv_rsi_lo = env_f("WB_RV_RSI_LO", 30), rv_rsi_hi = env_f("WB_RV_RSI_HI", 70),
    pb_tp = env_f("WB_PB_TP_PCT", 0.80), pb_sl = env_f("WB_PB_SL_PCT", 2.00),
    pb_depth = env_f("WB_PB_DEPTH_ATR", 2.0),
    http_port = env_i("WB_HTTP_PORT", 8103),
    mode      = os.environ.get("WB_MODE", "paper"),   # paper | live
    ref_taker_bps = env_f("WB_REF_TAKER_BPS", 1.5),   # Yellow VIP5 rates, reference only
    ref_maker_bps = env_f("WB_REF_MAKER_BPS", 0.0),
)
LIVE_RISK = {}
if CFG["mode"] == "live":                 # $100 bankroll config; missing/invalid file => live refuses to start
    LIVE_RISK = json.load(open(RISK_FILE))
    CFG.update(equity0=float(LIVE_RISK["allocation_usd"]), clip_usd=float(LIVE_RISK["clip_usd"]),
               leverage=float(LIVE_RISK["leverage"]), max_open=int(LIVE_RISK["max_open"]))

LOCK = threading.RLock()
CANDLES = {m: [] for m in MARKETS}     # closed bars: [ms,o,h,l,c]
CURBAR = {m: None for m in MARKETS}    # forming bar
MARK = {m: None for m in MARKETS}      # (price, ts)
POS = {}                               # market -> position dict
def _row(): return dict(trades=0, wins=0, net_usd=0.0, gross_usd=0.0, fees_usd=0.0, funding_usd=0.0, ref_net_usd=0.0)
STATS = dict(_row(), by_strat={})
CLOSES = []                            # (ts_ms, net_usd) — kill-switch accounting
EQUITY = CFG["equity0"]
STARTED = time.time()
WS_OK = {"connected": False, "last_msg": 0, "last_exch_ts": 0, "latency_ms": None, "reconnects": 0,
         "connects": 0, "last_connect": 0, "last_error": None, "msgs": 0}
STRATS = ["WY-REVERT", "WY-PULLBACK"]
CNT = dict(evals=0, bars_evaluated=0, signals=0, skipped_open=0, skipped_capacity=0,
           by_strat={s: dict(evals=0, signals=0) for s in STRATS}, last_eval_ms=None,
           since_ms=int(time.time()*1000))
EXEC = None                            # LiveExecutor when WB_MODE=live, else None (paper)

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, path)

def journal_append(rec):
    rec = dict(rec, venue=VENUE)          # every record carries its venue
    with open(JOURNAL, "a") as f: f.write(json.dumps(rec) + "\n")

def evals_append(rec):
    with open(EVALS, "a") as f: f.write(json.dumps(dict(rec, venue=VENUE)) + "\n")

# ---------- indicators ----------
def ema_last(vals, n):
    k = 2/(n+1); e = vals[0]
    for v in vals: e = v*k + e*(1-k)
    return e

def rsi_last(closes, n=14):
    g = l = 0.0
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        gain, loss = max(d,0), max(-d,0)
        if i <= n: g += gain/n; l += loss/n
        else: g = (g*(n-1)+gain)/n; l = (l*(n-1)+loss)/n
    return 100.0 if l == 0 else 100-100/(1+g/l)

def atr_last(bars, n=14):
    a = bars[0][2]-bars[0][3]
    for i in range(1, len(bars)):
        pc = bars[i-1][4]
        tr = max(bars[i][2]-bars[i][3], abs(bars[i][2]-pc), abs(bars[i][3]-pc))
        a = (a*(n-1)+tr)/n
    return a

# ---------- persistence ----------
def add_close(r):
    """The ONLY place closed-trade accounting happens (live path and journal replay share it)."""
    global EQUITY
    net = r["pnl_usd"]
    gross = r.get("gross_usd", r.get("notional", 0) * r.get("pnl_pct", 0) / 100)
    fees, fund = r.get("fees_usd", 0.0), r.get("funding_usd", 0.0)
    ref = r.get("pnl_usd_at_yellow_fees", net)
    for t in (STATS, STATS["by_strat"].setdefault(r["strategy"], _row())):
        t["trades"] += 1; t["net_usd"] += net; t["gross_usd"] += gross
        t["fees_usd"] += fees; t["funding_usd"] += fund; t["ref_net_usd"] += ref
        if net > 0: t["wins"] += 1
    CLOSES.append((r["ts"], net))
    EQUITY = CFG["equity0"] + STATS["net_usd"]

# ---------- authenticated fee schedule (shared by paper and live) ----------
FEES = {}                              # bybit symbol -> (maker_bps, taker_bps)
FEES_META = dict(source="env default (NOT authenticated)", vip=None, fetched_ms=None)
def load_fees():
    try:
        d = json.load(open(FEES_FILE))
        FEES.clear(); FEES.update({k: (v["maker_bps"], v["taker_bps"]) for k, v in d["rates"].items()})
        FEES_META.update(source=d.get("source"), vip=d.get("vip_level"), fetched_ms=d.get("fetched_ms"))
    except Exception: pass
def fee_bps(m, maker):
    r = FEES.get(BYBIT[m])
    return (r[0] if maker else r[1]) if r else (CFG["maker_bps"] if maker else CFG["taker_bps"])
def fees_loop():
    while True:
        load_fees(); time.sleep(600)

# ---------- funding (Bybit linear perps: rate x notional at each funding timestamp held across) ----------
FUND_INTERVAL_MS = {}                  # market -> ms (instruments-info.fundingInterval)
FUND_LAST = {}                         # market -> last ticker fundingRate (fallback only)
FUND_CACHE = {}                        # (bybit symbol, ts_ms) -> realized rate
def load_fund_intervals():
    try:
        d = json.load(urllib.request.urlopen("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000", timeout=15))
        by = {x["symbol"]: x for x in d["result"]["list"]}
        for m in MARKETS: FUND_INTERVAL_MS[m] = int(by[BYBIT[m]].get("fundingInterval", 480)) * 60000
    except Exception as e: log("funding interval load failed, defaulting to 8h:", e)
def funding_rate_at(m, ts_ms):
    key = (BYBIT[m], ts_ms)
    if key in FUND_CACHE: return FUND_CACHE[key]
    try:
        u = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={BYBIT[m]}&startTime={ts_ms}&endTime={ts_ms}&limit=1"
        l = json.load(urllib.request.urlopen(u, timeout=4))["result"]["list"]
        if l: FUND_CACHE[key] = float(l[0]["fundingRate"]); return FUND_CACHE[key]
    except Exception: pass
    return FUND_LAST.get(m, 0.0001)     # not yet published / unreachable: last ticker rate, else base 1bp
def funding_cost(m, pos, close_ms):
    """(usd expense, periods). Long pays when rate>0, short receives; negative = income."""
    iv = FUND_INTERVAL_MS.get(m, 8 * 3600 * 1000)
    t = (pos["opened_ms"] // iv + 1) * iv; cost = 0.0; n = 0
    while t <= close_ms:
        r = funding_rate_at(m, t)
        cost += pos["notional"] * r * (1 if pos["side"] == "long" else -1); n += 1; t += iv
    return cost, n

# ---------- LIVE-only risk gate (bankroll accounting; NON-compounding) ----------
def worst_case_loss(notional, sl_pct, m):
    """Planned loss if the stop is hit: stop distance + entry taker + exit taker + slippage allowance, in USD."""
    return notional * (sl_pct / 100 + (2 * fee_bps(m, False) + 2 * CFG["spread_bps"]) / 1e4)

def risk_block(m, strat, sl_pct, notional):
    if CFG["mode"] != "live": return None
    R = LIVE_RISK
    if os.path.exists(HALT_FLAG): return "halt.flag present (latched kill)"
    now = time.time(); day0 = int(now // 86400 * 86400 * 1000)
    day_net = sum(n for t, n in CLOSES if t >= day0)
    if day_net <= -R["daily_loss_kill_usd"]: return f"DAILY LOSS KILL (day net {day_net:+.2f} <= -{R['daily_loss_kill_usd']})"
    cum = peak = CFG["equity0"]
    for _, n in CLOSES: cum += n; peak = max(peak, cum)
    floor = peak - R["max_drawdown_kill_usd"]
    if cum <= floor:
        try: open(HALT_FLAG, "w").write(f"max drawdown kill {time.strftime('%F %T')} equity {cum:.2f} peak {peak:.2f}\n")
        except Exception: pass
        return f"MAX DRAWDOWN KILL latched (equity {cum:.2f} <= floor {floor:.2f})"
    agg = sum(worst_case_loss(p["notional"], p["sl_pct"], mm) for mm, p in POS.items()) + worst_case_loss(notional, sl_pct, m)
    if cum - agg < floor: return f"aggregate planned loss {agg:.2f} would breach drawdown floor {floor:.2f}"
    if sum(p["notional"] for p in POS.values()) + notional > R["max_total_notional_usd"]: return "max total notional"
    return None

def load_state():
    global POS, STATS, EQUITY
    if os.path.exists(POSFILE):
        try: POS = json.load(open(POSFILE))
        except Exception: POS = {}
    if os.path.exists(JOURNAL):
        for line in open(JOURNAL):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("event") == "CLOSE" and r.get("mode", "paper") == CFG["mode"]:
                add_close(r)
    EQUITY = CFG["equity0"] + STATS["net_usd"]
    try:                                   # keep evaluation/signal totals across restarts
        old = json.load(open(SNAPSHOT)).get("counters") or {}
        for k in ("evals", "bars_evaluated", "signals", "skipped_open", "skipped_capacity", "last_eval_ms", "since_ms"):
            if old.get(k) is not None: CNT[k] = old[k]
        for sname, v in (old.get("by_strat") or {}).items():
            if sname in CNT["by_strat"]: CNT["by_strat"][sname].update(v)
        WS_OK["reconnects"] = int((json.load(open(SNAPSHOT)).get("feed") or {}).get("reconnects", 0))
    except Exception: pass
    log(f"state loaded: {STATS['trades']} closed, {len(POS)} open, equity {EQUITY:.2f}")

def seed_candles():
    for m in MARKETS:
        sym = BYBIT[m]
        try:
            url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=5&limit=400"
            d = json.load(urllib.request.urlopen(url, timeout=15))
            rows = d.get("result", {}).get("list", [])
            rows.sort(key=lambda r: int(r[0]))
            CANDLES[m] = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows[:-1]]
            log(f"seeded {m}: {len(CANDLES[m])} bars")
        except Exception as e:
            log(f"seed {m} FAILED: {e}")
        time.sleep(0.12)

# ---------- trading ----------
def open_position(m, side, strat, price, detail):
    global POS
    bar_ms = detail.pop("bar_ms", None)      # signal identity for duplicate-order protection; not shown on the page
    slip = price * CFG["spread_bps"]/1e4
    entry = price + slip if side == "long" else price - slip
    tp_pct, sl_pct = (CFG["rv_tp"], CFG["rv_sl"]) if strat == "WY-REVERT" else (CFG["pb_tp"], CFG["pb_sl"])
    fee_in = CFG["clip_usd"] * fee_bps(m, False)/1e4
    CNT["signals"] += 1; CNT["by_strat"][strat]["signals"] += 1        # a signal = the strategy fired
    why = risk_block(m, strat, sl_pct, CFG["clip_usd"])
    if why:
        CNT["blocked"] = CNT.get("blocked", 0) + 1
        journal_append(dict(event="BLOCKED", ts=int(time.time()*1000), market=m, side=side, strategy=strat, reason=why, mode=CFG["mode"]))
        log(f"ENTRY BLOCKED {strat} {side} {m}: {why}"); return
    live_fill = None
    if EXEC is not None:                   # LIVE: the exchange fill replaces the simulated one
        stop_px = entry*(1-sl_pct/100) if side == "long" else entry*(1+sl_pct/100)
        try:
            live_fill = EXEC.open(m, side, strat, CFG["clip_usd"], stop_px, CFG["leverage"],
                                  bar_ms, MARK[m][1] if MARK[m] else None,
                                  risk=dict(sl_pct=sl_pct, max_loss_usd=LIVE_RISK["per_trade_max_loss_usd"],
                                            fee_bps_rt=2*fee_bps(m, False), slip_bps_rt=2*CFG["spread_bps"]))
        except Exception as e:
            log(f"LIVE OPEN REFUSED {strat} {side} {m}: {e!r}"); return
        entry = live_fill["avg_px"]; fee_in = live_fill["fee_usd"]
    pos = dict(market=m, side=side, strategy=strat, entry=entry, opened_ms=int(time.time()*1000),
               bars_held=0, tp_pct=tp_pct, sl_pct=sl_pct, notional=CFG["clip_usd"],
               fee_in=fee_in, detail=detail, hold_to_price_exit=True)   # 09-19: no time-based market exit; only positions opened with this flag skip TIMEOUT (older open lots keep the legacy timeout)
    if side == "long":
        pos["target"] = entry*(1+tp_pct/100); pos["stop"] = entry*(1-sl_pct/100)
    else:
        pos["target"] = entry*(1-tp_pct/100); pos["stop"] = entry*(1+sl_pct/100)
    if live_fill: pos["live"] = live_fill; pos["notional"] = live_fill["notional"]
    if live_fill:                          # resting reduce-only maker TP at the target; a failure never voids the trade (venue stop + engine watch remain)
        try: log(f"resting TP {m}: {EXEC.place_tp(m, pos)}")
        except Exception as e: log(f"resting TP {m} NOT placed: {e!r} (venue stop + engine target watch remain)")
    POS[m] = pos
    atomic_write(POSFILE, POS)
    journal_append(dict(event="OPEN", ts=pos["opened_ms"], venue_symbol=BYBIT[m], mode=CFG["mode"],
                        **{k: pos[k] for k in ("market","side","strategy","entry","target","stop","notional","detail")}))
    log(f"OPEN {strat} {side} {m} @ {entry:.6g} tp {pos['target']:.6g} sl {pos['stop']:.6g}")

def close_position(m, exit_price, reason):
    pos = POS.pop(m, None)
    if not pos: return
    slip = exit_price * CFG["spread_bps"]/1e4
    px = exit_price - slip if pos["side"] == "long" else exit_price + slip
    fee_out = pos["notional"] * fee_bps(m, reason == "TARGET")/1e4     # maker on TARGET, taker otherwise
    exch_fund = None; exit_info = None; fee_repaired = False
    if EXEC is not None and pos.get("live"):       # LIVE: exchange fill + fees are authoritative
        lv_ = pos["live"]
        if not pos.get("fee_in") and not lv_.get("fee_repair_tried"):      # entry fee was never captured at fill time: take it from the exchange before booking PnL
            lv_["fee_repair_tried"] = True
            fx = EXEC.entry_fee(m, pos)
            if fx: log(f"entry fee repaired {m}: booked $0 -> exchange ${fx:.6f}"); pos["fee_in"] = lv_["fee_usd"] = fx; fee_repaired = True
            else: log(f"WARNING {m}: entry fee is 0 and could not be verified from the exchange")
        try: lf = EXEC.close(m, pos, reason)
        except Exception as e:
            POS[m] = pos
            if type(e).__name__ in ("TPPending", "ExitPending"): return   # resting TP working / closing order not visible yet; retried on the next mark or bar
            log(f"LIVE CLOSE FAILED {m} {reason}: {e!r} — position kept, will retry"); return
        px, fee_out, exch_fund = lf["avg_px"], lf["fee_usd"], lf.get("funding_usd")
        exit_info = lf.get("exit_info"); reason = lf.get("exit_reason") or reason     # the exchange order that really closed it decides the label
    if pos["side"] == "long": pnl_pct = (px - pos["entry"]) / pos["entry"] * 100
    else: pnl_pct = (pos["entry"] - px) / pos["entry"] * 100
    now = int(time.time()*1000)
    gross = pos["notional"] * pnl_pct/100
    fees = pos["fee_in"] + fee_out
    fund, fund_n = funding_cost(m, pos, now)
    if exch_fund is not None: fund = exch_fund       # exchange ledger funding beats the model whenever we have it
    net = gross - fees - fund
    ref_fees = pos["notional"] * (CFG["ref_taker_bps"] + (CFG["ref_maker_bps"] if reason == "TARGET" else CFG["ref_taker_bps"]))/1e4
    ref_net = gross - ref_fees - fund                # SAME trade + SAME funding, Yellow's fee rates
    rec = dict(event="CLOSE", ts=now, market=m, side=pos["side"], strategy=pos["strategy"],
               entry=pos["entry"], exit=px, reason=reason, pnl_pct=round(pnl_pct,4),
               gross_usd=round(gross,4), fee_in_usd=round(pos["fee_in"],4), fee_out_usd=round(fee_out,4),
               fees_usd=round(fees,4), funding_usd=round(fund,4), funding_periods=fund_n,
               pnl_usd=round(net,4), pnl_usd_at_yellow_fees=round(ref_net,4),
               fee_source=FEES_META.get("source"), venue_symbol=BYBIT[m], mode=CFG["mode"],
               hold_min=round((now-pos["opened_ms"])/60000,1), notional=pos["notional"],
               detail=pos.get("detail",{}))
    if exch_fund is not None: rec["funding_usd_exchange"] = round(exch_fund, 6)
    if exit_info: rec.update(exit_kind=exit_info.get("kind"), exit_order_id=exit_info.get("order_id"), exit_link=exit_info.get("link"),
                             exit_create_type=exit_info.get("create_type"))
    if fee_repaired: rec["fee_in_repaired"] = True
    with LOCK: add_close(rec)
    atomic_write(POSFILE, POS)
    journal_append(rec)
    log(f"CLOSE {pos['strategy']} {pos['side']} {m} @ {px:.6g} {reason} gross ${gross:+.3f} fees ${fees:.3f} fund ${fund:+.3f} NET ${net:+.3f}")

def on_mark(m, price):
    pos = POS.get(m)
    if not pos: return
    if pos["side"] == "long":
        if price <= pos["stop"]: close_position(m, pos["stop"], "STOP")
        elif price >= pos["target"]: close_position(m, pos["target"], "TARGET")
    else:
        if price >= pos["stop"]: close_position(m, pos["stop"], "STOP")
        elif price <= pos["target"]: close_position(m, pos["target"], "TARGET")

def on_bar_close(m):
    bars = CANDLES[m]
    if len(bars) < 250: return
    pos = POS.get(m)
    if pos:
        pos["bars_held"] += 1
        try: atomic_write(POSFILE, POS)              # persist the hold clock every bar so a restart never loses timeout time
        except Exception as e: log(f"bars_held persist failed {m}: {e!r}")
        if EXEC is not None and pos.get("live"):     # exits done on the exchange (resting TP / venue stop) that the mark feed may not have shown
            try:
                why = EXEC.gone_reason(m, pos)
                if why: close_position(m, pos["target"] if why == "TARGET" else pos["stop"], why); return
            except Exception as e: log(f"exit sync {m}: {e!r}")
        if pos["bars_held"] >= CFG["timeout_bars"] and MARK[m]:
            if not pos.get("hold_to_price_exit"): close_position(m, MARK[m][0], "TIMEOUT")
            elif not pos.get("timeout_logged"):      # diagnostic only: TP / SL stay the sole exits
                pos["timeout_logged"] = True; log(f"{m} reached {pos['bars_held']} bars: no time exit, holding to TP/SL")
        CNT["skipped_open"] += 1
        return
    if len(POS) >= CFG["max_open"]:
        CNT["skipped_capacity"] += 1; return
    closes = [b[4] for b in bars[-250:]]
    c = closes[-1]
    e20 = ema_last(closes, 20); e50 = ema_last(closes, 50)
    r = rsi_last(closes[-60:]); a = atr_last(bars[-60:])
    up = e20 > e50
    px = MARK[m][0] if MARK[m] else c
    bar_ms = bars[-1][0]
    # evaluation bookkeeping: one closed bar x one market = one evaluation of EACH sleeve
    CNT["bars_evaluated"] += 1; CNT["evals"] += len(STRATS); CNT["last_eval_ms"] = int(time.time()*1000)
    for sname in STRATS: CNT["by_strat"][sname]["evals"] += 1
    depth = (e20 - c)/a if up else (c - e20)/a
    fired = None
    if r < CFG["rv_rsi_lo"] and up:
        fired = ("long", "WY-REVERT"); det = dict(rsi=round(r,1), trend="up")
    elif r > CFG["rv_rsi_hi"] and not up:
        fired = ("short", "WY-REVERT"); det = dict(rsi=round(r,1), trend="down")
    elif up and c < e20 - CFG["pb_depth"]*a:
        fired = ("long", "WY-PULLBACK"); det = dict(depth_atr=round((e20-c)/a,2), trend="up")
    elif (not up) and c > e20 + CFG["pb_depth"]*a:
        fired = ("short", "WY-PULLBACK"); det = dict(depth_atr=round((c-e20)/a,2), trend="down")
    evals_append(dict(ts=CNT["last_eval_ms"], market=m, venue_symbol=BYBIT[m], bar_ms=bar_ms, close=c,
                      rsi=round(r,1), depth_atr=round(depth,2), trend="up" if up else "down",
                      signal=(f"{fired[1]}:{fired[0]}" if fired else None)))
    if fired:
        det["bar_ms"] = bar_ms
        open_position(m, fired[0], fired[1], px, det)

# ---------- websocket feed (Bybit v5 public linear) ----------
# Normalizes Bybit messages into the exact structures the (unchanged) engine reads:
#   MARK[m]    = (mark_price, recv_ts)          <- tickers.{SYM}.markPrice
#   CURBAR[m]  = [start_ms, o, h, l, c]         <- kline.5.{SYM}; a new start rolls the bar
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
                ws.send(json.dumps({"op": "subscribe", "args": topics[i:i+10]}))
            WS_OK["connects"] += 1; WS_OK["last_connect"] = time.time()
            if WS_OK["connects"] > 1: WS_OK["reconnects"] += 1
            WS_OK["connected"] = True; WS_OK["last_error"] = None; backoff = 1
            log("bybit ws connected, subscribed", len(topics), "topics")
            ws.settimeout(15)
            last_ping = time.time()
            while True:
                if time.time() - last_ping > 20:
                    ws.send(json.dumps({"op": "ping"})); last_ping = time.time()
                if WS_OK["last_msg"] and time.time() - WS_OK["last_msg"] > 60:
                    raise RuntimeError("feed stale >60s, forcing reconnect")
                try: raw = ws.recv()
                except websocket.WebSocketTimeoutException: continue
                now = time.time()
                WS_OK["last_msg"] = now; WS_OK["msgs"] += 1
                try: msg = json.loads(raw)
                except Exception: continue
                topic = msg.get("topic")
                if not topic: continue              # subscribe acks / pongs
                if msg.get("ts"):
                    WS_OK["last_exch_ts"] = msg["ts"]; WS_OK["latency_ms"] = round(now*1000 - msg["ts"], 1)
                data = msg.get("data")
                if topic.startswith("tickers."):
                    sym = topic.split(".", 1)[1]; m = FROM_BYBIT.get(sym)
                    if not m or not isinstance(data, dict): continue
                    if data.get("fundingRate") not in (None, ""): FUND_LAST[m] = float(data["fundingRate"])
                    mp = data.get("markPrice")      # deltas only carry changed fields
                    if mp is not None:
                        p = float(mp); changed = (MARK[m] is None or MARK[m][0] != p)
                        MARK[m] = (p, now)
                        if changed: on_mark(m, p)
                    elif MARK[m] is not None:
                        MARK[m] = (MARK[m][0], now)  # ticker alive, mark unchanged -> still fresh
                elif topic.startswith("kline."):
                    sym = topic.split(".")[2]; m = FROM_BYBIT.get(sym)
                    if not m or not isinstance(data, list): continue
                    for k in data:
                        bar = [int(k["start"]), float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"])]
                        cur = CURBAR[m]
                        if cur is not None and bar[0] > cur[0]:
                            CANDLES[m].append(cur)
                            if len(CANDLES[m]) > 600: CANDLES[m] = CANDLES[m][-500:]
                            on_bar_close(m)
                        CURBAR[m] = bar
        except Exception as e:
            WS_OK["connected"] = False; WS_OK["last_error"] = repr(e)[:200]
            log("ws error:", repr(e))
            time.sleep(backoff)
            backoff = min(backoff*2, 60)

# ---------- snapshot ----------
def snapshot_loop():
    while True:
        try:
            with LOCK:
                open_rows = []
                for m, p in POS.items():
                    mark = MARK[m][0] if MARK[m] else None
                    upnl = None
                    if mark:
                        d = (mark-p["entry"])/p["entry"] if p["side"]=="long" else (p["entry"]-mark)/p["entry"]
                        upnl = round(p["notional"]*d - p["fee_in"], 2)
                    open_rows.append(dict(market=m, side=p["side"], strategy=p["strategy"],
                        entry=p["entry"], target=p["target"], stop=p["stop"], mark=mark,
                        upnl_usd=upnl, opened_ms=p["opened_ms"], notional=p["notional"],
                        detail=p.get("detail",{})))
                now_s = time.time()
                marks_ts = [v[1] for v in MARK.values() if v]
                unreal = sum(r["upnl_usd"] for r in open_rows if r["upnl_usd"] is not None)
                cap = dict(CAPITAL) if CAPITAL.get("ts") and time.time()*1000 - CAPITAL["ts"] < 180000 else None
                capital = None
                if cap:
                    capital = dict(funded_usd=cap.get("funded_usd"), usdt_wallet=round(cap["usdt_wallet"], 4), usdt_equity=round(cap["usdt_equity"], 4),
                                   usdt_upnl=round(cap["usdt_upnl"], 4), total_equity=round(cap["total_equity"], 4),
                                   pnl_vs_funded=round(cap["usdt_equity"] - cap["funded_usd"], 4) if cap.get("funded_usd") else None,
                                   bankroll_config_usd=CFG["equity0"], age_s=round(time.time() - cap["ts"]/1000, 1))
                snap = dict(venue=VENUE, mode=CFG["mode"], ts=int(time.time()*1000), equity=round(cap["usdt_equity"] if cap else EQUITY, 2),
                    equity_model=round(EQUITY, 2), capital=capital,
                    equity0=CFG["equity0"], stats=dict(STATS), open=open_rows,
                    win_rate=round(STATS["wins"]/STATS["trades"]*100,1) if STATS["trades"] else None,
                    universe=MARKETS, clip_usd=CFG["clip_usd"], leverage=CFG["leverage"],
                    max_open=CFG["max_open"],
                    fees=dict(taker_bps=fee_bps("BTCUSDT-PERP", False), maker_bps=fee_bps("BTCUSDT-PERP", True),
                              source=FEES_META["source"], vip=FEES_META["vip"], fetched_ms=FEES_META["fetched_ms"],
                              ref_taker_bps=CFG["ref_taker_bps"], ref_maker_bps=CFG["ref_maker_bps"]),
                    risk=dict(mode=CFG["mode"], live=LIVE_RISK or None, halted=os.path.exists(HALT_FLAG),
                              bankroll_usd=CFG["equity0"], equity_usd=round(EQUITY, 4), clip_usd=CFG["clip_usd"],
                              leverage=CFG["leverage"], max_open=CFG["max_open"]),
                    feed=dict(connected=WS_OK["connected"], url=WS_URL,
                              last_msg_age=round(now_s-WS_OK["last_msg"],1) if WS_OK["last_msg"] else None,
                              last_msg_ms=int(WS_OK["last_msg"]*1000) if WS_OK["last_msg"] else None,
                              last_exchange_ts=WS_OK["last_exch_ts"] or None, latency_ms=WS_OK["latency_ms"],
                              reconnects=WS_OK["reconnects"], connects=WS_OK["connects"],
                              markets_fresh=sum(1 for t in marks_ts if now_s - t < 30), markets_total=len(MARKETS),
                              last_error=WS_OK["last_error"]),
                    upnl_usd=round(unreal, 2),
                    counters=dict(CNT), strategies=STRATS,
                    strategies_evaluating=[k for k in STRATS if CNT["by_strat"][k]["evals"] > 0],
                    uptime_s=int(time.time()-STARTED))
            atomic_write(SNAPSHOT, snap)
        except Exception:
            traceback.print_exc()
        time.sleep(5)

# ---------- web ----------
def read_trades(limit=200):
    if not os.path.exists(JOURNAL): return []
    out = []
    with open(JOURNAL) as f:
        for line in f:
            try: r = json.loads(line)
            except Exception: continue
            if r.get("event") == "CLOSE": out.append(r)
    return out[-limit:][::-1]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p in ("/", "/p"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif p in ("/api/book", "/p/api/book"):
            try: snap = json.load(open(SNAPSHOT))
            except Exception: snap = {}
            snap["trades"] = read_trades(120)
            self._send(200, json.dumps(snap))
        elif p == "/health":
            self._send(200, json.dumps(dict(ok=True, venue=VENUE, connected=WS_OK["connected"],
                bars={m: [len(CANDLES[m]), CANDLES[m][-1][0] if CANDLES[m] else None] for m in MARKETS},
                marks={m: (MARK[m][0] if MARK[m] else None) for m in MARKETS})))
        else:
            self._send(404, json.dumps(dict(error="not found")))

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WHYTradingBot &mdash; BYBIT</title>
<style>
:root{--bg:#0b0e11;--panel:#12161c;--line:#1f2630;--txt:#e8edf2;--dim:#8b97a5;
--gold:#f7a600;--green:#2ecc8f;--red:#ff5d6c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;padding:28px 4vw}
h1{font-size:22px;letter-spacing:.4px}
h1 .y{color:var(--gold)}
.sub{color:var(--dim);font-size:12.5px;margin:4px 0 22px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.8px}
.card .v{font-size:24px;font-weight:600;margin-top:4px}
.pos .v,.neg .v{font-variant-numeric:tabular-nums}
.pos .v{color:var(--green)}.neg .v{color:var(--red)}
h2{font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin:26px 0 10px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;font-size:12.5px;white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.6px;border-bottom:1px solid var(--line)}
tr+tr td{border-top:1px solid var(--line)}
td.n{font-variant-numeric:tabular-nums}
.g{color:var(--green)}.r{color:var(--red)}.d{color:var(--dim)}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;border:1px solid var(--line)}
.long{color:var(--green)}.short{color:var(--red)}
.wrap{overflow-x:auto}
.foot{margin-top:30px;color:var(--dim);font-size:11px;line-height:1.6;max-width:860px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
</style></head><body>
<h1>WHYTradingBot &mdash; <span class="y">BYBIT</span></h1>
<div class="sub"><span class="dot" id="dot" style="background:#555"></span>
The WHY strategy book run on Bybit linear perpetuals (same system, same universe as the Yellow book) &middot; <span id="upd" class="d"></span></div>
<h2>Bybit Connection &amp; Engine</h2>
<div class="stats" id="status"></div>
<h2>Book</h2>
<div class="stats" id="stats"></div>
<h2>Open Positions</h2><div class="wrap"><table id="open"><thead><tr>
<th>Market</th><th>Side</th><th>Strategy</th><th>Entry</th><th>Mark</th><th>Target</th><th>Stop</th><th>uPnL</th><th>Opened</th></tr></thead><tbody></tbody></table></div>
<h2>Strategy Performance</h2><div class="wrap"><table id="strat"><thead><tr>
<th>Strategy</th><th>Trades</th><th>Win rate</th><th>Net PnL</th></tr></thead><tbody></tbody></table></div>
<h2>Trade History</h2><div class="wrap"><table id="hist"><thead><tr>
<th>Closed</th><th>Market</th><th>Side</th><th>Strategy</th><th>Entry</th><th>Exit</th><th>Exit type</th><th>Gross</th><th>Fees</th><th>Funding</th><th>Net PnL</th><th>Held</th><th>Signal</th></tr></thead><tbody></tbody></table></div>
<div class="foot" id="feeline" style="margin-top:14px"></div>
<div class="foot">Performance is tracked against live Bybit market data with Bybit's standard fee
rates applied; results shown are strategy signal tracking, not exchange account statements,
and past performance does not guarantee future results.</div>
<script>
const $=s=>document.querySelector(s);
const fmt=(x,d=2)=>x==null?'—':Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const px=x=>x==null?'—':Number(x).toLocaleString('en-US',{maximumSignificantDigits:6});
const ago=ms=>{const s=(Date.now()-ms)/1e3;if(s<90)return Math.round(s)+'s ago';if(s<5400)return Math.round(s/60)+'m ago';return (s/3600).toFixed(1)+'h ago'};
function card(k,v,cls=''){return `<div class="card ${cls}"><div class="k">${k}</div><div class="v">${v}</div></div>`}
async function tick(){
 try{
  const r=await fetch('/p/api/book',{cache:'no-store'});const b=await r.json();
  $('#dot').style.background=(b.feed&&b.feed.connected)?'var(--green)':'var(--red)';
  $('#upd').textContent='updated '+new Date(b.ts).toLocaleTimeString();
  const f=b.feed||{},c=b.counters||{},up=b.upnl_usd||0,nt=(b.stats||{}).net_usd||0;
  const lastTs=f.last_msg_ms?new Date(f.last_msg_ms).toISOString().replace('T',' ').slice(0,19)+' UTC':'—';
  const age=f.last_msg_age;
  $('#status').innerHTML=
   card('Bybit connection',f.connected?'CONNECTED':'DISCONNECTED',f.connected?'pos':'neg')+
   card('Market data',(f.markets_fresh??0)+' / '+(f.markets_total??10)+' live',(f.markets_fresh>=(f.markets_total||10))?'pos':'neg')+
   card('Latest market timestamp',`<span style="font-size:15px">${lastTs}</span>`)+
   card('Market data age',age==null?'—':fmt(age,1)+' s',age!=null&&age<30?'pos':'neg')+
   card('Feed latency',f.latency_ms==null?'—':fmt(f.latency_ms,0)+' ms')+
   card('Reconnects',f.reconnects??0)+
   card('Evaluations',(c.evals||0).toLocaleString('en-US'))+
   card('Signals',c.signals||0)+
   card('Paper trades',(b.stats||{}).trades||0)+
   card('Open positions',(b.open||[]).length+' / '+(b.max_open||8))+
   card('Realized PnL (net)',(nt>=0?'+$':'-$')+fmt(Math.abs(nt)),nt>=0?'pos':'neg')+
   card('Unrealized PnL',(up>=0?'+$':'-$')+fmt(Math.abs(up)),up>=0?'pos':'neg')+
   card('Strategies active',(b.strategies_evaluating||[]).length+' / '+(b.strategies||[]).length);
  const st=b.stats||{},wr=b.win_rate,net=st.net_usd||0;
  const usd=(x,d=2)=>(x>=0?'+$':'-$')+fmt(Math.abs(x),d), ref=st.ref_net_usd||0, fs=b.fees||{};
  $('#stats').innerHTML=
   card('Win rate',wr==null?'—':fmt(wr,1)+'%',wr>=68?'pos':'')+
   card('NET PnL (after fees &amp; funding)',usd(net),net>=0?'pos':'neg')+
   card('Gross PnL (before costs)',usd(st.gross_usd||0),'')+
   card('Trading fees','-$'+fmt(st.fees_usd||0),'')+
   card('Funding paid','-$'+fmt(st.funding_usd||0,4),'')+
   card('Same trades at Yellow fees',usd(ref),ref>=0?'pos':'neg')+
   card('Fee difference (Bybit vs Yellow)',usd(net-ref),(net-ref)>=0?'pos':'neg')+
   card('Equity','$'+fmt(b.equity))+
   (b.capital&&b.capital.funded_usd!=null?card('Funded capital','$'+fmt(b.capital.funded_usd))+card('Equity vs funded',usd(b.capital.pnl_vs_funded||0),(b.capital.pnl_vs_funded||0)>=0?'pos':'neg'):'')+
   card('Closed trades',st.trades||0)+
   card('Open',(b.open||[]).length+' / '+(b.max_open||8));
  $('#feeline').textContent='Fees applied: maker '+fmt(fs.maker_bps,2)+' bps / taker '+fmt(fs.taker_bps,2)+' bps ('+(fs.source||'?')+(fs.vip?', '+fs.vip:'')+'). Yellow comparison: '+fmt(fs.ref_maker_bps,2)+' / '+fmt(fs.ref_taker_bps,2)+' bps.';
  $('#open tbody').innerHTML=(b.open||[]).map(p=>`<tr>
   <td>${p.market.replace('-PERP','')}</td>
   <td><span class="tag ${p.side}">${p.side.toUpperCase()}</span></td>
   <td>${p.strategy}</td><td class="n">${px(p.entry)}</td><td class="n">${px(p.mark)}</td>
   <td class="n g">${px(p.target)}</td><td class="n r">${px(p.stop)}</td>
   <td class="n ${p.upnl_usd>=0?'g':'r'}">${p.upnl_usd==null?'—':(p.upnl_usd>=0?'+$':'-$')+fmt(Math.abs(p.upnl_usd))}</td>
   <td class="d">${ago(p.opened_ms)}</td></tr>`).join('')||'<tr><td colspan="9" class="d">scanning&hellip;</td></tr>';
  const bs=st.by_strat||{};
  $('#strat tbody').innerHTML=Object.entries(bs).map(([k,s])=>`<tr>
   <td>${k}</td><td class="n">${s.trades}</td>
   <td class="n">${s.trades?fmt(s.wins/s.trades*100,1)+'%':'—'}</td>
   <td class="n ${s.net_usd>=0?'g':'r'}">${(s.net_usd>=0?'+$':'-$')+fmt(Math.abs(s.net_usd))}</td></tr>`).join('')||'<tr><td colspan="4" class="d">no closed trades yet</td></tr>';
  $('#hist tbody').innerHTML=(b.trades||[]).map(t=>`<tr>
   <td class="d">${new Date(t.ts).toLocaleString()}</td>
   <td>${t.market.replace('-PERP','')}</td>
   <td><span class="tag ${t.side}">${t.side.toUpperCase()}</span></td>
   <td>${t.strategy}</td><td class="n">${px(t.entry)}</td><td class="n">${px(t.exit)}</td>
   <td>${t.reason}</td>
   <td class="n d">${t.gross_usd==null?'—':(t.gross_usd>=0?'+$':'-$')+fmt(Math.abs(t.gross_usd),3)}</td>
   <td class="n d">${t.fees_usd==null?'—':'-$'+fmt(t.fees_usd,3)}</td>
   <td class="n d">${t.funding_usd==null?'—':(t.funding_usd>0?'-$':'+$')+fmt(Math.abs(t.funding_usd),4)}</td>
   <td class="n ${t.pnl_usd>=0?'g':'r'}">${(t.pnl_usd>=0?'+$':'-$')+fmt(Math.abs(t.pnl_usd),3)}</td>
   <td class="d">${t.hold_min<90?Math.round(t.hold_min)+'m':(t.hold_min/60).toFixed(1)+'h'}</td>
   <td class="d">${t.detail?Object.entries(t.detail).map(([k,v])=>k+' '+v).join(' · '):''}</td></tr>`).join('')||'<tr><td colspan="13" class="d">no closed trades yet</td></tr>';
 }catch(e){$('#dot').style.background='var(--red)'}
}
tick();setInterval(tick,5000);
</script></body></html>"""

def init_exec():
    """paper -> None. live -> LiveExecutor, only after key/permission preflight AND exchange reconciliation pass."""
    global EXEC
    if CFG["mode"] != "live": return
    import bybit_exec
    ex = bybit_exec.LiveExecutor(universe=BYBIT)
    ex.preflight()                       # raises unless keys exist, ARMED=1, no withdraw perm, one-way mode...
    rep_ = ex.reconcile(POS)             # raises on any mismatch between exchange and local positions
    log("LIVE reconcile:", rep_)
    for m_, p_ in POS.items():            # restored live positions get their resting TP if it is missing
        if p_.get("live"):
            try: log(f"resting TP {m_}: {ex.ensure_tp(m_, p_)} @ {p_['live'].get('tp_px')}")
            except Exception as e: log(f"resting TP {m_} NOT placed: {e!r} (venue stop + engine target watch remain)")
    for m_, p_ in POS.items():            # entry fee booked as 0 (executions lag at fill time): repair from the exchange
        if p_.get("live") and not p_.get("fee_in"):
            fx = ex.entry_fee(m_, p_)
            if fx: log(f"entry fee repaired {m_}: booked $0 -> exchange ${fx:.6f}"); p_["fee_in"] = p_["live"]["fee_usd"] = fx
            else: log(f"WARNING {m_}: entry fee is 0 and could not be verified from the exchange")
    atomic_write(POSFILE, POS)
    EXEC = ex

CAPITAL = {}                             # LIVE: exchange-authoritative USDT wallet/equity + funded amount (read-only)
CAPITAL_FILE = os.path.join(STATE, "capital.json")
def _live_since_ms():
    try:
        for line in open(JOURNAL):
            r = json.loads(line)
            if r.get("event") == "OPEN" and r.get("mode") == "live": return int(r["ts"]) - 6 * 3600 * 1000
    except Exception: pass
    return int(time.time() * 1000) - 24 * 3600 * 1000

def capital_loop():
    """Reporting only: never feeds the risk gate. Own HTTP session, one wallet GET a minute, ledger flows every 10 min."""
    last_flow = 0.0; warned = False
    while True:
        try:
            eq = EXEC.exchange_equity()
            if time.time() - last_flow > 600:
                st = EXEC.sync_flows(CAPITAL_FILE, _live_since_ms()); last_flow = time.time()
                CAPITAL["funded_usd"] = round(sum(f["change"] for f in st["flows"].values()), 6); CAPITAL["flows"] = len(st["flows"])
            CAPITAL.update(eq); CAPITAL.pop("error", None)
            if not warned and CAPITAL.get("funded_usd") and abs(CAPITAL["funded_usd"] - CFG["equity0"]) > 1.0:
                warned = True
                log(f"NOTE risk bankroll ${CFG['equity0']:.2f} (live_risk.json) != funded ${CAPITAL['funded_usd']:.2f} (exchange ledger); kill floors use the bankroll figure")
        except Exception as e:
            CAPITAL["error"] = repr(e)[:200]; log(f"capital sync failed: {e!r}")
        time.sleep(60)

def main():
    load_state()
    load_fees(); load_fund_intervals()
    init_exec()
    seed_candles()
    if EXEC is not None: threading.Thread(target=capital_loop, daemon=True).start()
    threading.Thread(target=fees_loop, daemon=True).start()
    threading.Thread(target=ws_loop, daemon=True).start()
    threading.Thread(target=snapshot_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", CFG["http_port"]), H)
    log(f"http on 127.0.0.1:{CFG['http_port']}")
    srv.serve_forever()

if __name__ == "__main__":
    main()
