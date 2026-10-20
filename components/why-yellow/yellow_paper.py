#!/usr/bin/env python3
"""WHY x Yellow — strategy book on the Yellow Pro perp universe.

Feeds on Yellow Pro public market data (wss://trade.api.yellow.pro/ws,
Centrifugo protocol) and runs the two calibrated sleeves on the venue's own
10-market universe:

  WY-REVERT   RSI(14) extreme fade, trend-gated (EMA20 vs EMA50)
              tp 0.30% / sl 1.60% / timeout 12h   (calib: 91% win, +6.0%/31d @10bps)
  WY-PULLBACK 2.0-ATR pullback to EMA20 with the trend
              tp 0.80% / sl 2.00% / timeout 12h   (calib: 68.3% win, +10.7%/31d @10bps)

No orders are ever sent anywhere. Reads public data, writes a journal and a
snapshot, serves the /yellow page on loopback WY_HTTP_PORT.

Fee model is env-driven so the venue's negotiated rates drop in without a
code change: WY_TAKER_BPS / WY_MAKER_BPS / WY_SPREAD_BPS.
"""
import json, math, os, threading, time, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
os.makedirs(STATE, exist_ok=True)
JOURNAL = os.path.join(STATE, "journal.jsonl")
POSFILE = os.path.join(STATE, "positions.json")
SNAPSHOT = os.path.join(STATE, "snapshot.json")

MARKETS = ["BTCUSDT-PERP","ETHUSDT-PERP","SOLUSDT-PERP","XRPUSDT-PERP","DOGEUSDT-PERP",
           "BNBUSDT-PERP","LINKUSDT-PERP","SUIUSDT-PERP","TAOUSDT-PERP","HYPEUSDT-PERP"]
BYBIT = {m: m.replace("-PERP","") for m in MARKETS}

def env_f(k, d): return float(os.environ.get(k, d))
def env_i(k, d): return int(os.environ.get(k, d))

CFG = dict(
    equity0    = env_f("WY_EQUITY0", 25000),
    clip_usd   = env_f("WY_CLIP_USD", 2000),
    leverage   = env_f("WY_LEVERAGE", 10),
    taker_bps  = env_f("WY_TAKER_BPS", 4.0),
    maker_bps  = env_f("WY_MAKER_BPS", 1.0),
    spread_bps = env_f("WY_SPREAD_BPS", 2.0),
    max_open   = env_i("WY_MAX_OPEN", 8),
    timeout_bars = env_i("WY_TIMEOUT_BARS", 144),   # 12h of 5m
    rv_tp = env_f("WY_RV_TP_PCT", 0.30), rv_sl = env_f("WY_RV_SL_PCT", 1.60),
    rv_rsi_lo = env_f("WY_RV_RSI_LO", 30), rv_rsi_hi = env_f("WY_RV_RSI_HI", 70),
    pb_tp = env_f("WY_PB_TP_PCT", 0.80), pb_sl = env_f("WY_PB_SL_PCT", 2.00),
    pb_depth = env_f("WY_PB_DEPTH_ATR", 2.0),
    http_port = env_i("WY_HTTP_PORT", 8102),
)

LOCK = threading.RLock()
CANDLES = {m: [] for m in MARKETS}     # closed bars: [ms,o,h,l,c]
CURBAR = {m: None for m in MARKETS}    # forming bar
MARK = {m: None for m in MARKETS}      # (price, ts)
POS = {}                               # market -> position dict
STATS = dict(trades=0, wins=0, net_usd=0.0, fees_usd=0.0, by_strat={})
EQUITY = CFG["equity0"]
STARTED = time.time()
WS_OK = {"connected": False, "last_msg": 0}

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, path)

