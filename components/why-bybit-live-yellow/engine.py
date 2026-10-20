#!/usr/bin/env python3
"""Bybit paper OPEN event -> Bybit LIVE (mainnet) order bridge at affordable exchange size.

Signal source (read-only): /opt/why-bybit/state/journal.jsonl OPEN events. Nothing here decides
direction, market or geometry: sleeve, side, market and tp/sl
percentages all come from the strategy. Bybit is execution only.

Per signal:  durable signal_id (bar_ms|market|sleeve|side) -> idempotent skip if seen -> stale /
halt / symbol busy / book full -> account-sized Bybit lot at the live mark -> FRESH wallet read ->
fail-closed gates (exchange minimum, exposure cap, margin incl. full stop + fees, leverage) ->
ARMED?  no -> DRY_RUN record (exact order it would send, nothing sent)
        yes -> market order with exchange-side TP/SL (tpslMode Full, MarkPrice) -> TP/SL re-set from
               the real fill -> tracked until the exchange closes it (TARGET/STOP/TIMEOUT/EXTERNAL)
               -> trade record from the exchange's own executions + closed-pnl (ACTUAL) plus the
               Yellow-adjusted view of the same signal (internal comparison only).
"""
import json, os, sys, time, hashlib, math, threading, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import guard
from bybit_live import BybitError, verify_key

CFG = json.load(open(os.path.join(BASE, "config.json")))
STATE = os.path.join(BASE, "state"); LOGS = os.path.join(BASE, "logs")
BAR_MS = 300_000
MARKSHADOW = "/opt/why-yellow-markshadow"
LINK_PREFIX = "wyl-"

def now_ms(): return int(time.time() * 1000)
def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(str(x) for x in a)
    print(line, flush=True)

def append_jsonl(path, obj):
    with open(path, "a") as f: f.write(json.dumps(obj) + "\n")
def read_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try: out.append(json.loads(line))
                    except Exception: pass
    return out
def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1)
    os.replace(tmp, path)

# ---------- strategy geometry, read from the production strategy (never typed here) ----------
def load_strategy_cfg():
    """CFG + sha256 of /opt/why-yellow/yellow_paper.py via the mark-shadow's read-only loader."""
    sys.path.insert(0, MARKSHADOW)
    from wy_logic import Engine, prod_sha, prod_env
    tmp = os.path.join(STATE, "_wy_cfg_probe"); os.makedirs(tmp, exist_ok=True)
    e = Engine(tmp, tag="cfg-probe")
    return dict(cfg=dict(e.CFG), sha256=prod_sha(), env=prod_env(), markets=list(e.MARKETS))

def sleeve_geometry(strategy, cfg):
    if strategy == "WY-REVERT": return cfg["rv_tp"], cfg["rv_sl"]
    if strategy == "WY-PULLBACK": return cfg["pb_tp"], cfg["pb_sl"]
    raise ValueError(f"unknown sleeve {strategy}")

def signal_id(rec, source="yellow_mark_shadow"):
    # The Bybit paper journal records each accepted OPEN with a unique ts, but
    # does not persist bar_ms. Use that event identity so repeat paper entries
    # on the same market can each be mirrored exactly once.
    event_key = rec["ts"] if source == "bybit_paper" else rec.get("bar_ms")
    raw = f"{'wbp' if source == 'bybit_paper' else 'yms'}|{event_key}|{rec['market']}|{rec['strategy']}|{rec['side']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]

def to_symbol(market): return market.replace("-PERP", "")

def _dec(step): return max(0, -int(math.floor(math.log10(step) + 1e-9))) if step < 1 else 0
def fmt_step(x, step):
    if not step or step <= 0: return str(x)
    return f"{math.floor(x / step + 1e-9) * step:.{_dec(step)}f}"
def fmt_step_up(x, step):
    if not step or step <= 0: return str(x)
    return f"{math.ceil(x / step - 1e-9) * step:.{_dec(step)}f}"
def fmt_tick(x, tick):
    if not tick or tick <= 0: return str(x)
    return f"{round(x / tick) * tick:.{_dec(tick)}f}"

# ---------- sizing: the ONLY thing scaled ----------
def min_lot(ins, mark, sizing):
    """Smallest valid lot at `mark`: max(minOrderQty, minNotional*buffer/mark) rounded UP to qtyStep.
    With sizing.target_notional_usd set, floor(target/mark/step)*step but never below the minimum."""
    step, mq, mn = ins["qtyStep"], ins["minQty"], ins.get("minNotional") or 0.0
    need = max(mq, mn * sizing.get("min_notional_buffer", 1.0) / mark if mark > 0 else mq)
    q = float(fmt_step_up(need, step))
    if q < mq: q = mq
    tgt = sizing.get("target_notional_usd")
    if tgt:
        q2 = float(fmt_step(tgt / mark, step)); q = max(q, q2)
    return q

def affordable_lot(ins, mark, sizing, risk, sl_pct, taker_fee, equity, available, exposure):
    """Fit the requested lot to live collateral, keeping the exchange minimum.

    A result below the minimum is returned as the minimum so the normal gates
    record the precise funding shortfall. The gates reread the wallet before
    every order; this calculation alone never authorizes a submission.
    """
    minimum = min_lot(ins, mark, dict(sizing, target_notional_usd=None))
    desired = min_lot(ins, mark, sizing)
    if not mark or not ins["qtyStep"]: return minimum
    cost_per_notional = (1 / float(sizing["leverage"]) + sl_pct / 100
                         + risk.get("stop_overshoot_pct", 0) / 100 + 2 * taker_fee)
    margin_budget = max(0.0, available - risk.get("margin_buffer_usd", 0)) / cost_per_notional
    exposure_budget = max(0.0, equity * risk["max_exposure_x_equity"] - exposure)
    cap = float(fmt_step(min(margin_budget, exposure_budget) / mark, ins["qtyStep"]))
    return max(minimum, min(desired, cap))

