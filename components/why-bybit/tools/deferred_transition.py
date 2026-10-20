#!/usr/bin/env python3
"""ONE-TIME deferred live-risk transition for the Bybit book (why-bybit).

PHASE 1 PENDING : the armed lots are all still open on the exchange -> nothing changes, normal trading (max_open 3).
PHASE 2 DRAIN   : the moment ANY armed lot is CONFIRMED closed by exchange truth (position + its TP order gone, re-queried),
                  NEW ENTRIES ARE BLOCKED (state-live/halt.flag - the bot's existing entry gate: read only by risk_block/open(),
                  never by exits/TP/SL management; a file, so it survives restarts; re-asserted every poll). Nothing is closed or
                  modified. Any other old-config position (opened before the block) is detected, reported and added to the drain set.
PHASE 3 SWITCH  : exchange has ZERO positions and ZERO open orders (universe) AND the bot's local state caught up -> atomically merge
                  OVERRIDES into live_risk.json, restart why-bybit once (quiet window after a 5m close), verify, THEN remove the
                  entry block (only if this process created it) and confirm evaluation resumes.
Once    : persisted state machine PENDING -> DRAIN -> APPLYING -> APPLIED | ROLLED_BACK | ABORTED, fsync+rename writes, flock.
          A terminal state is never left; `run` on a terminal state exits without acting. A rollback KEEPS the entry block.
Safety  : this process never sets BYBIT_ARMED -> the client refuses any trading call; it only ever GETs.
Signals/TP/SL/timeout/universe/fees/execution are NOT touched: only the keys in OVERRIDES change.
Commands: arm --expect SYM,SYM,SYM | status | run
"""
import copy, fcntl, hashlib, json, os, shutil, subprocess, sys, time
BASE = os.environ.get("TR_BASE", "/opt/why-bybit")
sys.path.insert(0, BASE)
TRD = os.path.join(BASE, "transition")
RISK = os.path.join(BASE, "live_risk.json")
LIVE = os.path.join(BASE, "state-live")
PLAN_F, STATE_F, LOCK_F, REPORT_F = (os.path.join(TRD, n) for n in ("plan.json", "state.json", "lock", "applied_report.json"))
SERVICE = "why-bybit"
TERMINAL = ("APPLIED", "ROLLED_BACK", "ABORTED")
HALT = os.path.join(LIVE, "halt.flag")
MARK = "DEFERRED-TRANSITION DRAIN"
WINDOW = (20, 230)            # apply only 20..230 s after a 5-minute close (entries happen in the first ~4 s)
def interval(now): return 1 if (now % 300 >= 285 or now % 300 < 7) else 5     # 1 s polling around each 5-minute close (when entries happen)

OVERRIDES = dict(
    allocation_usd=194.0, compounding=False, leverage=10, margin_per_trade_usd=20.0, clip_usd=200.0, max_open=8,
    max_total_notional_usd=1600.0, daily_loss_kill_usd=194.0, max_drawdown_kill_usd=194.0,
    _status=("ACTIVE LIVE CONFIG (owner-authorized 2026-09-19, applied by the one-time deferred transition): $194 bankroll "
             "(= funded USDT), 10x, $20 fixed margin/trade, $200 target notional, max 8 open ($160 max margin committed; the ~$34 "
             "left over is free collateral headroom, NOT a 9th position), non-compounding. Only read when WB_MODE=live."),
    notes=("clip_usd = margin_per_trade_usd x leverage = $200. max_open=8 x $20 = $160 max margin committed; max_total_notional = "
           "max_open x clip = $1600. Qty is rounded DOWN to the venue qtyStep so a position never exceeds ~$200. Sizing is fixed and "
           "NON-compounding: bankroll basis = allocation_usd; profits never grow the clip. Loss kills are set to the full $194 bankroll. "
           "per_trade_max_loss_usd only gates a min-lot bump (dormant: no symbol needs a bump at $200). The previous live_risk.json is "
           "kept as live_risk.json.pre-194x8-*.bak."))

def log(*a): print(time.strftime("%H:%M:%S"), "[transition]", *a, flush=True)
def sha(o): return hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest()[:16]

