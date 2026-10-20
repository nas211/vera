#!/usr/bin/env python3
"""Yellow mark-shadow signal -> Bybit DEMO order bridge.

Signal source (read-only): /opt/why-yellow-markshadow/state/journal.jsonl OPEN events. Those
are produced by the UNCHANGED production strategy (yellow_paper.py loaded by AST) running on
completed 5-minute Yellow mark candles. Nothing here decides direction, market or geometry:
sleeve, side, market and the tp/sl percentages all come from the strategy.

Per signal:  signal_id (durable, from bar_ms|market|sleeve|side) -> skip if seen / stale /
symbol busy / book full / qty below min -> Bybit DEMO market order with exchange-side TP/SL
(tpslMode Full, MarkPrice trigger) -> TP/SL re-set from the real fill price -> tracked until
the demo position closes (TARGET / STOP / TIMEOUT / EXTERNAL) -> trade record with fees,
gross and net P&L from the exchange's own execution + closed-pnl rows.
"""
import json, os, sys, time, hashlib, math, threading, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import guard
from bybit_demo import BybitError

CFG = json.load(open(os.path.join(BASE, "config.json")))
STATE = os.path.join(BASE, "state"); LOGS = os.path.join(BASE, "logs")
BAR_MS = 300_000
MARKSHADOW = "/opt/why-yellow-markshadow"

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

def signal_id(rec):
    raw = f"yms|{rec.get('bar_ms')}|{rec['market']}|{rec['strategy']}|{rec['side']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]

def to_symbol(market): return market.replace("-PERP", "")

def fmt_step(x, step):
    if not step or step <= 0: return str(x)
    dec = max(0, -int(math.floor(math.log10(step) + 1e-9))) if step < 1 else 0
    return f"{math.floor(x / step + 1e-9) * step:.{dec}f}"
def fmt_tick(x, tick):
    if not tick or tick <= 0: return str(x)
    dec = max(0, -int(math.floor(math.log10(tick) + 1e-9))) if tick < 1 else 0
    return f"{round(x / tick) * tick:.{dec}f}"

# ---------- Yellow-adjusted accounting ----------
def yellow_pnl(cfg, side, entry, exit_level, reason, notional):
    """EXACT production yellow_paper.close_position arithmetic: exit slipped by spread_bps against
    the trade, taker fee in, maker fee out on TARGET else taker. `entry` is the strategy's own
    (already slipped) reference entry. Returns (exit_px, gross_usd, fees_usd, pnl_usd)."""
    slip = exit_level * cfg["spread_bps"] / 1e4
    px = exit_level - slip if side == "long" else exit_level + slip
    pnl_pct = (px - entry) / entry * 100 if side == "long" else (entry - px) / entry * 100
    fee_out_bps = cfg["maker_bps"] if reason == "TARGET" else cfg["taker_bps"]
    fees = notional * cfg["taker_bps"] / 1e4 + notional * fee_out_bps / 1e4
    gross = notional * pnl_pct / 100
    return px, gross, fees, gross - fees

def yellow_adjusted(strat_cfg, pos, shadow_close=None, yellow_mark=None, demo_reason=None):
    """Same signal priced under Yellow conditions. Final when the mark-shadow book has closed it
    (its CLOSE row = the unchanged strategy's exit on Yellow mark ticks); otherwise a pending
    estimate: open -> at the live Yellow mark, or fallback -> demo exit reason mapped onto the
    Yellow geometry (TARGET->target, STOP->stop, else current Yellow mark)."""
    notional = pos.get("reference_notional") or strat_cfg["clip_usd"]; entry = pos["reference_entry_price"]; side = pos["side"]
    if shadow_close:
        return dict(status="final", source="yellow_mark_shadow_close", entry=entry, exit=shadow_close["exit"], exit_reason=shadow_close["reason"],
                    gross_pnl_usd=round(shadow_close["pnl_usd"] + shadow_close.get("fees_usd", 0.0), 4), fees_usd=round(shadow_close.get("fees_usd", 0.0), 4),
                    net_pnl_usd=round(shadow_close["pnl_usd"], 4), notional_usd=notional, closed_ts=shadow_close.get("ts"), fee_model="yellow")
    if demo_reason in ("TARGET", "STOP"):
        level = pos["reference_target"] if demo_reason == "TARGET" else pos["reference_stop"]
        px, gross, fees, net = yellow_pnl(strat_cfg, side, entry, level, demo_reason, notional)
        return dict(status="pending", source="estimate_from_demo_exit_reason", entry=entry, exit=round(px, 8), exit_reason=demo_reason,
                    gross_pnl_usd=round(gross, 4), fees_usd=round(fees, 4), net_pnl_usd=round(net, 4), notional_usd=notional, fee_model="yellow")
    if yellow_mark:
        px, gross, fees, net = yellow_pnl(strat_cfg, side, entry, float(yellow_mark), "MARK", notional)
        return dict(status="pending", source="estimate_at_yellow_mark", entry=entry, exit=round(px, 8), exit_reason=demo_reason or "OPEN",
                    gross_pnl_usd=round(gross, 4), fees_usd=round(fees, 4), net_pnl_usd=round(net, 4), notional_usd=notional, fee_model="yellow")
    return dict(status="pending", source="no_yellow_data_yet", entry=entry, exit=None, exit_reason=demo_reason, gross_pnl_usd=None, fees_usd=None, net_pnl_usd=None, notional_usd=notional, fee_model="yellow")