def journal_append(rec):
    with open(JOURNAL, "a") as f: f.write(json.dumps(rec) + "\n")

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
def load_state():
    global POS, STATS, EQUITY
    if os.path.exists(POSFILE):
        try: POS = json.load(open(POSFILE))
        except Exception: POS = {}
    if os.path.exists(JOURNAL):
        for line in open(JOURNAL):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("event") == "CLOSE":
                STATS["trades"] += 1
                STATS["net_usd"] += r["pnl_usd"]
                STATS["fees_usd"] += r.get("fees_usd", 0)
                if r["pnl_usd"] > 0: STATS["wins"] += 1
                s = STATS["by_strat"].setdefault(r["strategy"], dict(trades=0, wins=0, net_usd=0.0))
                s["trades"] += 1; s["net_usd"] += r["pnl_usd"]
                if r["pnl_usd"] > 0: s["wins"] += 1
    EQUITY = CFG["equity0"] + STATS["net_usd"]
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
PAUSE_FLAG = os.path.join(STATE, "pause_entries.flag")   # touch to pause NEW paper entries; exits unaffected

def open_position(m, side, strat, price, detail):
    global POS
    if os.path.exists(PAUSE_FLAG):
        log(f"entry PAUSED ({PAUSE_FLAG} present): skip {strat} {side} {m}"); return
    slip = price * CFG["spread_bps"]/1e4
    entry = price + slip if side == "long" else price - slip
    tp_pct, sl_pct = (CFG["rv_tp"], CFG["rv_sl"]) if strat == "WY-REVERT" else (CFG["pb_tp"], CFG["pb_sl"])
    fee_in = CFG["clip_usd"] * CFG["taker_bps"]/1e4
    pos = dict(market=m, side=side, strategy=strat, entry=entry, opened_ms=int(time.time()*1000),
               bars_held=0, tp_pct=tp_pct, sl_pct=sl_pct, notional=CFG["clip_usd"],
               fee_in=fee_in, detail=detail)
    if side == "long":
        pos["target"] = entry*(1+tp_pct/100); pos["stop"] = entry*(1-sl_pct/100)
    else:
        pos["target"] = entry*(1-tp_pct/100); pos["stop"] = entry*(1+sl_pct/100)
    POS[m] = pos
    atomic_write(POSFILE, POS)
    journal_append(dict(event="OPEN", ts=pos["opened_ms"], **{k: pos[k] for k in
        ("market","side","strategy","entry","target","stop","notional","detail")}))
    log(f"OPEN {strat} {side} {m} @ {entry:.6g} tp {pos['target']:.6g} sl {pos['stop']:.6g}")

def close_position(m, exit_price, reason):
    global EQUITY
    pos = POS.pop(m, None)
    if not pos: return
    slip = exit_price * CFG["spread_bps"]/1e4
    px = exit_price - slip if pos["side"] == "long" else exit_price + slip
    if pos["side"] == "long": pnl_pct = (px - pos["entry"]) / pos["entry"] * 100
    else: pnl_pct = (pos["entry"] - px) / pos["entry"] * 100
    fee_out_bps = CFG["maker_bps"] if reason == "TARGET" else CFG["taker_bps"]
    fees = pos["fee_in"] + pos["notional"] * fee_out_bps/1e4
    pnl_usd = pos["notional"] * pnl_pct/100 - fees
    now = int(time.time()*1000)
    with LOCK:
        STATS["trades"] += 1; STATS["net_usd"] += pnl_usd; STATS["fees_usd"] += fees
        if pnl_usd > 0: STATS["wins"] += 1
        s = STATS["by_strat"].setdefault(pos["strategy"], dict(trades=0, wins=0, net_usd=0.0))
        s["trades"] += 1; s["net_usd"] += pnl_usd
        if pnl_usd > 0: s["wins"] += 1
        EQUITY = CFG["equity0"] + STATS["net_usd"]
    atomic_write(POSFILE, POS)
    journal_append(dict(event="CLOSE", ts=now, market=m, side=pos["side"], strategy=pos["strategy"],
                        entry=pos["entry"], exit=px, reason=reason, pnl_pct=round(pnl_pct,4),
                        pnl_usd=round(pnl_usd,2), fees_usd=round(fees,2),
                        hold_min=round((now-pos["opened_ms"])/60000,1), notional=pos["notional"],
                        detail=pos.get("detail",{})))
    log(f"CLOSE {pos['strategy']} {pos['side']} {m} @ {px:.6g} {reason} pnl ${pnl_usd:+.2f}")

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
        if pos["bars_held"] >= CFG["timeout_bars"] and MARK[m]:
            close_position(m, MARK[m][0], "TIMEOUT")
        return
    if len(POS) >= CFG["max_open"]: return
    closes = [b[4] for b in bars[-250:]]
    c = closes[-1]
    e20 = ema_last(closes, 20); e50 = ema_last(closes, 50)
    r = rsi_last(closes[-60:]); a = atr_last(bars[-60:])
    up = e20 > e50
    px = MARK[m][0] if MARK[m] else c
    if r < CFG["rv_rsi_lo"] and up:
        open_position(m, "long", "WY-REVERT", px, dict(rsi=round(r,1), trend="up"))
    elif r > CFG["rv_rsi_hi"] and not up:
        open_position(m, "short", "WY-REVERT", px, dict(rsi=round(r,1), trend="down"))
    elif up and c < e20 - CFG["pb_depth"]*a:
        open_position(m, "long", "WY-PULLBACK", px, dict(depth_atr=round((e20-c)/a,2), trend="up"))
    elif (not up) and c > e20 + CFG["pb_depth"]*a:
        open_position(m, "short", "WY-PULLBACK", px, dict(depth_atr=round((c-e20)/a,2), trend="down"))

