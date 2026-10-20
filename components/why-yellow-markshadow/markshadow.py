#!/usr/bin/env python3
"""WHY x Yellow MARK-CANDLE SHADOW — isolated recorder + shadow paper book.

Completely separate from /opt/why-yellow (production paper book, customer platform,
execution core). Reads ONLY Yellow public websocket channels. Sends no orders, holds no
keys, writes only under /opt/why-yellow-markshadow/state.

What it does, continuously:
  1. records every public.mark_price.<m> tick        -> state/ticks/<m>/<YYYY-MM-DD>.jsonl
  2. builds local 5m OHLC from those marks           -> state/mark_candles/<m>.jsonl
  3. records native public.kline.<m>.5m closed bars  -> state/native_klines/<m>.jsonl
     (so a native-vs-shadow comparison can be run offline once Yellow's klines unfreeze)
  4. runs the UNCHANGED production strategy (wy_logic.Engine) on the mark candles
     -> state/journal.jsonl / positions.json / snapshot.json   (shadow paper book)
  5. /health + /api/book on 127.0.0.1:8105

Seeding: production seeds 399 closed Bybit 5m bars on every start, then appends Yellow
bars. The shadow does the same, except recorded mark candles newer than the Bybit seed are
appended on restart so the book keeps its mark-candle history across restarts.
"""
import json, os, sys, threading, time, traceback, urllib.request, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from wy_logic import Engine, MarkCandleBuilder, parse_created_at, BAR_MS

STATE = os.path.join(BASE, "state")
TICKS = os.path.join(STATE, "ticks"); MC = os.path.join(STATE, "mark_candles")
NK = os.path.join(STATE, "native_klines"); SEED = os.path.join(STATE, "seed")
for d in (STATE, TICKS, MC, NK, SEED): os.makedirs(d, exist_ok=True)
WS_URL = os.environ.get("WYS_WS_URL", "wss://trade.api.yellow.pro/ws")
HTTP_PORT = int(os.environ.get("WYS_HTTP_PORT", "8105"))
STARTED = time.time()

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

LOCK = threading.RLock()
ENGINE = Engine(STATE, clock=time.time, logger=log, tag="mark-candle-shadow")
MARKETS = ENGINE.MARKETS
BUILDER = None
FEED = {"connected": False, "last_msg": 0.0, "reconnects": 0, "ticks": 0, "klines": 0}
NATIVE_CUR = {m: None for m in MARKETS}
NATIVE_LAST = {m: None for m in MARKETS}
LAST_TICK_MS = {m: None for m in MARKETS}
CANDLE_STATS = {m: dict(closed=0, partial=0, gap_bars=0, last_bar_ms=None) for m in MARKETS}
_tick_fh = {}     # (market, day) -> file handle

# ---------- persistence helpers ----------
def tick_write(m, server_ms, recv_ms, mark, index):
    day = dt.datetime.fromtimestamp(server_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    key = (m, day)
    fh = _tick_fh.get(key)
    if fh is None:
        for k in [k for k in _tick_fh if k[0] == m]:
            try: _tick_fh.pop(k).close()
            except Exception: pass
        os.makedirs(os.path.join(TICKS, m), exist_ok=True)
        fh = _tick_fh[key] = open(os.path.join(TICKS, m, day + ".jsonl"), "a", buffering=1 << 16)
    fh.write(f"[{server_ms},{recv_ms},{mark},{index}]\n")

def flush_ticks():
    for fh in _tick_fh.values():
        try: fh.flush()
        except Exception: pass

def append_jsonl(path, obj):
    with open(path, "a") as f: f.write(json.dumps(obj) + "\n")

def read_jsonl(path):
    out = []
    if not os.path.exists(path): return out
    for line in open(path):
        line = line.strip()
        if not line: continue
        try: out.append(json.loads(line))
        except Exception: pass
    return out

# ---------- seeding (same source + shape as production) ----------
def bybit_seed(m, limit=400):
    sym = m.replace("-PERP", "")
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=5&limit={limit}"
    d = json.load(urllib.request.urlopen(url, timeout=15))
    rows = d.get("result", {}).get("list", [])
    rows.sort(key=lambda r: int(r[0]))
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows[:-1]]