def atomic_json(path, obj, mode=0o600):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=1); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    d = os.open(os.path.dirname(path), os.O_DIRECTORY); os.fsync(d); os.close(d)

def load(path, default=None):
    try: return json.load(open(path))
    except FileNotFoundError: return default

def validate(cfg):
    o = OVERRIDES
    for k in ("allocation_usd", "leverage", "margin_per_trade_usd", "clip_usd", "max_open", "max_total_notional_usd", "per_trade_max_loss_usd",
              "daily_loss_kill_usd", "max_drawdown_kill_usd", "compounding"):
        assert k in cfg, f"missing {k}"
    assert cfg["clip_usd"] == cfg["margin_per_trade_usd"] * cfg["leverage"], "clip != margin x leverage"
    assert cfg["max_total_notional_usd"] == cfg["max_open"] * cfg["clip_usd"], "max_total_notional != max_open x clip"
    assert cfg["compounding"] is False
    assert cfg["daily_loss_kill_usd"] == cfg["allocation_usd"] == cfg["max_drawdown_kill_usd"]
    return True

class Ports:
    """Everything that touches the outside world (so the state machine can be tested with fakes)."""
    def __init__(self):
        self._c = None
    def client(self):
        if self._c is None:
            os.environ.pop("BYBIT_ARMED", None)                    # GET-only: the client's own guard refuses trading calls
            import bybit_exec as be
            self._c = be.BybitClient.from_secrets(); assert self._c.has_keys, "no api key"
            self.universe = set(be.UNIVERSE.values())
        return self._c
    def now(self): return time.time()
    def sleep(self, s): time.sleep(s)
    def exchange(self):
        c = self.client()
        pos = [p for p in c.positions() if float(p.get("size") or 0) > 0 and p["symbol"] in self.universe]
        return dict(positions=pos, orders=[o for o in c.open_orders() if o["symbol"] in self.universe])
    def closed_evidence(self, sym, entry, since_ms):
        try:
            rows = self.client()._req("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": sym, "limit": 20, "startTime": int(since_ms)})["list"]
            return [dict(closedPnl=r["closedPnl"], entry=r["avgEntryPrice"], exit=r["avgExitPrice"], qty=r["qty"], updated=r["updatedTime"])
                    for r in rows if abs(float(r["avgEntryPrice"]) - entry) <= max(1e-9, entry * 1e-6)]
        except Exception as e: return [f"lookup failed: {e!r}"]
    def service_info(self):
        o = subprocess.run(["systemctl", "show", SERVICE, "-p", "ActiveState,MainPID,ActiveEnterTimestampMonotonic,ActiveEnterTimestamp"], capture_output=True, text=True).stdout
        d = dict(l.split("=", 1) for l in o.strip().splitlines() if "=" in l)
        try: start = float(subprocess.run(["date", "-d", d["ActiveEnterTimestamp"], "+%s"], capture_output=True, text=True).stdout.strip())
        except Exception: start = 0.0
        return dict(active=d.get("ActiveState") == "active", pid=int(d.get("MainPID") or 0), start=start)
    def restart(self): return subprocess.run(["systemctl", "restart", SERVICE], capture_output=True, text=True).returncode == 0
    def snapshot(self): return load(os.path.join(LIVE, "snapshot.json"))
    def journal_since(self, epoch):
        return subprocess.run(["journalctl", "-u", SERVICE, "--since", f"@{int(epoch)}", "--no-pager", "-o", "cat"], capture_output=True, text=True).stdout
    def env_of(self, pid):
        try: return dict(x.split("=", 1) for x in open(f"/proc/{pid}/environ").read().split("\0") if "=" in x)
        except Exception: return {}

def summarize(view):
    pos = {p["symbol"]: dict(side=p["side"], size=p["size"], avg=p["avgPrice"], sl=p.get("stopLoss")) for p in view["positions"]}
    orders = sorted((o["symbol"], o.get("orderType"), o.get("stopOrderType") or "", o.get("price") if o.get("orderType") == "Limit" else o.get("triggerPrice"),
                     o["qty"], o.get("orderLinkId") or "", bool(o.get("reduceOnly"))) for o in view["orders"])
    return dict(positions=pos, orders=[list(x) for x in orders])

