import re
p='/opt/why-bybit/bybit_paper.py'; s=open(p).read()
def rep(old,new,cnt=1):
    global s
    assert s.count(old)==cnt, f"count={s.count(old)} (want {cnt}) for: "+old[:90]
    s=s.replace(old,new)

# ---- mode-specific state dir (paper and live records can never mix)
rep('STATE = os.path.join(BASE, "state")',
    'MODE0 = os.environ.get("WB_MODE", "paper")\nSTATE = os.environ.get("WB_STATE_DIR") or os.path.join(BASE, "state" if MODE0 == "paper" else "state-live")')
rep('EVALS = os.path.join(STATE, "evals.jsonl")',
    'EVALS = os.path.join(STATE, "evals.jsonl")\nFEES_FILE = os.path.join(STATE, "fees.json")     # written by `bybit_exec.py fees` (authenticated, read-only)\nHALT_FLAG = os.path.join(STATE, "halt.flag")\nRISK_FILE = os.path.join(BASE, "live_risk.json")  # separate LIVE risk config; only read when WB_MODE=live')

# ---- live risk overlay (Yellow-scale paper config is untouched)
rep('''    ref_maker_bps = env_f("WB_REF_MAKER_BPS", 0.0),
)''','''    ref_maker_bps = env_f("WB_REF_MAKER_BPS", 0.0),
)
LIVE_RISK = {}
if CFG["mode"] == "live":                 # $100 bankroll config; missing/invalid file => live refuses to start
    LIVE_RISK = json.load(open(RISK_FILE))
    CFG.update(equity0=float(LIVE_RISK["allocation_usd"]), clip_usd=float(LIVE_RISK["clip_usd"]),
               leverage=float(LIVE_RISK["leverage"]), max_open=int(LIVE_RISK["max_open"]))''')

# ---- STATS with full net-PnL decomposition
rep('STATS = dict(trades=0, wins=0, net_usd=0.0, fees_usd=0.0, by_strat={})',
'''def _row(): return dict(trades=0, wins=0, net_usd=0.0, gross_usd=0.0, fees_usd=0.0, funding_usd=0.0, ref_net_usd=0.0)
STATS = dict(_row(), by_strat={})
CLOSES = []                            # (ts_ms, net_usd) — kill-switch accounting''')

# ---- load_state via the single add_close() path; only records of THIS mode count
a = s.index('    if os.path.exists(JOURNAL):\n        for line in open(JOURNAL):'); b = s.index('    EQUITY = CFG["equity0"] + STATS["net_usd"]\n    try:')
s = s[:a] + '''    if os.path.exists(JOURNAL):
        for line in open(JOURNAL):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("event") == "CLOSE" and r.get("mode", "paper") == CFG["mode"]:
                add_close(r)
''' + s[b:]

# ---- helpers inserted before load_state
rep('def load_state():','''def add_close(r):
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
        try: open(HALT_FLAG, "w").write(f"max drawdown kill {time.strftime('%F %T')} equity {cum:.2f} peak {peak:.2f}\\n")
        except Exception: pass
        return f"MAX DRAWDOWN KILL latched (equity {cum:.2f} <= floor {floor:.2f})"
    agg = sum(worst_case_loss(p["notional"], p["sl_pct"], mm) for mm, p in POS.items()) + worst_case_loss(notional, sl_pct, m)
    if cum - agg < floor: return f"aggregate planned loss {agg:.2f} would breach drawdown floor {floor:.2f}"
    if sum(p["notional"] for p in POS.values()) + notional > R["max_total_notional_usd"]: return "max total notional"
    return None

def load_state():''')

# ---- open_position: fees from schedule, signal count first, live gate
rep('''    fee_in = CFG["clip_usd"] * CFG["taker_bps"]/1e4
    live_fill = None''','''    fee_in = CFG["clip_usd"] * fee_bps(m, False)/1e4
    CNT["signals"] += 1; CNT["by_strat"][strat]["signals"] += 1        # a signal = the strategy fired
    why = risk_block(m, strat, sl_pct, CFG["clip_usd"])
    if why:
        CNT["blocked"] = CNT.get("blocked", 0) + 1
        journal_append(dict(event="BLOCKED", ts=int(time.time()*1000), market=m, side=side, strategy=strat, reason=why, mode=CFG["mode"]))
        log(f"ENTRY BLOCKED {strat} {side} {m}: {why}"); return
    live_fill = None''')
