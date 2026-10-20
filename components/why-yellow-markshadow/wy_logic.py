#!/usr/bin/env python3
"""EXACT production strategy logic, loaded read-only from /opt/why-yellow/yellow_paper.py.

Nothing is re-implemented here. The production file is parsed (AST) and its function
objects (ema_last, rsi_last, atr_last, open_position, close_position, on_mark,
on_bar_close, load_state) plus its CFG/MARKETS/state globals are executed inside a
private namespace whose file paths, clock, logger and pause flag point at the SHADOW
state directory. The production module is never imported, never written, never
started. The CFG env (WY_TAKER_BPS etc.) is read from the production systemd unit so
the shadow always runs the same fee/spread assumptions as the live paper book.

The only thing the shadow changes is WHERE THE 5-MINUTE BARS COME FROM:
    production : Yellow native  public.kline.<m>.5m   (broken / frozen since 2026-09-18)
    shadow     : local OHLC built from Yellow public.mark_price.<m> ticks
"""
import ast, hashlib, json, os, re, threading, time, traceback, types

PROD_SRC = "/opt/why-yellow/yellow_paper.py"
PROD_UNIT = "/etc/systemd/system/why-yellow.service"
BAR_MS = 300_000
WHITELIST_ASSIGN = ["MARKETS", "BYBIT", "CFG", "LOCK", "CANDLES", "CURBAR", "MARK",
                    "POS", "STATS", "EQUITY", "STARTED", "WS_OK"]
FUNCS = ["env_f", "env_i", "ema_last", "rsi_last", "atr_last", "load_state",
         "open_position", "close_position", "on_mark", "on_bar_close"]


def prod_env():
    """Environment= lines of the production unit (fee / spread knobs)."""
    env = {}
    try:
        for line in open(PROD_UNIT):
            m = re.match(r"^Environment=([A-Z0-9_]+)=(.*)$", line.strip())
            if m: env[m.group(1)] = m.group(2)
    except OSError:
        pass
    return env


def prod_sha():
    return hashlib.sha256(open(PROD_SRC, "rb").read()).hexdigest()


class Engine:
    """Production yellow_paper.py functions bound to a shadow state dir + injectable clock."""

    def __init__(self, state_dir, clock=None, logger=None, env=None, tag="shadow"):
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.clock = clock or time.time
        self.logger = logger or (lambda *a: None)
        self.env = dict(prod_env() if env is None else env)
        self.tag = tag
        self.signals = []            # OPEN records with bar_ms (in-memory, for comparison)
        self.closes = []
        self._cur_bar = {}           # market -> bar_ms currently being closed
        self.sha = prod_sha()
        self._build()

    # ---- namespace construction -------------------------------------------------
    def _build(self):
        src = open(PROD_SRC).read()
        tree = ast.parse(src)
        eng = self
        fake_os = types.SimpleNamespace(environ=self.env, path=os.path, replace=os.replace,
                                        makedirs=os.makedirs)
        fake_time = types.SimpleNamespace(time=lambda: eng.clock(), strftime=time.strftime,
                                          sleep=lambda s: None)
        ns = dict(os=fake_os, json=json, math=__import__("math"), time=fake_time,
                  threading=threading, traceback=traceback, open=open)
        # functions first (env_f/env_i are needed by the CFG assignment)
        fdefs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        missing = [f for f in FUNCS if f not in fdefs]
        if missing:
            raise RuntimeError(f"production file lacks expected functions: {missing}")
        exec(compile(ast.Module(body=[fdefs[f] for f in FUNCS], type_ignores=[]),
                     PROD_SRC, "exec"), ns)
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in WHITELIST_ASSIGN
                                                 for t in n.targets):
                exec(compile(ast.Module(body=[n], type_ignores=[]), PROD_SRC, "exec"), ns)
        # shadow-private paths + IO (production writes only to its own STATE; we write only to ours)
        ns["JOURNAL"] = os.path.join(self.state_dir, "journal.jsonl")
        ns["POSFILE"] = os.path.join(self.state_dir, "positions.json")
        ns["SNAPSHOT"] = os.path.join(self.state_dir, "snapshot.json")
        ns["PAUSE_FLAG"] = os.path.join(self.state_dir, "pause_entries.flag")
        ns["log"] = self.logger
        ns["atomic_write"] = self._atomic_write
        ns["journal_append"] = self._journal_append
        self.ns = ns
        self.MARKETS = list(ns["MARKETS"])
        self.CFG = ns["CFG"]

    # ---- IO overrides (same semantics as production, shadow paths, plus capture) ------------
    def _atomic_write(self, path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w") as f: json.dump(obj, f)
        os.replace(tmp, path)

    def _journal_append(self, rec):
        rec = dict(rec)
        if rec.get("event") == "OPEN":
            rec["bar_ms"] = self._cur_bar.get(rec["market"])
            self.signals.append(rec)
        elif rec.get("event") == "CLOSE":
            self.closes.append(rec)
        with open(self.ns["JOURNAL"], "a") as f: f.write(json.dumps(rec) + "\n")

    # ---- state access -------------------------------------------------------------
    @property
    def POS(self): return self.ns["POS"]
    @property
    def STATS(self): return self.ns["STATS"]
    @property
    def EQUITY(self): return self.ns["EQUITY"]
    @property
    def CANDLES(self): return self.ns["CANDLES"]
    @property
    def MARK(self): return self.ns["MARK"]

    def load_state(self):
        """Production load_state (restores POS/STATS from the SHADOW journal)."""
        self.ns["load_state"]()

    def seed(self, market, bars):
        """bars: closed [ms,o,h,l,c] list, oldest first (same shape production seeds from Bybit)."""
        self.ns["CANDLES"][market] = [list(b) for b in bars]

    def mark(self, market, price, ts=None):
        """Production MARK update + on_mark exit check (called per mark tick)."""
        if market not in self.ns["MARK"]: return
        self.ns["MARK"][market] = (float(price), ts if ts is not None else self.clock())
        self.ns["on_mark"](market, float(price))

    def bar_close(self, market, bar):
        """Append a CLOSED bar exactly as production's ws loop does, then production on_bar_close."""
        c = self.ns["CANDLES"][market]
        c.append([int(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])])
        if len(c) > 600: self.ns["CANDLES"][market] = c[-500:]
        self._cur_bar[market] = int(bar[0])
        self.ns["on_bar_close"](market)

    def snapshot(self, extra=None):
        ns = self.ns; STATS = ns["STATS"]; POS = ns["POS"]; CFG = ns["CFG"]; MARK = ns["MARK"]
        open_rows = []
        for m, p in POS.items():
            mk = MARK[m][0] if MARK[m] else None
            upnl = None
            if mk:
                d = (mk - p["entry"]) / p["entry"] if p["side"] == "long" else (p["entry"] - mk) / p["entry"]
                upnl = round(p["notional"] * d - p["fee_in"], 2)
            open_rows.append(dict(market=m, side=p["side"], strategy=p["strategy"], entry=p["entry"],
                                  target=p["target"], stop=p["stop"], mark=mk, upnl_usd=upnl,
                                  opened_ms=p["opened_ms"], bars_held=p["bars_held"], notional=p["notional"],
                                  detail=p.get("detail", {})))
        snap = dict(ts=int(self.clock() * 1000), tag=self.tag, equity=round(ns["EQUITY"], 2), equity0=CFG["equity0"],
                    stats=dict(STATS), open=open_rows,
                    win_rate=round(STATS["wins"] / STATS["trades"] * 100, 1) if STATS["trades"] else None,
                    universe=self.MARKETS, clip_usd=CFG["clip_usd"], leverage=CFG["leverage"], max_open=CFG["max_open"],
                    fees=dict(taker_bps=CFG["taker_bps"], maker_bps=CFG["maker_bps"], spread_bps=CFG["spread_bps"]),
                    bars={m: len(ns["CANDLES"][m]) for m in self.MARKETS},
                    prod_sha256=self.sha, prod_env=self.env)
        if extra: snap.update(extra)
        self._atomic_write(ns["SNAPSHOT"], snap)
        return snap