def seed_all():
    for m in MARKETS:
        try:
            bars = bybit_seed(m)
        except Exception as e:
            log(f"seed {m} FAILED: {e}"); bars = []
        recorded = [r for r in read_jsonl(os.path.join(MC, m + ".jsonl"))]
        rec_bars = [r["bar"] for r in recorded]
        if rec_bars:
            first_rec = rec_bars[0][0]
            bars = [b for b in bars if b[0] < first_rec] + rec_bars
            src = f"bybit<{first_rec} + {len(rec_bars)} recorded mark candles"
        else:
            src = "bybit only"
        bars = bars[-500:]
        ENGINE.seed(m, bars)
        stamp = int(time.time() * 1000)
        append_jsonl(os.path.join(SEED, m + ".jsonl"), dict(ts=stamp, source=src, n=len(bars),
                     first=bars[0][0] if bars else None, last=bars[-1][0] if bars else None, bars=bars))
        if rec_bars:
            CANDLE_STATS[m]["last_bar_ms"] = rec_bars[-1][0]
        log(f"seeded {m}: {len(bars)} bars ({src})")
        time.sleep(0.12)

# ---------- candle close -> record + engine ----------
def on_candle(m, bar, meta):
    with LOCK:
        st = CANDLE_STATS[m]
        if st["last_bar_ms"] is not None and bar[0] - st["last_bar_ms"] > BAR_MS:
            st["gap_bars"] += (bar[0] - st["last_bar_ms"]) // BAR_MS - 1
            log(f"GAP {m}: {(bar[0]-st['last_bar_ms'])//BAR_MS-1} missing 5m bars before {bar[0]}")
        st["closed"] += 1; st["partial"] += 1 if meta["partial"] else 0; st["last_bar_ms"] = bar[0]
        append_jsonl(os.path.join(MC, m + ".jsonl"), dict(bar=bar, **meta, closed_recv_ms=int(time.time() * 1000)))
        try:
            ENGINE.bar_close(m, bar)
        except Exception:
            traceback.print_exc()

# ---------- websocket ----------
def ws_loop():
    global BUILDER
    import websocket
    BUILDER = MarkCandleBuilder(on_candle)
    backoff = 1
    while True:
        try:
            ws = websocket.create_connection(WS_URL, timeout=30)
            ws.send(json.dumps({"id": 1, "connect": {}})); ws.recv()
            i = 2
            for m in MARKETS:
                ws.send(json.dumps({"id": i, "subscribe": {"channel": f"public.mark_price.{m}"}})); i += 1
                ws.send(json.dumps({"id": i, "subscribe": {"channel": f"public.kline.{m}.5m"}})); i += 1
            FEED["connected"] = True; backoff = 1
            log("yellow ws connected, subscribed", len(MARKETS) * 2, "channels")
            ws.settimeout(60)
            while True:
                try: raw = ws.recv()
                except Exception as e:
                    if "timed out" in str(e): ws.send("{}"); continue
                    raise
                FEED["last_msg"] = time.time()
                recv_ms = int(FEED["last_msg"] * 1000)
                for line in raw.split("\n"):
                    line = line.strip()
                    if not line: continue
                    if line == "{}": ws.send("{}"); continue
                    try: msg = json.loads(line)
                    except Exception: continue
                    push = msg.get("push")
                    if not push: continue
                    ch = push.get("channel", "")
                    data = (push.get("pub") or {}).get("data") or {}
                    if ch.startswith("public.mark_price."):
                        m = ch.rsplit(".", 1)[-1]
                        if m not in ENGINE.MARK: continue
                        try: p = float(data["mark_price"])
                        except Exception: continue
                        idx = data.get("index_price")
                        ts = parse_created_at((data.get("header") or {}).get("created_at", "")) or recv_ms
                        FEED["ticks"] += 1; LAST_TICK_MS[m] = ts
                        tick_write(m, ts, recv_ms, data["mark_price"], idx if idx is not None else "null")
                        with LOCK:
                            BUILDER.tick(m, ts, p)          # may close a bar -> on_candle -> on_bar_close
                            ENGINE.mark(m, p, ts / 1000.0)  # exits on the tick (production on_mark)
                    elif ch.startswith("public.kline."):
                        parts = ch.split("."); m = parts[2]
                        k = data.get("kline")
                        if not k or m not in NATIVE_CUR: continue
                        FEED["klines"] += 1
                        bar = [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                               float(k[5]) if len(k) > 5 else None]
                        cur = NATIVE_CUR[m]
                        if cur is not None and bar[0] > cur[0]:
                            frozen = cur[1] == cur[2] == cur[3] == cur[4] and (cur[5] in (0, 0.0, None))
                            append_jsonl(os.path.join(NK, m + ".jsonl"),
                                         dict(bar=cur[:5], vol=cur[5], closed_recv_ms=recv_ms, frozen=frozen))
                            NATIVE_LAST[m] = cur
                        NATIVE_CUR[m] = bar
        except Exception as e:
            FEED["connected"] = False; FEED["reconnects"] += 1
            log("ws error:", repr(e))
            flush_ticks()
            time.sleep(backoff); backoff = min(backoff * 2, 60)