# ---------- Yellow-adjusted accounting (secondary; internal comparison only) ----------
def yellow_pnl(cfg, side, entry, exit_level, reason, notional):
    """EXACT production yellow_paper.close_position arithmetic: exit slipped by spread_bps against the
    trade, taker fee in, maker fee out on TARGET else taker."""
    slip = exit_level * cfg["spread_bps"] / 1e4
    px = exit_level - slip if side == "long" else exit_level + slip
    pnl_pct = (px - entry) / entry * 100 if side == "long" else (entry - px) / entry * 100
    fee_out_bps = cfg["maker_bps"] if reason == "TARGET" else cfg["taker_bps"]
    fees = notional * cfg["taker_bps"] / 1e4 + notional * fee_out_bps / 1e4
    gross = notional * pnl_pct / 100
    return px, gross, fees, gross - fees

def yellow_adjusted(strat_cfg, pos, shadow_close=None, yellow_mark=None, live_reason=None, live_notional=None):
    """Same signal priced under Yellow conditions at the strategy's reference notional ($2000), plus the
    same result scaled to the live lot (`scaled_to_live`). Final when the mark-shadow book closed it."""
    notional = pos.get("reference_notional") or strat_cfg["clip_usd"]; entry = pos["reference_entry_price"]; side = pos["side"]
    def scale(d):
        if live_notional and d.get("net_pnl_usd") is not None and notional:
            k = live_notional / notional
            d["scaled_to_live"] = dict(notional_usd=round(live_notional, 4), gross_pnl_usd=round(d["gross_pnl_usd"] * k, 6), fees_usd=round(d["fees_usd"] * k, 6), net_pnl_usd=round(d["net_pnl_usd"] * k, 6))
        return d
    if shadow_close:
        return scale(dict(status="final", source="yellow_mark_shadow_close", entry=entry, exit=shadow_close["exit"], exit_reason=shadow_close["reason"],
                    gross_pnl_usd=round(shadow_close["pnl_usd"] + shadow_close.get("fees_usd", 0.0), 4), fees_usd=round(shadow_close.get("fees_usd", 0.0), 4),
                    net_pnl_usd=round(shadow_close["pnl_usd"], 4), notional_usd=notional, closed_ts=shadow_close.get("ts"), fee_model="yellow"))
    if live_reason in ("TARGET", "STOP"):
        level = pos["reference_target"] if live_reason == "TARGET" else pos["reference_stop"]
        px, gross, fees, net = yellow_pnl(strat_cfg, side, entry, level, live_reason, notional)
        return scale(dict(status="pending", source="estimate_from_live_exit_reason", entry=entry, exit=round(px, 8), exit_reason=live_reason,
                    gross_pnl_usd=round(gross, 4), fees_usd=round(fees, 4), net_pnl_usd=round(net, 4), notional_usd=notional, fee_model="yellow"))
    if yellow_mark:
        px, gross, fees, net = yellow_pnl(strat_cfg, side, entry, float(yellow_mark), "MARK", notional)
        return scale(dict(status="pending", source="estimate_at_yellow_mark", entry=entry, exit=round(px, 8), exit_reason=live_reason or "OPEN",
                    gross_pnl_usd=round(gross, 4), fees_usd=round(fees, 4), net_pnl_usd=round(net, 4), notional_usd=notional, fee_model="yellow"))
    return dict(status="pending", source="no_yellow_data_yet", entry=entry, exit=None, exit_reason=live_reason, gross_pnl_usd=None, fees_usd=None, net_pnl_usd=None, notional_usd=notional, fee_model="yellow")

# ---------- wallet snapshot (UTA; isolated-margin accounts leave totalAvailableBalance empty) ----------
def wallet_snapshot(w):
    def f(x):
        try: return float(x)
        except Exception: return None
    usdt = next((c for c in w.get("coin", []) if c.get("coin") == "USDT"), {})
    eq = f(w.get("totalEquity")) or 0.0
    avail = f(w.get("totalAvailableBalance"))
    if avail is None:
        wb = f(usdt.get("walletBalance")) or 0.0; upl = f(usdt.get("unrealisedPnl")) or 0.0
        im = f(usdt.get("totalPositionIM")) or 0.0; oim = f(usdt.get("totalOrderIM")) or 0.0; lk = f(usdt.get("locked")) or 0.0
        avail = wb + min(0.0, upl) - im - oim - lk
    return dict(equity=eq, wallet_balance=f(w.get("totalWalletBalance")) or 0.0, available=round(avail, 6), usdt_wallet=f(usdt.get("walletBalance")) or 0.0,
                usdt_equity=f(usdt.get("equity")) or 0.0, position_im=f(usdt.get("totalPositionIM")) or 0.0, order_im=f(usdt.get("totalOrderIM")) or 0.0,
                unrealized_usd=f(w.get("totalPerpUPL")) or 0.0, im_rate=w.get("accountIMRate"), mm_rate=w.get("accountMMRate"))