class MarkCandleBuilder:
    """Builds local 5m OHLC from mark ticks. No interpolation, no synthetic bars: a bar
    exists only if at least one real tick fell inside it; O/H/L/C are the first/max/min/last
    observed marks. Coverage fields (n, first_ms, last_ms, gap_max_s) are recorded per bar so
    sparse bars are visible, never hidden."""

    def __init__(self, on_close):
        self.on_close = on_close
        self.cur = {}     # market -> dict

    def tick(self, market, ts_ms, price):
        b = ts_ms // BAR_MS * BAR_MS
        cur = self.cur.get(market)
        if cur is not None and b > cur["bar_ms"]:
            self._close(market, cur)
            cur = None
        if cur is None:
            self.cur[market] = dict(bar_ms=b, o=price, h=price, l=price, c=price, n=1, first_ms=ts_ms,
                                    last_ms=ts_ms, gap_max_s=0.0)
            return
        if ts_ms < cur["last_ms"]:            # out-of-order tick: still a real observation
            cur["h"] = max(cur["h"], price); cur["l"] = min(cur["l"], price); cur["n"] += 1
            return
        cur["gap_max_s"] = max(cur["gap_max_s"], (ts_ms - cur["last_ms"]) / 1000.0)
        cur["h"] = max(cur["h"], price); cur["l"] = min(cur["l"], price); cur["c"] = price
        cur["n"] += 1; cur["last_ms"] = ts_ms

    def _close(self, market, cur):
        bar = [cur["bar_ms"], cur["o"], cur["h"], cur["l"], cur["c"]]
        meta = dict(n=cur["n"], first_ms=cur["first_ms"], last_ms=cur["last_ms"],
                    gap_max_s=round(cur["gap_max_s"], 1),
                    partial=(cur["n"] < 30 or cur["first_ms"] - cur["bar_ms"] > 60_000
                             or cur["bar_ms"] + BAR_MS - cur["last_ms"] > 60_000))
        self.on_close(market, bar, meta)

    def flush(self, market=None):
        for m in list(self.cur):
            if market is None or m == market:
                self._close(m, self.cur.pop(m))


def parse_created_at(s):
    """'2026-09-24T13:24:22.542606283Z' -> epoch ms (int) or None."""
    try:
        head, _, frac = s.rstrip("Z").partition(".")
        import datetime as dt
        base = dt.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
        ms = int(base.timestamp() * 1000)
        if frac: ms += int((frac + "000")[:3])
        return ms
    except Exception:
        return None


if __name__ == "__main__":
    e = Engine("/tmp/wy_logic_selfcheck", tag="selfcheck")
    print("prod sha256:", e.sha)
    print("prod env   :", e.env)
    print("CFG        :", json.dumps(e.CFG))
    print("markets    :", e.MARKETS)