# ---------- snapshot ----------
def native_status():
    out = {}
    for m in MARKETS:
        cur = NATIVE_CUR[m]; mk = ENGINE.MARK[m]
        if not cur: out[m] = None; continue
        frozen = cur[1] == cur[2] == cur[3] == cur[4] and (cur[5] in (0, 0.0, None))
        div = round((mk[0] / cur[4] - 1) * 100, 3) if mk and cur[4] else None
        out[m] = dict(bar_ms=cur[0], close=cur[4], frozen_shape=frozen, mark_vs_native_pct=div)
    return out

def snapshot_loop():
    while True:
        try:
            with LOCK:
                snap = ENGINE.snapshot(extra=dict(
                    feed=dict(connected=FEED["connected"], reconnects=FEED["reconnects"], ticks=FEED["ticks"],
                              klines=FEED["klines"],
                              last_msg_age=round(time.time() - FEED["last_msg"], 1) if FEED["last_msg"] else None),
                    candles=CANDLE_STATS, native=native_status(),
                    forming={m: (BUILDER.cur.get(m) if BUILDER else None) for m in MARKETS},
                    uptime_s=int(time.time() - STARTED), started_ms=int(STARTED * 1000)))
            flush_ticks()
        except Exception:
            traceback.print_exc()
        time.sleep(5)

# ---------- http ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        b = body.encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p in ("/health", "/"):
            self._send(200, json.dumps(dict(ok=True, tag="mark-candle-shadow", connected=FEED["connected"],
                ticks=FEED["ticks"], candles=CANDLE_STATS, native=native_status(),
                bars={m: len(ENGINE.CANDLES[m]) for m in MARKETS},
                marks={m: (ENGINE.MARK[m][0] if ENGINE.MARK[m] else None) for m in MARKETS},
                open=len(ENGINE.POS), stats=ENGINE.STATS, prod_sha256=ENGINE.sha)))
        elif p == "/api/book":
            try: snap = json.load(open(os.path.join(STATE, "snapshot.json")))
            except Exception: snap = {}
            snap["trades"] = [r for r in read_jsonl(os.path.join(STATE, "journal.jsonl")) if r.get("event") == "CLOSE"][-120:][::-1]
            self._send(200, json.dumps(snap))
        else:
            self._send(404, json.dumps(dict(error="not found")))

def main():
    log(f"mark-candle shadow starting; production logic sha256 {ENGINE.sha}; env {ENGINE.env}")
    ENGINE.load_state()
    seed_all()
    threading.Thread(target=ws_loop, daemon=True).start()
    threading.Thread(target=snapshot_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), H)
    log(f"http on 127.0.0.1:{HTTP_PORT}")
    srv.serve_forever()

if __name__ == "__main__":
    main()