class Transition:
    def __init__(self, ports=None): self.p = ports or Ports(); self.streak = 0

    # ---------------- exchange-truth readiness
    def lot_open(self, lot, view):
        pos = next((x for x in view["positions"] if x["symbol"] == lot["symbol"]), None)
        tp_open = any(o.get("orderLinkId") == lot["tp_link"] for o in view["orders"])
        same = bool(pos) and pos["side"] == lot["ex_side"] and abs(float(pos["avgPrice"]) - lot["entry"]) <= max(1e-9, lot["entry"] * 1e-9)
        return same or tp_open

    def is_tracked(self, p, plan):
        return any(l["symbol"] == p["symbol"] and p["side"] == l["ex_side"] and abs(float(p["avgPrice"]) - l["entry"]) <= max(1e-9, l["entry"] * 1e-9) for l in plan["tracked"])

    def strays(self, plan, view):
        return [dict(symbol=p["symbol"], side=p["side"], size=p["size"], avg=p["avgPrice"]) for p in view["positions"] if not self.is_tracked(p, plan)]

    def flat_ready(self, view):
        """Phase-3 gate: exchange truth says ZERO positions and ZERO open orders, and the bot's own state has caught up."""
        why = []; loc = load(os.path.join(LIVE, "positions.json"), {}) or {}
        if view["positions"]: why.append("exchange still has positions: " + ",".join(sorted(p["symbol"] for p in view["positions"])))
        if view["orders"]: why.append(f"exchange still has {len(view['orders'])} open order(s)")
        if loc: why.append("bot local state still lists: " + ",".join(sorted(loc)))
        if not self.p.service_info()["active"]: why.append("why-bybit service not active")
        try:
            age = self.p.now() - os.path.getmtime(os.path.join(LIVE, "positions.json"))
            if age < 15: why.append(f"positions.json changed {age:.0f}s ago (settling)")
        except FileNotFoundError: pass
        return not why, why

    # ---------------- entry block (the bot's existing halt.flag gate: entries only)
    def assert_block(self, st):
        if os.path.exists(HALT): return False
        try:
            fd = os.open(HALT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w") as f:
                f.write(f"{MARK}: new entries blocked while the original lots close; removed automatically after the switch. {time.strftime('%F %T', time.gmtime(self.p.now()))}Z\n"); f.flush(); os.fsync(f.fileno())
        except FileExistsError: return False
        st["drain"]["flag_owned"] = True; self.set_state(st); log("ENTRY BLOCK created/re-asserted:", HALT); return True

    def release_block(self, st):
        if not os.path.exists(HALT): return "already absent"
        try: mine = open(HALT).read().startswith(MARK)
        except Exception: mine = False
        if st.get("drain", {}).get("flag_owned") and mine: os.remove(HALT); return "removed"
        return "foreign halt.flag LEFT IN PLACE (not created by this transition)"

    def enter_drain(self, st, plan, closed, view):
        ev = {s: self.p.closed_evidence(s, next(l["entry"] for l in plan["tracked"] if l["symbol"] == s), next(l["opened_ms"] for l in plan["tracked"] if l["symbol"] == s)) for s in closed}
        pre = os.path.exists(HALT)
        st.update(status="DRAIN", drain=dict(ts=self.p.now(), first_closed=closed, evidence=ev, flag_owned=not pre, preexisting_flag=pre, strays=self.strays(plan, view)))
        self.set_state(st)                                                    # intent durable BEFORE the flag is written
        self.assert_block(st)
        log(f"PHASE 2 DRAIN: {closed} confirmed CLOSED on the exchange -> new entries blocked; remaining lots left untouched" + (f"; OLD-CONFIG POSITIONS ALREADY OPEN (kept, not closed): {st['drain']['strays']}" if st["drain"]["strays"] else ""))

    # ---------------- state machine
    def state(self): return load(STATE_F)
    def set_state(self, st): atomic_json(STATE_F, st)

    def run_once(self):
        """One poll. Returns the (possibly new) status string."""
        st = self.state()
        if st is None: return "UNARMED"
        if st["status"] in TERMINAL: return st["status"]
        plan = load(PLAN_F)
        if st["status"] == "APPLYING": return self.resume(st, plan)
        view = self.p.exchange()
        if st["status"] == "PENDING":                                         # ---- PHASE 1
            closed = [l["symbol"] for l in plan["tracked"] if not self.lot_open(l, view)]
            if not closed: self.streak = 0; return "PENDING"
            v2 = self.p.exchange()                                            # confirm with a fresh exchange query before blocking
            closed = [l["symbol"] for l in plan["tracked"] if l["symbol"] in closed and not self.lot_open(l, v2)]
            if not closed: log("closure not confirmed by the second exchange query - staying in PENDING"); return "PENDING"
            self.enter_drain(st, plan, closed, v2); return "DRAIN"
        d = st["drain"]                                                       # ---- PHASE 2 (DRAIN)
        self.assert_block(st)
        seen = {(x["symbol"], x["avg"]) for x in d["strays"]}
        for x in self.strays(plan, view):
            if (x["symbol"], x["avg"]) not in seen:
                d["strays"].append(x); self.set_state(st); log(f"OLD-CONFIG POSITION DETECTED (opened before the block): {x} -> added to the drain set, NOT closed")
        ok, why = self.flat_ready(view)
        self.streak = self.streak + 1 if ok else 0
        if not ok:
            if int(self.p.now()) % 30 < 5: log("draining:", "; ".join(why))
            return "DRAIN"
        off = self.p.now() % 300
        if not (WINDOW[0] <= off <= WINDOW[1]): log(f"drained (streak {self.streak}) but outside the quiet window (offset {off:.0f}s)"); return "DRAIN"
        if self.streak < 2: log("drained, confirming on the next poll"); return "DRAIN"
        return self.apply(st, plan, view)

    def apply(self, st, plan, pre_view):
        cur = load(RISK); new = copy.deepcopy(cur); new.update(copy.deepcopy(OVERRIDES)); validate(new)
        ev = {l["symbol"]: self.p.closed_evidence(l["symbol"], l["entry"], l["opened_ms"]) for l in plan["tracked"]}
        bak = os.path.join(BASE, f"live_risk.json.pre-194x8-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(self.p.now()))}.bak")
        st.update(status="APPLYING", apply=dict(ts=self.p.now(), old_sha=sha(cur), new_sha=sha(new), backup=bak, closed_evidence=ev, pre=summarize(pre_view)))
        self.set_state(st)                                                   # 1. intent is durable BEFORE any change
        if not os.path.exists(bak): shutil.copy2(RISK, bak); os.chmod(bak, 0o600)
        atomic_json(RISK, new)                                              # 2. atomic swap of the config
        log(f"live_risk.json swapped (old {sha(cur)} -> new {sha(new)}), backup {os.path.basename(bak)}")
        if not self.p.restart(): log("WARNING restart command returned non-zero")   # 3. the bot reads it at start
        return self.finish(st, plan)

    def resume(self, st, plan):
        cur = load(RISK); a = st["apply"]
        if not all(cur.get(k) == v for k, v in OVERRIDES.items()):
            log("APPLYING but config not swapped yet -> back to PENDING (preconditions will be re-verified)"); st["status"] = "PENDING"; st.pop("apply", None); self.set_state(st); return "PENDING"
        if self.p.service_info()["start"] < a["ts"] - 1: log("config swapped but bot not restarted since -> restarting"); self.p.restart()
        return self.finish(st, plan)

    def finish(self, st, plan):
        ok, detail = self.verify(st["apply"])
        st["apply"]["verification"] = detail
        if ok:
            snap0 = (self.p.snapshot() or {}).get("counters", {}).get("bars_evaluated")
            st["apply"]["block_release"] = self.release_block(st)            # new config verified -> lift the temporary entry block
            st.update(status="APPLIED", applied_ts=self.p.now()); self.set_state(st); atomic_json(REPORT_F, st)
            log("PHASE 3 SWITCHED + verified:", json.dumps({k: v for k, v in detail.items() if k != "post"}), "| entry block:", st["apply"]["block_release"])
            self.observe_resume(st, snap0); return "APPLIED"
        log("VERIFICATION FAILED:", json.dumps(detail), "-> rolling back (entry block KEPT in place)")
        shutil.copy2(st["apply"]["backup"], RISK + ".rb"); os.replace(RISK + ".rb", RISK); os.chmod(RISK, 0o600); self.p.restart()
        st.update(status="ROLLED_BACK", rolled_back_ts=self.p.now()); st["apply"]["block_release"] = "KEPT after rollback"; self.set_state(st); atomic_json(REPORT_F, st); return "ROLLED_BACK"

    def observe_resume(self, st, base):
        """Best effort, after APPLIED: confirm the bot is un-halted and evaluating again (needs one 5m close). Never changes state."""
        end = self.p.now() + 330; res = dict(unhalted=False, evaluations_resumed=False)
        while self.p.now() < end:
            sn = self.p.snapshot() or {}; res["unhalted"] = not (sn.get("risk") or {}).get("halted", True)
            b = (sn.get("counters") or {}).get("bars_evaluated")
            if res["unhalted"] and base is not None and b is not None and b > base: res.update(evaluations_resumed=True, bars_evaluated=b); break
            self.p.sleep(5)
        st["apply"]["post_observation"] = res; atomic_json(REPORT_F, st); log("post-switch observation:", json.dumps(res))

    # ---------------- post-apply verification
    def verify(self, a):
        want = OVERRIDES; det = {}; deadline = self.p.now() + 180
        while True:
            info = self.p.service_info(); snap = self.p.snapshot()
            good = False
            if info["active"] and info["start"] >= a["ts"] - 2 and snap and snap["ts"] / 1000 > info["start"] + 4:
                r = snap.get("risk") or {}; lv = r.get("live") or {}; f = snap.get("feed") or {}
                cfg = dict(mode=snap.get("mode"), bankroll=r.get("bankroll_usd"), equity0=snap.get("equity0"), clip=snap.get("clip_usd"), lev=snap.get("leverage"),
                           max_open=snap.get("max_open"), max_total=lv.get("max_total_notional_usd"), margin=lv.get("margin_per_trade_usd"),
                           compounding=lv.get("compounding"), daily_kill=lv.get("daily_loss_kill_usd"), dd_kill=lv.get("max_drawdown_kill_usd"),
                           feed=f"{f.get('markets_fresh')}/{f.get('markets_total')}", ws=f.get("connected"))
                exp = dict(mode="live", bankroll=want["allocation_usd"], equity0=want["allocation_usd"], clip=want["clip_usd"], lev=want["leverage"], max_open=want["max_open"],
                           max_total=want["max_total_notional_usd"], margin=want["margin_per_trade_usd"], compounding=False,
                           daily_kill=want["daily_loss_kill_usd"], dd_kill=want["max_drawdown_kill_usd"])
                det["config_seen"] = cfg
                good = all(cfg[k] == v for k, v in exp.items()) and cfg["ws"] and f.get("markets_fresh") == f.get("markets_total")
            if good: break
            if self.p.now() > deadline: det["error"] = "bot did not come up healthy with the new config within 180s"; return False, det
            self.p.sleep(3)
        env = self.p.env_of(info["pid"]); det["armed_live"] = (env.get("BYBIT_ARMED") == "1" and env.get("WB_MODE") == "live")
        j = self.p.journal_since(info["start"]); det["startup_reconcile_clean"] = "'clean': True" in j; det["traceback_in_log"] = "Traceback" in j
        post = summarize(self.p.exchange()); pre = a["pre"]; det["pre_existing_positions_changed"] = []
        for sym, p0 in pre["positions"].items():
            if sym not in post["positions"]: det["pre_existing_positions_changed"].append(f"{sym}: closed during the restart window (natural exit)"); continue
            same = post["positions"][sym] == p0 and [o for o in post["orders"] if o[0] == sym] == [o for o in pre["orders"] if o[0] == sym]
            if not same: det["pre_existing_positions_changed"].append(f"{sym}: MODIFIED")
        dup = []
        for sym in post["positions"]:
            tp = [o for o in post["orders"] if o[0] == sym and o[1] == "Limit" and o[6]]; sl = [o for o in post["orders"] if o[0] == sym and o[2] == "StopLoss"]
            if len(tp) > 1 or len(sl) > 1: dup.append(f"{sym}: {len(tp)} TP / {len(sl)} SL")
        det["duplicate_orders"] = dup; det["orphan_orders"] = [o for o in post["orders"] if o[0] not in post["positions"]]
        det["post"] = post
        ok = det["armed_live"] and det["startup_reconcile_clean"] and not det["traceback_in_log"] and not dup and not det["orphan_orders"] \
             and not any("MODIFIED" in x for x in det["pre_existing_positions_changed"])
        if not ok: det["error"] = "post-restart checks failed"
        return ok, det

    # ---------------- arming
    def arm(self, expect):
        os.makedirs(TRD, exist_ok=True)
        if os.path.exists(STATE_F): print("ALREADY ARMED/USED:", json.load(open(STATE_F))["status"]); return 1
        loc = load(os.path.join(LIVE, "positions.json"), {}); view = self.p.exchange(); ex = {x["symbol"]: x for x in view["positions"]}
        cur = load(RISK); new = dict(cur); new.update(OVERRIDES); validate(new)
        tracked = []
        for m, v in loc.items():
            s = m.replace("-PERP", ""); lv = v["live"]; x = ex.get(s); assert x, f"{s} not on exchange"
            assert x["side"] == ("Sell" if v["side"] == "short" else "Buy") and float(x["size"]) == float(lv["qty"]) and abs(float(x["avgPrice"]) - lv["avg_px"]) < 1e-9, f"{s} local != exchange"
            assert any(o.get("orderLinkId") == lv["tp_link"] for o in view["orders"]), f"{s}: resting TP not found on exchange"
            tracked.append(dict(symbol=s, market=m, side=v["side"], ex_side=x["side"], entry=lv["avg_px"], qty=lv["qty"], link=lv["link"], tp_link=lv["tp_link"],
                                venue_stop=lv["venue_stop"], opened_ms=v["opened_ms"]))
        assert sorted(t["symbol"] for t in tracked) == sorted(expect), f"tracked {[t['symbol'] for t in tracked]} != expected {expect}"
        plan = dict(armed_ts=self.p.now(), tracked=tracked, overrides=OVERRIDES, risk_sha_at_arm=sha(cur))
        atomic_json(PLAN_F, plan); self.set_state(dict(status="PENDING", armed_ts=plan["armed_ts"]))
        print("ARMED. tracked:", [(t["symbol"], t["side"], t["qty"], t["entry"]) for t in tracked]); return 0

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"; t = Transition()
    if cmd == "arm":
        exp = sys.argv[sys.argv.index("--expect") + 1].split(",") if "--expect" in sys.argv else []
        os.makedirs(TRD, exist_ok=True); lk = open(LOCK_F, "w"); fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB); return t.arm(exp)
    if cmd == "status":
        st = t.state(); print("state:", json.dumps(st, indent=1)[:1500] if st else "UNARMED")
        print("entry block (halt.flag) present:", os.path.exists(HALT))
        if st and st["status"] in ("PENDING", "DRAIN"):
            plan = load(PLAN_F); view = t.p.exchange()
            print("armed lots still open on exchange:", [l["symbol"] for l in plan["tracked"] if t.lot_open(l, view)])
            print("exchange positions:", [p["symbol"] for p in view["positions"]], "| open orders:", len(view["orders"]), "| strays:", t.strays(plan, view))
            if st["status"] == "DRAIN": ok, why = t.flat_ready(view); print("flat & ready to switch:", ok, why)
        return 0
    if cmd == "run":
        os.makedirs(TRD, exist_ok=True); lk = open(LOCK_F, "w")
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: log("another instance holds the lock; exiting"); return 0
        st = t.state(); log("start; state =", st["status"] if st else "UNARMED")
        while True:
            try: s = t.run_once()
            except Exception as e: log("poll error (will retry):", repr(e)); s = "PENDING"
            if s in TERMINAL or s == "UNARMED": log("terminal state", s, "- nothing more to do; exiting"); return 0
            t.p.sleep(interval(t.p.now()))
    print(__doc__); return 2

if __name__ == "__main__": sys.exit(main())