rep('''            live_fill = EXEC.open(m, side, strat, CFG["clip_usd"], stop_px, CFG["leverage"],
                                  bar_ms, MARK[m][1] if MARK[m] else None)''','''            live_fill = EXEC.open(m, side, strat, CFG["clip_usd"], stop_px, CFG["leverage"],
                                  bar_ms, MARK[m][1] if MARK[m] else None,
                                  risk=dict(sl_pct=sl_pct, max_loss_usd=LIVE_RISK["per_trade_max_loss_usd"],
                                            fee_bps_rt=2*fee_bps(m, False), slip_bps_rt=2*CFG["spread_bps"]))''')
rep('''        entry = live_fill["avg_px"]; fee_in = live_fill["fee_usd"]
    CNT["signals"] += 1; CNT["by_strat"][strat]["signals"] += 1
''','''        entry = live_fill["avg_px"]; fee_in = live_fill["fee_usd"]
''')

# ---- close_position: full decomposition
a = s.index('def close_position(m, exit_price, reason):'); b = s.index('def on_mark(m, price):')
s = s[:a] + '''def close_position(m, exit_price, reason):
    pos = POS.pop(m, None)
    if not pos: return
    slip = exit_price * CFG["spread_bps"]/1e4
    px = exit_price - slip if pos["side"] == "long" else exit_price + slip
    fee_out = pos["notional"] * fee_bps(m, reason == "TARGET")/1e4     # maker on TARGET, taker otherwise
    exch_fund = None
    if EXEC is not None and pos.get("live"):       # LIVE: exchange fill + fees are authoritative
        try: lf = EXEC.close(m, pos, reason)
        except Exception as e:
            log(f"LIVE CLOSE FAILED {m} {reason}: {e!r} — position kept, will retry"); POS[m] = pos; return
        px, fee_out, exch_fund = lf["avg_px"], lf["fee_usd"], lf.get("funding_usd")
    if pos["side"] == "long": pnl_pct = (px - pos["entry"]) / pos["entry"] * 100
    else: pnl_pct = (pos["entry"] - px) / pos["entry"] * 100
    now = int(time.time()*1000)
    gross = pos["notional"] * pnl_pct/100
    fees = pos["fee_in"] + fee_out
    fund, fund_n = funding_cost(m, pos, now)
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
    with LOCK: add_close(rec)
    atomic_write(POSFILE, POS)
    journal_append(rec)
    log(f"CLOSE {pos['strategy']} {pos['side']} {m} @ {px:.6g} {reason} gross ${gross:+.3f} fees ${fees:.3f} fund ${fund:+.3f} NET ${net:+.3f}")

''' + s[b:]

# ---- ticker fundingRate fallback capture (2 lines in ws_loop)
rep('''                    mp = data.get("markPrice")      # deltas only carry changed fields''','''                    if data.get("fundingRate") not in (None, ""): FUND_LAST[m] = float(data["fundingRate"])
                    mp = data.get("markPrice")      # deltas only carry changed fields''')

# ---- snapshot: fee source, risk, breakdown are read from STATS/FEES_META
rep('''                    fees=dict(taker_bps=CFG["taker_bps"], maker_bps=CFG["maker_bps"]),''','''                    fees=dict(taker_bps=fee_bps("BTCUSDT-PERP", False), maker_bps=fee_bps("BTCUSDT-PERP", True),
                              source=FEES_META["source"], vip=FEES_META["vip"], fetched_ms=FEES_META["fetched_ms"],
                              ref_taker_bps=CFG["ref_taker_bps"], ref_maker_bps=CFG["ref_maker_bps"]),
                    risk=dict(mode=CFG["mode"], live=LIVE_RISK or None, halted=os.path.exists(HALT_FLAG),
                              bankroll_usd=CFG["equity0"], equity_usd=round(EQUITY, 4), clip_usd=CFG["clip_usd"],
                              leverage=CFG["leverage"], max_open=CFG["max_open"]),''')

# ---- main: load fees, funding intervals, start fees thread
rep('''def main():
    load_state()
    init_exec()
    seed_candles()''','''def main():
    load_state()
    load_fees(); load_fund_intervals()
    init_exec()
    seed_candles()
    threading.Thread(target=fees_loop, daemon=True).start()''')
open(p,'w').write(s); print("engine patch 1 applied")