# ---------- websocket feed ----------
def ws_loop():
    import websocket
    backoff = 1
    while True:
        try:
            ws = websocket.create_connection("wss://trade.api.yellow.pro/ws", timeout=30)
            ws.send(json.dumps({"id": 1, "connect": {}}))
            ws.recv()
            i = 2
            for m in MARKETS:
                ws.send(json.dumps({"id": i, "subscribe": {"channel": f"public.mark_price.{m}"}})); i += 1
                ws.send(json.dumps({"id": i, "subscribe": {"channel": f"public.kline.{m}.5m"}})); i += 1
            WS_OK["connected"] = True; backoff = 1
            log("yellow ws connected, subscribed", len(MARKETS)*2, "channels")
            ws.settimeout(60)
            while True:
                try: raw = ws.recv()
                except Exception as e:
                    # idle timeout: nudge with an empty frame (centrifugo tolerates it)
                    if "timed out" in str(e): ws.send("{}"); continue
                    raise
                WS_OK["last_msg"] = time.time()
                for line in raw.split("\n"):
                    line = line.strip()
                    if not line: continue
                    if line == "{}":            # centrifugo app-level ping
                        ws.send("{}"); continue
                    try: msg = json.loads(line)
                    except Exception: continue
                    push = msg.get("push")
                    if not push: continue
                    ch = push.get("channel", "")
                    data = (push.get("pub") or {}).get("data") or {}
                    if ch.startswith("public.mark_price."):
                        m = ch.rsplit(".", 1)[-1]
                        try: p = float(data["mark_price"])
                        except Exception: continue
                        if m in MARK:
                            MARK[m] = (p, time.time())
                            on_mark(m, p)
                    elif ch.startswith("public.kline."):
                        parts = ch.split(".")
                        m = parts[2]
                        k = data.get("kline")
                        if not k or m not in CANDLES: continue
                        bar = [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])]
                        cur = CURBAR[m]
                        if cur is not None and bar[0] > cur[0]:
                            CANDLES[m].append(cur)
                            if len(CANDLES[m]) > 600: CANDLES[m] = CANDLES[m][-500:]
                            on_bar_close(m)
                        CURBAR[m] = bar
        except Exception as e:
            WS_OK["connected"] = False
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
                snap = dict(ts=int(time.time()*1000), equity=round(EQUITY,2),
                    equity0=CFG["equity0"], stats=dict(STATS), open=open_rows,
                    win_rate=round(STATS["wins"]/STATS["trades"]*100,1) if STATS["trades"] else None,
                    universe=MARKETS, clip_usd=CFG["clip_usd"], leverage=CFG["leverage"],
                    max_open=CFG["max_open"],
                    fees=dict(taker_bps=CFG["taker_bps"], maker_bps=CFG["maker_bps"]),
                    feed=dict(connected=WS_OK["connected"], last_msg_age=round(time.time()-WS_OK["last_msg"],1) if WS_OK["last_msg"] else None),
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
        if p in ("/", "/yellow"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif p in ("/api/book", "/yellow/api/book"):
            try: snap = json.load(open(SNAPSHOT))
            except Exception: snap = {}
            snap["trades"] = read_trades(120)
            self._send(200, json.dumps(snap))
        elif p == "/health":
            self._send(200, json.dumps(dict(ok=True, connected=WS_OK["connected"],
                bars={m: [len(CANDLES[m]), CANDLES[m][-1][0] if CANDLES[m] else None] for m in MARKETS},
                marks={m: (MARK[m][0] if MARK[m] else None) for m in MARKETS})))
        else:
            self._send(404, json.dumps(dict(error="not found")))

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WHY &times; Yellow</title>
<style>
:root{--bg:#0b0e11;--panel:#12161c;--line:#1f2630;--txt:#e8edf2;--dim:#8b97a5;
--gold:#f5c518;--green:#2ecc8f;--red:#ff5d6c}
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
<h1>WHY <span class="y">&times;</span> YELLOW</h1>
<div class="sub"><span class="dot" id="dot" style="background:#555"></span>
WHY Protocol strategy book on the Yellow Pro perpetuals universe &middot; <span id="upd" class="d"></span></div>
<div class="stats" id="stats"></div>
<h2>Open Positions</h2><div class="wrap"><table id="open"><thead><tr>
<th>Market</th><th>Side</th><th>Strategy</th><th>Entry</th><th>Mark</th><th>Target</th><th>Stop</th><th>uPnL</th><th>Opened</th></tr></thead><tbody></tbody></table></div>
<h2>Strategy Performance</h2><div class="wrap"><table id="strat"><thead><tr>
<th>Strategy</th><th>Trades</th><th>Win rate</th><th>Net PnL</th></tr></thead><tbody></tbody></table></div>
<h2>Trade History</h2><div class="wrap"><table id="hist"><thead><tr>
<th>Closed</th><th>Market</th><th>Side</th><th>Strategy</th><th>Entry</th><th>Exit</th><th>Exit type</th><th>PnL</th><th>Held</th><th>Signal</th></tr></thead><tbody></tbody></table></div>
<div class="foot">Performance is tracked against live Yellow Pro market data with venue fee
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
  const r=await fetch('/yellow/api/book',{cache:'no-store'});const b=await r.json();
  $('#dot').style.background=(b.feed&&b.feed.connected)?'var(--green)':'var(--red)';
  $('#upd').textContent='updated '+new Date(b.ts).toLocaleTimeString();
  const st=b.stats||{},wr=b.win_rate,net=st.net_usd||0;
  $('#stats').innerHTML=
   card('Win rate',wr==null?'—':fmt(wr,1)+'%',wr>=68?'pos':'')+
   card('Net PnL',(net>=0?'+$':'-$')+fmt(Math.abs(net)),net>=0?'pos':'neg')+
   card('Equity','$'+fmt(b.equity))+
   card('Closed trades',st.trades||0)+
   card('Open',(b.open||[]).length+' / '+(b.max_open||8))+
   card('Fees paid','$'+fmt(st.fees_usd||0));
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
   <td class="n ${t.pnl_usd>=0?'g':'r'}">${(t.pnl_usd>=0?'+$':'-$')+fmt(Math.abs(t.pnl_usd))}</td>
   <td class="d">${t.hold_min<90?Math.round(t.hold_min)+'m':(t.hold_min/60).toFixed(1)+'h'}</td>
   <td class="d">${t.detail?Object.entries(t.detail).map(([k,v])=>k+' '+v).join(' · '):''}</td></tr>`).join('')||'<tr><td colspan="10" class="d">no closed trades yet</td></tr>';
 }catch(e){$('#dot').style.background='var(--red)'}
}
tick();setInterval(tick,5000);
</script></body></html>"""

def main():
    load_state()
    seed_candles()
    threading.Thread(target=ws_loop, daemon=True).start()
    threading.Thread(target=snapshot_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", CFG["http_port"]), H)
    log(f"http on 127.0.0.1:{CFG['http_port']}")
    srv.serve_forever()

if __name__ == "__main__":
    main()