# ---------- the bridge ----------
class Bridge:
    def __init__(self, client, strategy, clock=None, state_dir=STATE, logger=log, cfg=CFG, arm_file=None):
        self.c = client; self.strat = strategy; self.clock = clock or time.time; self.log = logger; self.cfg = cfg
        self.state_dir = state_dir; os.makedirs(state_dir, exist_ok=True)
        self.arm_file = arm_file or os.path.join(state_dir, "ARMED.json")
        self.halt_file = os.path.join(state_dir, "halt.flag")
        self.f_signals = os.path.join(state_dir, "signals.jsonl")
        self.f_trades = os.path.join(state_dir, "trades.jsonl")
        self.f_open = os.path.join(state_dir, "open_positions.json")
        self.f_account = os.path.join(state_dir, "account.json")
        self.f_status = os.path.join(state_dir, "status.json")
        self.lock = threading.RLock()
        self.seen = {}; self.open = {}
        self.account = dict(start_equity=None, start_ms=None, peak_equity=None, max_drawdown_usd=0.0, hosts=[])
        self.instr = {}; self.errors = []; self.yellow_marks = {}; self.fees = {}
        self.key_summary = None; self.leverage_ok = {}
        self.max_open = strategy["cfg"]["max_open"]
        self.timeout_ms = strategy["cfg"]["timeout_bars"] * BAR_MS
        self.lev = float(cfg["sizing"]["leverage"])
        self.load_state()

    # ---- arm / halt ----
    def armed(self): return guard.armed(self.arm_file)
    def halted(self): return os.path.exists(self.halt_file)

    # ---- persistence ----
    def load_state(self):
        for r in read_jsonl(self.f_signals): self.seen[r["signal_id"]] = r
        if os.path.exists(self.f_open):
            try: self.open = json.load(open(self.f_open))
            except Exception: self.open = {}
        if os.path.exists(self.f_account):
            try: self.account.update(json.load(open(self.f_account)))
            except Exception: pass
    def save_open(self): atomic_write(self.f_open, self.open)
    def save_account(self): atomic_write(self.f_account, self.account)
    def record_signal(self, rec):
        rec = dict(rec, recorded_ms=int(self.clock() * 1000)); self.seen[rec["signal_id"]] = rec
        append_jsonl(self.f_signals, rec); return rec

    # ---- market metadata ----
    def load_instruments(self):
        for i in self.c.instruments():
            lot = i.get("lotSizeFilter", {}); pf = i.get("priceFilter", {}); lf = i.get("leverageFilter", {})
            self.instr[i["symbol"]] = dict(qtyStep=float(lot.get("qtyStep", 0)), minQty=float(lot.get("minOrderQty", 0)), minNotional=float(lot.get("minNotionalValue") or 0),
                                           tickSize=float(pf.get("tickSize", 0)), maxLeverage=float(lf.get("maxLeverage") or 0), status=i.get("status"))
    def mark_price(self, symbol):
        t = self.c.tickers(symbol)[0]
        return float(t["markPrice"]), float(t.get("bid1Price") or 0), float(t.get("ask1Price") or 0), float(t.get("lastPrice") or 0)
    def fee_for(self, symbol):
        if symbol not in self.fees:
            try: fr = self.c.fee_rate(symbol); self.fees[symbol] = dict(taker=float(fr.get("takerFeeRate") or 0.00055), maker=float(fr.get("makerFeeRate") or 0.0002))
            except Exception as e: self.log(f"fee-rate {symbol}: {e}"); return dict(taker=0.00055, maker=0.0002)
        return self.fees[symbol]

    # ---- signal intake ----
    def canonical(self, rec):
        tp_pct, sl_pct = sleeve_geometry(rec["strategy"], self.strat["cfg"])
        e = rec["entry"]; sg = 1 if rec["side"] == "long" else -1
        ok = abs(rec["target"] - e * (1 + sg * tp_pct / 100)) < 1e-9 * e and abs(rec["stop"] - e * (1 - sg * sl_pct / 100)) < 1e-9 * e
        source = self.cfg["signals"]["source"]
        bar_ms = rec.get("bar_ms") if rec.get("bar_ms") is not None else (rec["ts"] // BAR_MS - 1) * BAR_MS
        return dict(signal_id=signal_id(rec, source), ts=rec["ts"], bar_ms=bar_ms, market=rec["market"],
                    symbol=to_symbol(rec["market"]), sleeve=rec["strategy"].replace("WY-", ""), strategy=rec["strategy"],
                    side=rec["side"], reference_entry_price=rec["entry"], reference_target=rec["target"], reference_stop=rec["stop"],
                    tp_pct=tp_pct, sl_pct=sl_pct, geometry_ok=ok, source=source, reference_notional=rec.get("notional", self.strat["cfg"]["clip_usd"]),
                    strategy_version=self.strat["sha256"][:16], detail=rec.get("detail", {}))

    def on_signal(self, rec):
        with self.lock:
            sig = self.canonical(rec); sid = sig["signal_id"]
            if sid in self.seen: return None                                   # idempotent: already decided
            age = self.clock() * 1000 - sig["ts"]
            if not sig["geometry_ok"]: return self.record_signal(dict(sig, decision="SKIP", reason="geometry_mismatch"))
            if age > self.cfg["signals"]["max_signal_age_s"] * 1000:
                return self.record_signal(dict(sig, decision="SKIP", reason=f"stale_{int(age/1000)}s"))
            if self.halted(): return self.record_signal(dict(sig, decision="SKIP", reason="halt_flag"))
            if sig["symbol"] in self.open: return self.record_signal(dict(sig, decision="SKIP", reason="symbol_busy"))
            if len(self.open) >= self.max_open: return self.record_signal(dict(sig, decision="SKIP", reason="book_full"))
            return self.execute(sig)

    # ---- gates: every one must pass; the result is recorded on the signal ----
    def gates(self, sig, ins, mark, qty):
        r = self.cfg["risk"]; notional = qty * mark; fee = self.fee_for(sig["symbol"])
        im = notional / self.lev; sl_loss = notional * sig["sl_pct"] / 100; overshoot = notional * r.get("stop_overshoot_pct", 0) / 100
        fees_rt = notional * fee["taker"] * 2
        need_margin = im + sl_loss + overshoot + fees_rt + r.get("margin_buffer_usd", 0)
        g = dict(qty=qty, notional_usd=round(notional, 4), initial_margin_usd=round(im, 4), stop_loss_usd=round(sl_loss, 4), fees_roundtrip_usd=round(fees_rt, 4), need_available_usd=round(need_margin, 4))
        try: w = wallet_snapshot(self.c.wallet()[0])
        except Exception as e: return False, dict(g, fail=f"wallet_read:{e}")
        try: live = [p for p in self.c.positions() if float(p.get("size") or 0) > 0]
        except Exception as e: return False, dict(g, fail=f"positions_read:{e}")
        exposure = sum(float(p.get("size") or 0) * float(p.get("markPrice") or p.get("avgPrice") or 0) for p in live)
        g.update(equity=w["equity"], available=w["available"], exposure_open_usd=round(exposure, 4), exposure_after_usd=round(exposure + notional, 4),
                 exposure_cap_usd=round(w["equity"] * r["max_exposure_x_equity"], 4), open_on_exchange=len(live))
        if qty < ins["minQty"] or qty <= 0: return False, dict(g, fail="below_min_qty")
        if ins.get("minNotional") and notional < ins["minNotional"]: return False, dict(g, fail="below_min_notional")
        if exposure + notional > w["equity"] * r["max_exposure_x_equity"] + 1e-9: return False, dict(g, fail="exposure_would_exceed_equity")
        if w["available"] < need_margin: return False, dict(g, fail=f"available_{w['available']:.2f}_below_need_{need_margin:.2f}")
        if any(p["symbol"] == sig["symbol"] for p in live): return False, dict(g, fail="external_position_on_symbol")
        if len(live) >= self.max_open: return False, dict(g, fail="exchange_book_full")
        liq_dist_pct = (1 / self.lev - 0.01) * 100                    # conservative: 1% MMR
        if sig["sl_pct"] >= liq_dist_pct * 0.5: return False, dict(g, fail=f"stop_{sig['sl_pct']}pct_too_close_to_liquidation_{liq_dist_pct:.1f}pct")
        levs = [p for p in self.c.positions(sig["symbol"])]
        cur_lev = float(levs[0].get("leverage") or 0) if levs else None
        g["leverage_on_symbol"] = cur_lev
        if cur_lev is not None and abs(cur_lev - self.lev) > 1e-9:
            if not self.armed(): g["leverage_note"] = f"would set {cur_lev}->{self.lev} at arm time"
            else:
                try: self.c.set_leverage(sig["symbol"], int(self.lev)); g["leverage_set"] = True
                except BybitError as e:
                    if e.code != 110043: return False, dict(g, fail=f"set_leverage:{e.code}:{e.msg}")   # 110043 = leverage not modified
        return True, g

    def execute(self, sig):
        sym = sig["symbol"]; ins = self.instr.get(sym)
        if not ins or ins.get("status") != "Trading": return self.record_signal(dict(sig, decision="SKIP", reason="symbol_unavailable"))
        try: mark, bid, ask, last = self.mark_price(sym)
        except Exception as e: return self.record_signal(dict(sig, decision="ERROR", reason=f"ticker:{e}"))
        try:
            w = wallet_snapshot(self.c.wallet()[0])
            live = [p for p in self.c.positions() if float(p.get("size") or 0) > 0]
            exposure = sum(float(p.get("size") or 0) * float(p.get("markPrice") or p.get("avgPrice") or 0) for p in live)
            qty = affordable_lot(ins, mark, self.cfg["sizing"], self.cfg["risk"], sig["sl_pct"],
                                 self.fee_for(sym)["taker"], w["equity"], w["available"], exposure)
        except Exception as e:
            return self.record_signal(dict(sig, decision="ERROR", reason=f"sizing_read:{e}"))
        ok, g = self.gates(sig, ins, mark, qty)
        qty_s = fmt_step(qty, ins["qtyStep"]); sg = 1 if sig["side"] == "long" else -1
        tp = fmt_tick(mark * (1 + sg * sig["tp_pct"] / 100), ins["tickSize"]); sl = fmt_tick(mark * (1 - sg * sig["sl_pct"] / 100), ins["tickSize"])
        link = LINK_PREFIX + sig["signal_id"]
        base = dict(sig, order_link_id=link, qty=qty_s, bybit_mark_at_signal=mark, bybit_bid=bid, bybit_ask=ask,
                    yellow_vs_bybit_bps=round((mark / sig["reference_entry_price"] - 1) * 1e4, 2) if sig["reference_entry_price"] else None,
                    tp_submitted=tp, sl_submitted=sl, gates=g, live_notional_usd=g.get("notional_usd"), size_scale=round(g.get("notional_usd", 0) / sig["reference_notional"], 6) if sig.get("reference_notional") else None)
        if not ok: return self.record_signal(dict(base, decision="SKIP", reason="gate:" + g.get("fail", "?")))
        if not self.armed():
            self.log(f"DRY_RUN {sig['sleeve']} {sig['side']} {sym} qty {qty_s} (~${g['notional_usd']}) tp {tp} sl {sl} link {link} -- NOT SENT (unarmed)")
            return self.record_signal(dict(base, decision="DRY_RUN", reason="not_armed", would_send=dict(side="Buy" if sg > 0 else "Sell", qty=qty_s, takeProfit=tp, stopLoss=sl, orderLinkId=link)))
        pos = dict(base, decision="SUBMITTED", submitted_ms=int(self.clock() * 1000), order_id=None, fill=None)
        self.record_signal(pos)                                                  # persisted BEFORE the request: a crash here never re-sends
        self.open[sym] = pos; self.save_open()
        try:
            r = self.c.place_market(sym, "Buy" if sg > 0 else "Sell", qty_s, link, take_profit=tp, stop_loss=sl, trigger_by=self.cfg["exits"]["trigger_by"])
            pos["order_id"] = r.get("orderId"); pos["decision"] = "ORDERED"
            self.log(f"LIVE ORDER {sig['sleeve']} {sig['side']} {sym} qty {qty_s} (~${g['notional_usd']}) tp {tp} sl {sl} link {link} id {pos['order_id']}")
        except guard.NotArmed as e:
            pos["decision"] = "DRY_RUN"; pos["reason"] = f"not_armed:{e}"; self.open.pop(sym, None)
        except BybitError as e:
            if e.code in (110072,): pos["decision"] = "ORDERED"; self.log(f"order {link} already on exchange (dup link)")
            else:
                pos["decision"] = "ERROR"; pos["reason"] = f"order:{e.code}:{e.msg}"; self.open.pop(sym, None)
                self.log(f"ORDER FAILED {sym}: {e}")
        self.record_signal(pos); self.save_open()
        if pos["decision"] == "ORDERED": self.confirm_fill(sym)
        return pos

    # ---- fill confirmation + exact TP/SL from the real fill ----
    def confirm_fill(self, sym, tries=6):
        pos = self.open.get(sym)
        if not pos: return
        for i in range(tries):
            try: o = self.c.order_by_link(pos["order_link_id"])
            except Exception as e: o = None; self.log(f"order lookup {sym}: {e}")
            if o and o.get("orderStatus") in ("Filled", "PartiallyFilled") and float(o.get("avgPrice") or 0) > 0:
                avg = float(o["avgPrice"]); ins = self.instr[sym]; sg = 1 if pos["side"] == "long" else -1
                pos["fill"] = dict(avg_price=avg, qty=float(o.get("cumExecQty") or pos["qty"]), fee=float(o.get("cumExecFee") or 0),
                                   order_id=o.get("orderId"), filled_ms=int(o.get("updatedTime") or self.clock() * 1000), status=o["orderStatus"])
                pos["fill_vs_yellow_bps"] = round((avg / pos["reference_entry_price"] - 1) * 1e4, 2)
                pos["tp"] = fmt_tick(avg * (1 + sg * pos["tp_pct"] / 100), ins["tickSize"]); pos["sl"] = fmt_tick(avg * (1 - sg * pos["sl_pct"] / 100), ins["tickSize"])
                pos["decision"] = "FILLED"; pos["timeout_ms"] = pos["fill"]["filled_ms"] + self.timeout_ms
                if (pos["tp"], pos["sl"]) != (pos["tp_submitted"], pos["sl_submitted"]):
                    try: self.c.set_trading_stop(sym, pos["tp"], pos["sl"], self.cfg["exits"]["trigger_by"]); pos["tpsl_reset"] = True
                    except (BybitError, guard.NotArmed) as e: pos["tpsl_reset"] = f"failed:{e}"; self.log(f"trading-stop {sym}: {e}")
                self.log(f"FILLED {sym} @ {avg} (yellow ref {pos['reference_entry_price']}, {pos['fill_vs_yellow_bps']} bps) tp {pos['tp']} sl {pos['sl']}")
                self.record_signal(pos); self.save_open(); return pos
            if o and o.get("orderStatus") in ("Cancelled", "Rejected", "Deactivated"):
                pos["decision"] = "ERROR"; pos["reason"] = f"order_{o['orderStatus']}:{o.get('rejectReason')}"
                self.open.pop(sym, None); self.record_signal(pos); self.save_open(); self.log(f"order {sym} {o['orderStatus']}"); return pos
            time.sleep(0.5 * (i + 1)) if self.clock is time.time else None
        self.log(f"fill not confirmed yet for {sym}; will retry in poll"); return pos

    # ---- exits ----
    def close_reason(self, sym, pos, execs):
        for x in execs:
            if float(x.get("closedSize") or 0) <= 0: continue
            sot = x.get("stopOrderType") or ""
            if sot in ("TakeProfit", "PartialTakeProfit"): return "TARGET"
            if sot in ("StopLoss", "PartialStopLoss"): return "STOP"
            if (x.get("orderLinkId") or "").startswith(pos["order_link_id"] + "-to"): return "TIMEOUT"
        return "EXTERNAL"

    def finalize(self, sym, pos):
        since = (pos.get("fill") or {}).get("filled_ms", pos["submitted_ms"]) - 60_000
        try: execs = self.c.executions(sym, since); cp = self.c.closed_pnl(sym, since)
        except Exception as e: self.log(f"finalize {sym} fetch: {e}"); return False
        opens = [x for x in execs if float(x.get("closedSize") or 0) == 0 and (x.get("orderLinkId") == pos["order_link_id"] or x.get("orderId") == pos.get("order_id"))]
        closes = [x for x in execs if float(x.get("closedSize") or 0) > 0 and int(x.get("execTime") or 0) >= since]
        if not closes and not cp: return False
        fee_in = sum(float(x.get("execFee") or 0) for x in opens) or (pos.get("fill") or {}).get("fee", 0.0)
        fee_out = sum(float(x.get("execFee") or 0) for x in closes)
        row = cp[0] if cp else {}
        entry = float(row.get("avgEntryPrice") or (pos.get("fill") or {}).get("avg_price") or 0)
        exit_px = float(row.get("avgExitPrice") or 0) or (sum(float(x["execPrice"]) * float(x["execQty"]) for x in closes) / max(1e-12, sum(float(x["execQty"]) for x in closes)))
        qty = float(row.get("qty") or (pos.get("fill") or {}).get("qty") or pos["qty"])
        sg = 1 if pos["side"] == "long" else -1
        gross = sg * (exit_px - entry) * qty
        net_exchange = float(row.get("closedPnl")) if row.get("closedPnl") not in (None, "") else None
        net = gross - fee_in - fee_out
        reason = self.close_reason(sym, pos, closes)
        live_notional = entry * qty
        trade = dict(signal_id=pos["signal_id"], yellow_signal_ts=pos["ts"], yellow_bar_ms=pos["bar_ms"], sleeve=pos["sleeve"], strategy=pos["strategy"],
                     market=pos["market"], symbol=sym, side=pos["side"], yellow_reference_price=pos["reference_entry_price"],
                     bybit_fill=entry, fill_vs_yellow_bps=pos.get("fill_vs_yellow_bps"), qty=qty, notional_usd=round(live_notional, 4),
                     size_scale=round(live_notional / pos["reference_notional"], 6) if pos.get("reference_notional") else None,
                     tp=pos.get("tp", pos["tp_submitted"]), sl=pos.get("sl", pos["sl_submitted"]), tp_pct=pos["tp_pct"], sl_pct=pos["sl_pct"],
                     reference_target=pos.get("reference_target"), reference_stop=pos.get("reference_stop"),
                     entry=entry, exit=exit_px, exit_reason=reason, fee_in_usd=round(fee_in, 6), fee_out_usd=round(fee_out, 6), fees_usd=round(fee_in + fee_out, 6),
                     gross_pnl_usd=round(gross, 6), net_pnl_usd=round(net, 6), exchange_closed_pnl_usd=net_exchange, accounting="actual_bybit_live",
                     opened_ms=(pos.get("fill") or {}).get("filled_ms", pos["submitted_ms"]), closed_ms=int(row.get("updatedTime") or closes[-1].get("execTime") if closes else self.clock() * 1000),
                     order_ids=dict(entry=pos.get("order_id"), entry_link=pos["order_link_id"], exit=[x.get("orderId") for x in closes], exit_link=[x.get("orderLinkId") for x in closes]),
                     yellow_shadow_close=self.yellow_close_for(pos))
        trade["hold_min"] = round((trade["closed_ms"] - trade["opened_ms"]) / 60000, 1)
        trade["yellow_adjusted"] = yellow_adjusted(self.strat["cfg"], pos, trade["yellow_shadow_close"], self.yellow_marks.get(pos["market"]), reason, live_notional)
        append_jsonl(self.f_trades, trade); self.open.pop(sym, None); self.save_open()
        self.log(f"CLOSED {pos['sleeve']} {pos['side']} {sym} {reason} entry {entry} exit {exit_px} gross ${gross:+.4f} fees ${fee_in+fee_out:.4f} NET ${net:+.4f} (exchange {net_exchange})")
        return True

    def yellow_close_for(self, pos):
        try:
            for r in read_jsonl(self.cfg["signals"]["journal"]):
                if r.get("event") == "CLOSE" and r["market"] == pos["market"] and r["strategy"] == pos["strategy"] and r["side"] == pos["side"] and abs(r["entry"] - pos["reference_entry_price"]) < 1e-9 * max(1, r["entry"]):
                    return dict(reason=r["reason"], exit=r["exit"], pnl_usd=r["pnl_usd"], fees_usd=r.get("fees_usd", 0.0), ts=r["ts"], hold_min=r.get("hold_min"))
        except Exception: pass
        return None

    def poll(self):
        """Reconcile open positions with the exchange; enforce TIMEOUT; finalize closed ones."""
        with self.lock:
            if not self.open: return
            try: live = {p["symbol"]: p for p in self.c.positions() if float(p.get("size") or 0) > 0}
            except Exception as e: self.log(f"positions poll: {e}"); return
            now = int(self.clock() * 1000)
            for sym, pos in list(self.open.items()):
                if pos["decision"] == "ORDERED": self.confirm_fill(sym, tries=1); pos = self.open.get(sym)
                if not pos: continue
                if sym in live:
                    lp = live[sym]; pos["live"] = dict(size=lp.get("size"), avg=lp.get("avgPrice"), upnl=lp.get("unrealisedPnl"), mark=lp.get("markPrice"), tp=lp.get("takeProfit"), sl=lp.get("stopLoss"), lev=lp.get("leverage"), liq=lp.get("liqPrice"))
                    if pos.get("decision") != "FILLED" and float(lp.get("avgPrice") or 0) > 0:
                        self.confirm_fill(sym, tries=1)
                    if pos.get("timeout_ms") and now >= pos["timeout_ms"] and not pos.get("timeout_sent"):
                        link = pos["order_link_id"] + "-to"
                        try:
                            r = self.c.place_market(sym, "Sell" if pos["side"] == "long" else "Buy", lp["size"], link, reduce_only=True)
                            pos["timeout_sent"] = now; pos["timeout_order_id"] = r.get("orderId"); self.log(f"TIMEOUT close sent {sym} link {link}")
                        except guard.NotArmed as e: self.log(f"TIMEOUT close {sym} blocked: {e} (position stays under exchange TP/SL)")
                        except BybitError as e:
                            if e.code == 110072: pos["timeout_sent"] = now
                            else: self.log(f"timeout close {sym} failed: {e}")
                    continue
                if pos["decision"] in ("FILLED",) or pos.get("fill"):
                    if not self.finalize(sym, pos):
                        pos["missing_polls"] = pos.get("missing_polls", 0) + 1
                        if pos["missing_polls"] > 20: self.log(f"{sym} vanished without executions; dropping"); self.open.pop(sym, None)
                elif now - pos["submitted_ms"] > 120_000 and pos["decision"] == "ORDERED":
                    self.confirm_fill(sym, tries=1)
                    if self.open.get(sym, {}).get("decision") == "ORDERED" and now - pos["submitted_ms"] > 600_000:
                        pos["decision"] = "ERROR"; pos["reason"] = "never_filled"; self.open.pop(sym, None); self.record_signal(pos)
            self.save_open()

    def reconcile_yellow(self):
        trades = read_jsonl(self.f_trades); changed = False
        for t in trades:
            ya = t.get("yellow_adjusted") or {}
            if ya.get("status") == "final": continue
            pos = dict(t, reference_entry_price=t["yellow_reference_price"], reference_target=t.get("reference_target"), reference_stop=t.get("reference_stop"), reference_notional=ya.get("notional_usd"))
            sc = self.yellow_close_for(pos)
            if sc: t["yellow_shadow_close"] = sc; t["yellow_adjusted"] = yellow_adjusted(self.strat["cfg"], pos, sc, live_notional=t.get("notional_usd")); changed = True
        if changed:
            tmp = self.f_trades + ".tmp"
            with open(tmp, "w") as f:
                for t in trades: f.write(json.dumps(t) + "\n")
            os.replace(tmp, self.f_trades)
        return changed

    # ---- equity / status ----
    def refresh_account(self):
        try: w = wallet_snapshot(self.c.wallet()[0])
        except Exception as e: self.log(f"wallet: {e}"); return
        eq = w["equity"]; now = int(self.clock() * 1000)
        with self.lock:
            a = self.account
            if a["start_equity"] is None and self.armed(): a["start_equity"] = eq; a["start_ms"] = now; a["peak_equity"] = eq
            a.update(w); a["equity"] = eq; a["updated_ms"] = now
            if a.get("start_equity") is not None:
                a["peak_equity"] = max(a.get("peak_equity") or eq, eq); a["max_drawdown_usd"] = max(a.get("max_drawdown_usd", 0.0), a["peak_equity"] - eq)
            a["hosts"] = sorted(self.c.hosts_contacted); a["api_calls"] = self.c.calls; a["api_writes"] = self.c.writes
            self.save_account()

    def status(self, extra=None):
        trades = read_jsonl(self.f_trades)
        def agg(rows):
            n = len(rows); w = [t for t in rows if t["net_pnl_usd"] > 0]; l = [t for t in rows if t["net_pnl_usd"] <= 0]
            gw = sum(t["net_pnl_usd"] for t in w); gl = -sum(t["net_pnl_usd"] for t in l)
            return dict(trades=n, wins=len(w), losses=len(l), win_rate=round(len(w) / n * 100, 1) if n else None,
                        net_pnl_usd=round(sum(t["net_pnl_usd"] for t in rows), 6), gross_pnl_usd=round(sum(t["gross_pnl_usd"] for t in rows), 6),
                        fees_usd=round(sum(t["fees_usd"] for t in rows), 6), profit_factor=round(gw / gl, 2) if gl > 0 else (None if not gw else "inf"),
                        avg_pnl_usd=round(sum(t["net_pnl_usd"] for t in rows) / n, 6) if n else None)
        def yrows(rows, scaled=False):
            out = []
            for t in rows:
                ya = t.get("yellow_adjusted") or {}
                src = ya.get("scaled_to_live") if scaled else ya
                if not src or src.get("net_pnl_usd") is None: continue
                out.append(dict(net_pnl_usd=src["net_pnl_usd"], gross_pnl_usd=src["gross_pnl_usd"], fees_usd=src["fees_usd"], sleeve=t["sleeve"], symbol=t["symbol"], final=ya.get("status") == "final"))
            return out
        yt = yrows(trades); yts = yrows(trades, scaled=True)
        y_open = []
        for p in self.open.values():
            ya = yellow_adjusted(self.strat["cfg"], p, None, self.yellow_marks.get(p["market"]), live_notional=p.get("live_notional_usd"))
            y_open.append(dict(symbol=p["symbol"], sleeve=p["sleeve"], side=p["side"], yellow_entry=p["reference_entry_price"], yellow_mark=self.yellow_marks.get(p["market"]), yellow_unrealized_usd=ya.get("net_pnl_usd"), scaled_to_live=(ya.get("scaled_to_live") or {}).get("net_pnl_usd")))
        a = self.account; st = guard.arm_state(self.arm_file)
        def block(rows):
            return dict(overall=agg(rows), by_sleeve={k: agg([t for t in rows if t["sleeve"] == k]) for k in sorted({t["sleeve"] for t in rows})},
                        by_market={k: agg([t for t in rows if t["symbol"] == k]) for k in sorted({t["symbol"] for t in rows})})
        s = dict(ts=int(self.clock() * 1000), mode="LIVE" if st["armed"] else "DRY_RUN", armed=st["armed"], arm_reason=st["reason"], halted=self.halted(),
                 host=self.c.base, hosts_contacted=sorted(self.c.hosts_contacted), api_calls=self.c.calls, api_writes=self.c.writes, uid=(self.key_summary or {}).get("uid"),
                 primary="actual_bybit_live",
                 actual_bybit_live=dict(starting_balance=a.get("start_equity"), current_equity=a.get("equity"), available=a.get("available"),
                                        realized_pnl_usd=agg(trades)["net_pnl_usd"], unrealized_pnl_usd=a.get("unrealized_usd"),
                                        net_pnl_usd=round((a.get("equity") or 0) - (a.get("start_equity") or 0), 6) if a.get("start_equity") is not None else None,
                                        max_drawdown_usd=round(a.get("max_drawdown_usd", 0.0), 6), fee_rates=self.fees, **block(trades)),
                 yellow_adjusted=dict(fee_model=dict(taker_bps=self.strat["cfg"]["taker_bps"], maker_bps=self.strat["cfg"]["maker_bps"], spread_bps=self.strat["cfg"]["spread_bps"]),
                                      reference_notional_usd=self.strat["cfg"]["clip_usd"], realized_pnl_usd=round(sum(t["net_pnl_usd"] for t in yt), 4),
                                      unrealized_pnl_usd=round(sum(x["yellow_unrealized_usd"] or 0 for x in y_open), 4),
                                      final_trades=sum(1 for t in yt if t["final"]), pending_trades=sum(1 for t in yt if not t["final"]), **block(yt),
                                      scaled_to_live=dict(realized_pnl_usd=round(sum(t["net_pnl_usd"] for t in yts), 6), **block(yts)), open=y_open),
                 open_positions=list(self.open.values()), signal_source=self.cfg["signals"]["source"], signals_seen=len(self.seen),
                 signal_decisions={k: sum(1 for r in self.seen.values() if r.get("decision") == k) for k in ("FILLED", "ORDERED", "SUBMITTED", "DRY_RUN", "SKIP", "ERROR")},
                 key=self.key_summary, strategy=dict(sha256=self.strat["sha256"], cfg=self.strat["cfg"], env=self.strat["env"], markets=self.strat["markets"]),
                 sizing=self.cfg["sizing"], risk=self.cfg["risk"], exits=self.cfg["exits"])
        if extra: s.update(extra)
        atomic_write(self.f_status, s); return s

# ---------- journal tail (read-only) ----------
class JournalTail:
    def __init__(self, path, on_open, start_at_end=False):
        self.path = path; self.on_open = on_open; self.pos = 0
        if start_at_end and os.path.exists(path): self.pos = os.path.getsize(path)
    def pump(self):
        if not os.path.exists(self.path): return 0
        size = os.path.getsize(self.path)
        if size < self.pos: self.pos = 0
        if size == self.pos: return 0
        n = 0
        with open(self.path) as f:
            f.seek(self.pos); chunk = f.read(); self.pos = f.tell()
        for line in chunk.splitlines():
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            if r.get("event") == "OPEN": self.on_open(r); n += 1
        return n

# ---------- service ----------
def yellow_health():
    try:
        with urllib.request.urlopen(CFG["signals"]["health_url"], timeout=5) as r: h = json.load(r)
        return dict(ok=h.get("ok"), connected=h.get("connected"), ticks=h.get("ticks"), prod_sha256=h.get("prod_sha256"),
                    last_bar_ms={m: v.get("last_bar_ms") for m, v in (h.get("candles") or {}).items()}, marks=h.get("marks") or {})
    except Exception as e: return dict(ok=False, error=repr(e))

def startup_checks(client, strat, logger=log):
    """Key policy + account sanity. Any problem -> guard.die (process refuses to run)."""
    info = client.key_info()
    ok, problems, warnings, summary = verify_key(info, CFG["key_policy"], CFG["expected_uid"])
    for w in warnings: logger("KEY WARNING:", w)
    if not ok: guard.die("live key policy failed: " + "; ".join(problems))
    ai = client.account_info()
    summary["margin_mode"] = ai.get("marginMode"); summary["unified_margin_status"] = ai.get("unifiedMarginStatus")
    logger(f"live host {client.base} | uid {summary['uid']} | key {client.key_masked} (id {summary['key_id']}) | readOnly {summary['readOnly']} | withdrawal {summary['withdrawal_permission']} | transfer {summary['transfer_permission']} | ips {summary['ips']} | margin {summary['margin_mode']}")
    logger(f"strategy sha256 {strat['sha256']} | cfg {json.dumps(strat['cfg'])}")
    return summary, warnings

def main():
    guard.install()
    os.makedirs(STATE, exist_ok=True); os.makedirs(LOGS, exist_ok=True)
    from bybit_live import from_secrets
    client = from_secrets(os.path.join(BASE, CFG["secrets_file"]), logger=log, arm_file=guard.ARM_FILE)
    strat = load_strategy_cfg()
    summary, warnings = startup_checks(client, strat)
    yh = yellow_health(); log("yellow mark-shadow health:", json.dumps({k: v for k, v in yh.items() if k != "marks"}))
    if yh.get("prod_sha256") and yh["prod_sha256"] != strat["sha256"]: guard.die("mark-shadow strategy sha != production file sha")
    b = Bridge(client, strat, logger=log); b.key_summary = dict(summary, warnings=warnings); b.yellow_marks = yh.get("marks") or {}; b.load_instruments(); b.refresh_account()
    st = guard.arm_state(guard.ARM_FILE); log(f"MODE: {'LIVE (ARMED)' if st['armed'] else 'DRY_RUN'} - {st['reason']}")
    tail = JournalTail(CFG["signals"]["journal"], b.on_signal)
    tail.pump()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body):
            bb = body.encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(bb))); self.end_headers(); self.wfile.write(bb)
        def do_GET(self):
            p = self.path.split("?")[0].rstrip("/") or "/"
            if p in ("/", "/health"):
                st = guard.arm_state(guard.ARM_FILE)
                self._send(200, json.dumps(dict(ok=True, mode="LIVE" if st["armed"] else "DRY_RUN", armed=st["armed"], halted=b.halted(), signal_source=CFG["signals"]["source"], available_usdt=b.account.get("available"), host=client.base, hosts_contacted=sorted(client.hosts_contacted), api_writes=client.writes, open=len(b.open), signals_seen=len(b.seen), uid=summary.get("uid"), yellow=yellow_health())))
            elif p == "/api/status": self._send(200, json.dumps(b.status(extra=dict(yellow=yellow_health()))))
            elif p == "/api/trades": self._send(200, json.dumps(read_jsonl(b.f_trades)[-200:]))
            elif p == "/api/signals": self._send(200, json.dumps(list(b.seen.values())[-200:]))
            else: self._send(404, "{}")
    srv = ThreadingHTTPServer(("127.0.0.1", CFG["http_port"]), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); log(f"http on 127.0.0.1:{CFG['http_port']}")
    last_acct = 0; last_status = 0; last_mode = st["armed"]
    while True:
        try:
            cur = guard.armed(guard.ARM_FILE)
            if cur != last_mode: log(f"MODE CHANGE: {'ARMED -> LIVE orders enabled' if cur else 'DISARMED -> DRY_RUN'}"); last_mode = cur; b.refresh_account()
            tail.pump(); b.poll()
            if time.time() - last_acct > 60: b.refresh_account(); last_acct = time.time()
            if time.time() - last_status > 30:
                yh = yellow_health(); b.yellow_marks = yh.get("marks") or {}; b.reconcile_yellow(); b.status(extra=dict(yellow=yh)); last_status = time.time()
        except guard.GuardViolation: raise
        except Exception: traceback.print_exc()
        time.sleep(CFG.get("poll_s", 2))

if __name__ == "__main__":
    main()