# ---------- the bridge ----------
class Bridge:
    def __init__(self, client, strategy, clock=None, state_dir=STATE, logger=log, cfg=CFG):
        self.c = client; self.strat = strategy; self.clock = clock or time.time; self.log = logger; self.cfg = cfg
        self.state_dir = state_dir; os.makedirs(state_dir, exist_ok=True)
        self.f_signals = os.path.join(state_dir, "signals.jsonl")
        self.f_trades = os.path.join(state_dir, "trades.jsonl")
        self.f_open = os.path.join(state_dir, "open_positions.json")
        self.f_account = os.path.join(state_dir, "account.json")
        self.f_status = os.path.join(state_dir, "status.json")
        self.lock = threading.RLock()
        self.seen = {}            # signal_id -> last decision record
        self.open = {}            # symbol -> position record
        self.account = dict(start_equity=None, start_ms=None, peak_equity=None, max_drawdown_usd=0.0, hosts=[])
        self.instr = {}           # symbol -> dict(qtyStep,minQty,tickSize)
        self.errors = []
        self.yellow_marks = {}
        self.max_open = strategy["cfg"]["max_open"]
        self.timeout_ms = strategy["cfg"]["timeout_bars"] * BAR_MS
        self.load_state()

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
            lot = i.get("lotSizeFilter", {}); pf = i.get("priceFilter", {})
            self.instr[i["symbol"]] = dict(qtyStep=float(lot.get("qtyStep", 0)), minQty=float(lot.get("minOrderQty", 0)),
                                           tickSize=float(pf.get("tickSize", 0)), status=i.get("status"))
    def mark_price(self, symbol):
        t = self.c.tickers(symbol)[0]
        return float(t["markPrice"]), float(t.get("bid1Price") or 0), float(t.get("ask1Price") or 0), float(t.get("lastPrice") or 0)

    # ---- signal intake ----
    def canonical(self, rec):
        tp_pct, sl_pct = sleeve_geometry(rec["strategy"], self.strat["cfg"])
        e = rec["entry"]; sg = 1 if rec["side"] == "long" else -1
        # fidelity check: the strategy's own target/stop must equal entry*(1±pct) — proves the geometry is untouched
        ok = abs(rec["target"] - e * (1 + sg * tp_pct / 100)) < 1e-9 * e and abs(rec["stop"] - e * (1 - sg * sl_pct / 100)) < 1e-9 * e
        return dict(signal_id=signal_id(rec), ts=rec["ts"], bar_ms=rec.get("bar_ms"), market=rec["market"],
                    symbol=to_symbol(rec["market"]), sleeve=rec["strategy"].replace("WY-", ""), strategy=rec["strategy"],
                    side=rec["side"], reference_entry_price=rec["entry"], reference_target=rec["target"], reference_stop=rec["stop"],
                    tp_pct=tp_pct, sl_pct=sl_pct, geometry_ok=ok, source="yellow_mark_shadow", reference_notional=rec.get("notional", self.strat["cfg"]["clip_usd"]),
                    strategy_version=self.strat["sha256"][:16], detail=rec.get("detail", {}))

    def on_signal(self, rec):
        with self.lock:
            sig = self.canonical(rec); sid = sig["signal_id"]
            if sid in self.seen: return None                                   # idempotent: already decided
            age = self.clock() * 1000 - sig["ts"]
            if not sig["geometry_ok"]: return self.record_signal(dict(sig, decision="SKIP", reason="geometry_mismatch"))
            if age > self.cfg["signals"]["max_signal_age_s"] * 1000:
                return self.record_signal(dict(sig, decision="SKIP", reason=f"stale_{int(age/1000)}s"))
            if sig["symbol"] in self.open: return self.record_signal(dict(sig, decision="SKIP", reason="symbol_busy"))
            if len(self.open) >= self.max_open: return self.record_signal(dict(sig, decision="SKIP", reason="book_full"))
            return self.execute(sig)

    def execute(self, sig):
        sym = sig["symbol"]; ins = self.instr.get(sym)
        if not ins or ins.get("status") != "Trading": return self.record_signal(dict(sig, decision="SKIP", reason="symbol_unavailable"))
        try: mark, bid, ask, last = self.mark_price(sym)
        except Exception as e: return self.record_signal(dict(sig, decision="ERROR", reason=f"ticker:{e}"))
        qty = math.floor(self.cfg["sizing"]["clip_usd"] / mark / ins["qtyStep"] + 1e-9) * ins["qtyStep"]
        if qty < ins["minQty"] or qty <= 0: return self.record_signal(dict(sig, decision="SKIP", reason="below_min_qty"))
        qty_s = fmt_step(qty, ins["qtyStep"])
        sg = 1 if sig["side"] == "long" else -1
        tp = fmt_tick(mark * (1 + sg * sig["tp_pct"] / 100), ins["tickSize"]); sl = fmt_tick(mark * (1 - sg * sig["sl_pct"] / 100), ins["tickSize"])
        link = "wyd-" + sig["signal_id"]
        pos = dict(sig, decision="SUBMITTED", order_link_id=link, qty=qty_s, bybit_mark_at_signal=mark, bybit_bid=bid, bybit_ask=ask,
                   yellow_vs_bybit_bps=round((mark / sig["reference_entry_price"] - 1) * 1e4, 2) if sig["reference_entry_price"] else None,
                   tp_submitted=tp, sl_submitted=sl, submitted_ms=int(self.clock() * 1000), order_id=None, fill=None)
        self.record_signal(pos)                                                  # persisted BEFORE the request: a crash here never re-sends
        self.open[sym] = pos; self.save_open()
        try:
            r = self.c.place_market(sym, "Buy" if sg > 0 else "Sell", qty_s, link, take_profit=tp, stop_loss=sl, trigger_by=self.cfg["exits"]["trigger_by"])
            pos["order_id"] = r.get("orderId"); pos["decision"] = "ORDERED"
            self.log(f"ORDER {sig['sleeve']} {sig['side']} {sym} qty {qty_s} tp {tp} sl {sl} link {link} id {pos['order_id']}")
        except BybitError as e:
            if e.code in (110072,):                                              # duplicate orderLinkId: it already exists on the exchange
                pos["decision"] = "ORDERED"; self.log(f"order {link} already on exchange (dup link)")
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
                    except BybitError as e: pos["tpsl_reset"] = f"failed:{e.code}:{e.msg}"; self.log(f"trading-stop {sym}: {e}")
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
        trade = dict(signal_id=pos["signal_id"], yellow_signal_ts=pos["ts"], yellow_bar_ms=pos["bar_ms"], sleeve=pos["sleeve"], strategy=pos["strategy"],
                     market=pos["market"], symbol=sym, side=pos["side"], yellow_reference_price=pos["reference_entry_price"],
                     bybit_fill=entry, fill_vs_yellow_bps=pos.get("fill_vs_yellow_bps"), qty=qty, notional_usd=round(entry * qty, 2),
                     tp=pos.get("tp", pos["tp_submitted"]), sl=pos.get("sl", pos["sl_submitted"]), tp_pct=pos["tp_pct"], sl_pct=pos["sl_pct"],
                     reference_target=pos.get("reference_target"), reference_stop=pos.get("reference_stop"),
                     entry=entry, exit=exit_px, exit_reason=reason, fee_in_usd=round(fee_in, 6), fee_out_usd=round(fee_out, 6), fees_usd=round(fee_in + fee_out, 6),
                     gross_pnl_usd=round(gross, 4), net_pnl_usd=round(net, 4), exchange_closed_pnl_usd=net_exchange,
                     opened_ms=(pos.get("fill") or {}).get("filled_ms", pos["submitted_ms"]), closed_ms=int(row.get("updatedTime") or closes[-1].get("execTime") if closes else self.clock() * 1000),
                     order_ids=dict(entry=pos.get("order_id"), entry_link=pos["order_link_id"], exit=[x.get("orderId") for x in closes], exit_link=[x.get("orderLinkId") for x in closes]),
                     yellow_shadow_close=self.yellow_close_for(pos))
        trade["hold_min"] = round((trade["closed_ms"] - trade["opened_ms"]) / 60000, 1)
        trade["yellow_adjusted"] = yellow_adjusted(self.strat["cfg"], pos, trade["yellow_shadow_close"], self.yellow_marks.get(pos["market"]), reason)
        append_jsonl(self.f_trades, trade); self.open.pop(sym, None); self.save_open()
        self.log(f"CLOSED {pos['sleeve']} {pos['side']} {sym} {reason} entry {entry} exit {exit_px} gross ${gross:+.4f} fees ${fee_in+fee_out:.4f} NET ${net:+.4f}")
        return True

    def yellow_close_for(self, pos):
        """What the Yellow shadow book itself did with the same signal (for comparison only)."""
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
                    lp = live[sym]; pos["live"] = dict(size=lp.get("size"), avg=lp.get("avgPrice"), upnl=lp.get("unrealisedPnl"), mark=lp.get("markPrice"), tp=lp.get("takeProfit"), sl=lp.get("stopLoss"), lev=lp.get("leverage"))
                    if pos.get("decision") != "FILLED" and float(lp.get("avgPrice") or 0) > 0:
                        self.confirm_fill(sym, tries=1)
                    if pos.get("timeout_ms") and now >= pos["timeout_ms"] and not pos.get("timeout_sent"):
                        link = pos["order_link_id"] + "-to"
                        try:
                            r = self.c.place_market(sym, "Sell" if pos["side"] == "long" else "Buy", lp["size"], link, reduce_only=True)
                            pos["timeout_sent"] = now; pos["timeout_order_id"] = r.get("orderId"); self.log(f"TIMEOUT close sent {sym} link {link}")
                        except BybitError as e:
                            if e.code == 110072: pos["timeout_sent"] = now
                            else: self.log(f"timeout close {sym} failed: {e}")
                    continue
                # not on the exchange any more
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
        """Upgrade pending yellow_adjusted rows in trades.jsonl once the mark-shadow book has closed the same signal."""
        trades = read_jsonl(self.f_trades); changed = False
        for t in trades:
            ya = t.get("yellow_adjusted") or {}
            if ya.get("status") == "final": continue
            pos = dict(t, reference_entry_price=t["yellow_reference_price"], reference_target=t.get("reference_target"), reference_stop=t.get("reference_stop"), reference_notional=ya.get("notional_usd"))
            sc = self.yellow_close_for(pos)
            if sc: t["yellow_shadow_close"] = sc; t["yellow_adjusted"] = yellow_adjusted(self.strat["cfg"], pos, sc); changed = True
        if changed:
            tmp = self.f_trades + ".tmp"
            with open(tmp, "w") as f:
                for t in trades: f.write(json.dumps(t) + "\n")
            os.replace(tmp, self.f_trades)
        return changed

    # ---- equity / status ----
    def refresh_account(self):
        try: w = self.c.wallet()[0]
        except Exception as e: self.log(f"wallet: {e}"); return
        eq = float(w.get("totalEquity") or 0); now = int(self.clock() * 1000)
        with self.lock:
            a = self.account
            if a["start_equity"] is None: a["start_equity"] = eq; a["start_ms"] = now; a["peak_equity"] = eq
            a["equity"] = eq; a["wallet_balance"] = float(w.get("totalWalletBalance") or 0); a["available"] = float(w.get("totalAvailableBalance") or 0)
            a["unrealized_usd"] = float(w.get("totalPerpUPL") or 0); a["peak_equity"] = max(a["peak_equity"] or eq, eq)
            a["max_drawdown_usd"] = max(a.get("max_drawdown_usd", 0.0), a["peak_equity"] - eq); a["updated_ms"] = now
            a["hosts"] = sorted(self.c.hosts_contacted); a["api_calls"] = self.c.calls
            self.save_account()

    def status(self, extra=None):
        trades = read_jsonl(self.f_trades)
        def agg(rows):
            n = len(rows); w = [t for t in rows if t["net_pnl_usd"] > 0]; l = [t for t in rows if t["net_pnl_usd"] <= 0]
            gw = sum(t["net_pnl_usd"] for t in w); gl = -sum(t["net_pnl_usd"] for t in l)
            return dict(trades=n, wins=len(w), losses=len(l), win_rate=round(len(w) / n * 100, 1) if n else None,
                        net_pnl_usd=round(sum(t["net_pnl_usd"] for t in rows), 4), gross_pnl_usd=round(sum(t["gross_pnl_usd"] for t in rows), 4),
                        fees_usd=round(sum(t["fees_usd"] for t in rows), 4), profit_factor=round(gw / gl, 2) if gl > 0 else (None if not gw else "inf"),
                        avg_pnl_usd=round(sum(t["net_pnl_usd"] for t in rows) / n, 4) if n else None)
        def yrows(rows):
            out = []
            for t in rows:
                ya = t.get("yellow_adjusted") or {}
                if ya.get("net_pnl_usd") is None: continue
                out.append(dict(net_pnl_usd=ya["net_pnl_usd"], gross_pnl_usd=ya["gross_pnl_usd"], fees_usd=ya["fees_usd"], sleeve=t["sleeve"], symbol=t["symbol"], final=ya.get("status") == "final"))
            return out
        yt = yrows(trades)
        # Yellow-adjusted unrealized: open demo positions priced at the live Yellow mark with Yellow fees
        y_open = []
        for p in self.open.values():
            ya = yellow_adjusted(self.strat["cfg"], p, None, self.yellow_marks.get(p["market"]))
            y_open.append(dict(symbol=p["symbol"], sleeve=p["sleeve"], side=p["side"], yellow_entry=p["reference_entry_price"], yellow_mark=self.yellow_marks.get(p["market"]), yellow_unrealized_usd=ya.get("net_pnl_usd")))
        y_unreal = round(sum(x["yellow_unrealized_usd"] or 0 for x in y_open), 4)
        y_real = round(sum(t["net_pnl_usd"] for t in yt), 4)
        y_curve = []; eq = 0.0; pk = 0.0; mdd = 0.0
        for t in yt: eq += t["net_pnl_usd"]; pk = max(pk, eq); mdd = max(mdd, pk - eq); y_curve.append(round(eq, 4))
        a = self.account
        s = dict(ts=int(self.clock() * 1000), host=self.c.base, hosts_contacted=sorted(self.c.hosts_contacted), api_calls=self.c.calls,
                 primary="yellow_adjusted",
                 yellow_adjusted=dict(fee_model=dict(taker_bps=self.strat["cfg"]["taker_bps"], maker_bps=self.strat["cfg"]["maker_bps"], spread_bps=self.strat["cfg"]["spread_bps"]),
                                      notional_usd=self.strat["cfg"]["clip_usd"], realized_pnl_usd=y_real, unrealized_pnl_usd=y_unreal, net_pnl_usd=round(y_real + y_unreal, 4),
                                      max_drawdown_usd=round(mdd, 4), final_trades=sum(1 for t in yt if t["final"]), pending_trades=sum(1 for t in yt if not t["final"]),
                                      overall=agg(yt), by_sleeve={k: agg([t for t in yt if t["sleeve"] == k]) for k in sorted({t["sleeve"] for t in yt})},
                                      by_market={k: agg([t for t in yt if t["symbol"] == k]) for k in sorted({t["symbol"] for t in yt})}, open=y_open),
                 raw_bybit_demo=dict(starting_balance=a.get("start_equity"), current_equity=a.get("equity"), realized_pnl_usd=agg(trades)["net_pnl_usd"],
                                     unrealized_pnl_usd=a.get("unrealized_usd"), net_pnl_usd=round((a.get("equity") or 0) - (a.get("start_equity") or 0), 4),
                                     max_drawdown_usd=round(a.get("max_drawdown_usd", 0.0), 4), overall=agg(trades),
                                     by_sleeve={k: agg([t for t in trades if t["sleeve"] == k]) for k in sorted({t["sleeve"] for t in trades})},
                                     by_market={k: agg([t for t in trades if t["symbol"] == k]) for k in sorted({t["symbol"] for t in trades})}),
                 starting_balance=a.get("start_equity"), current_equity=a.get("equity"), realized_pnl_usd=agg(trades)["net_pnl_usd"],
                 unrealized_pnl_usd=a.get("unrealized_usd"), net_pnl_usd=round((a.get("equity") or 0) - (a.get("start_equity") or 0), 4),
                 max_drawdown_usd=round(a.get("max_drawdown_usd", 0.0), 4), overall=agg(trades),
                 by_sleeve={k: agg([t for t in trades if t["sleeve"] == k]) for k in sorted({t["sleeve"] for t in trades})},
                 by_market={k: agg([t for t in trades if t["symbol"] == k]) for k in sorted({t["symbol"] for t in trades})},
                 open_positions=list(self.open.values()), signals_seen=len(self.seen),
                 signal_decisions={k: sum(1 for r in self.seen.values() if r.get("decision") == k) for k in ("FILLED", "ORDERED", "SUBMITTED", "SKIP", "ERROR")},
                 strategy=dict(sha256=self.strat["sha256"], cfg=self.strat["cfg"], env=self.strat["env"]), sizing=self.cfg["sizing"], exits=self.cfg["exits"])
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
        if size < self.pos: self.pos = 0                       # rotated / truncated
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

def main():
    guard.install()
    os.makedirs(STATE, exist_ok=True); os.makedirs(LOGS, exist_ok=True)
    from bybit_demo import from_secrets
    client = from_secrets(os.path.join(BASE, CFG["secrets_file"]), logger=log)
    strat = load_strategy_cfg()
    info = client.key_info()
    if str(info.get("readOnly")) == "1": guard.die("demo key is READ-ONLY (readOnly=1): orders would be refused (10005). Mint a Read-Write demo key.")
    if "Order" not in (info.get("permissions") or {}).get("ContractTrade", []): guard.die("demo key lacks ContractTrade:Order permission")
    log(f"demo host {client.base} | demo uid {info.get('userID')} | key {client.key[:4]}...{client.key[-2:]} | strategy sha256 {strat['sha256']} | cfg {json.dumps(strat['cfg'])}")
    yh = yellow_health(); log("yellow mark-shadow health:", json.dumps({k: v for k, v in yh.items() if k != "marks"}))
    if yh.get("prod_sha256") and yh["prod_sha256"] != strat["sha256"]: guard.die("mark-shadow strategy sha != production file sha")
    b = Bridge(client, strat, logger=log); b.yellow_marks = yh.get("marks") or {}; b.load_instruments(); b.refresh_account()
    tail = JournalTail(CFG["signals"]["journal"], b.on_signal)   # old OPENs are handled by the stale rule + seen set, never executed late
    tail.pump()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body):
            bb = body.encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(bb))); self.end_headers(); self.wfile.write(bb)
        def do_GET(self):
            p = self.path.split("?")[0].rstrip("/") or "/"
            if p in ("/", "/health"): self._send(200, json.dumps(dict(ok=True, host=client.base, hosts_contacted=sorted(client.hosts_contacted), open=len(b.open), signals_seen=len(b.seen), yellow=yellow_health())))
            elif p == "/api/status": self._send(200, json.dumps(b.status(extra=dict(yellow=yellow_health()))))
            elif p == "/api/trades": self._send(200, json.dumps(read_jsonl(b.f_trades)[-200:]))
            elif p == "/api/signals": self._send(200, json.dumps(list(b.seen.values())[-200:]))
            else: self._send(404, "{}")
    srv = ThreadingHTTPServer(("127.0.0.1", CFG["http_port"]), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); log(f"http on 127.0.0.1:{CFG['http_port']}")
    last_acct = 0; last_status = 0
    while True:
        try:
            tail.pump(); b.poll()
            if time.time() - last_acct > 60: b.refresh_account(); last_acct = time.time()
            if time.time() - last_status > 30:
                yh = yellow_health(); b.yellow_marks = yh.get("marks") or {}; b.reconcile_yellow(); b.status(extra=dict(yellow=yh)); last_status = time.time()
        except guard.GuardViolation: raise
        except Exception: traceback.print_exc()
        time.sleep(2)

if __name__ == "__main__":
    main()
