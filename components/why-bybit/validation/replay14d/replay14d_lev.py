import json, time, math, urllib.request, collections, sys
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
D = "/opt/why-bybit/validation/replay14d/"; V = "/opt/why-bybit/validation/"
MAKER, TAKER = 2.0/1e4, 5.5/1e4; LEV = int(sys.argv[1]); MARGIN = 10.0; CLIP = MARGIN*LEV; CAP = 2.0; EQ0 = 100.0   # ONLY sizing layer parameterized: $10 margin x leverage
now = time.time()*1000; cutoff = now - 14*86400000
rows = [json.loads(l) for l in open(D+"yellow_journal_copy.jsonl") if l.strip()]
opens = collections.defaultdict(list); trades = []
for r in rows:
    if r["event"] == "OPEN": opens[r["market"]].append(r)
    elif r["event"] == "CLOSE":
        o = opens[r["market"]].pop(0); assert o["side"] == r["side"] and o["strategy"] == r["strategy"]
        if o["ts"] >= cutoff:
            trades.append(dict(open_ts=o["ts"], close_ts=r["ts"], market=r["market"], sym=r["market"].replace("-PERP",""), strat=r["strategy"], side=r["side"],
                               y_entry=o["entry"], y_exit=r["exit"], reason=r["reason"], y_pnl=r["pnl_usd"], y_notional=r["notional"], hold_min=r["hold_min"]))
trades.sort(key=lambda t: t["open_ts"]); assert not any(v for v in opens.values()), "unclosed positions"
SYMS = sorted({t["sym"] for t in trades}); t0 = min(t["open_ts"] for t in trades) - 3600000; t1 = max(t["close_ts"] for t in trades) + 3600000
def get(u):
    for i in range(4):
        try: return json.load(urllib.request.urlopen(u, timeout=20))["result"]
        except Exception as e: err = e; time.sleep(1+i)
    raise err
K1 = {}
for s in SYMS:
    out = {}; e = int(t1)
    while e > t0:
        l = get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={s}&interval=1&end={e}&limit=1000")["list"]
        if not l: break
        for r in l: out[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4])]
        e = min(int(r[0]) for r in l) - 1; time.sleep(0.08)
    K1[s] = out
inst = {x["symbol"]: x for x in get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")["list"]}
FUND = {s: {int(k): v for k, v in json.load(open(V+f"data_bybit/fund_{s}.json")).items()} for s in SYMS}
def price_at(s, ts):                     # Bybit price at the exact timestamp: linear within the 1-minute candle (open->close)
    m = int(ts // 60000 * 60000); b = K1[s].get(m)
    if b is None: raise KeyError((s, ts))
    return b[0] + (b[3]-b[0]) * ((ts-m)/60000)
def through(s, ts, level, side):         # did Bybit trade THROUGH the target level (by >=1 tick) within +-1 min of the exit time?
    tick = float(inst[s]["priceFilter"]["tickSize"]); m0 = int(ts // 60000 * 60000)
    for m in (m0-60000, m0, m0+60000):
        b = K1[s].get(m)
        if b and ((side == "long" and b[1] >= level + tick) or (side == "short" and b[2] <= level - tick)): return True
    return False
def funding(s, side, a, b):
    iv = (4 if s == "TAOUSDT" else 8)*3600000; t = (int(a)//iv+1)*iv; c = 0.0; ks = sorted(FUND[s])
    while t <= b:
        r = FUND[s].get(t); r = r if r is not None else FUND[s][max(k for k in ks if k <= t)]
        c += r*(1 if side == "long" else -1); t += iv
    return c
TP = {"WY-PULLBACK": 0.80, "WY-REVERT": 0.30}; SL = {"WY-PULLBACK": 2.00, "WY-REVERT": 1.60}
eq = EQ0; peak = EQ0; mdd = 0.0; out = []; skipped = 0; fb = 0
byclose = sorted(trades, key=lambda t: t["close_ts"])
for t in byclose:
    s = t["sym"]; f = inst[s]; step = Decimal(f["lotSizeFilter"]["qtyStep"]); mn = Decimal(f["lotSizeFilter"]["minOrderQty"]); mnn = Decimal(f["lotSizeFilter"].get("minNotionalValue", "5"))
    ent = price_at(s, t["open_ts"]); qty = (Decimal(str(CLIP/ent))/step).to_integral_value(ROUND_DOWN)*step
    lot = max(mn, (mnn/Decimal(str(ent))/step).to_integral_value(ROUND_CEILING)*step)
    if qty < lot:
        planned = float(lot)*ent*(SL[t["strat"]]/100 + (2*5.5+4)/1e4)
        if planned > CAP: skipped += 1; t["skip"] = True; continue
        qty = lot
    q = float(qty); notional = q*ent
    tgt = ent*(1+TP[t["strat"]]/100) if t["side"] == "long" else ent*(1-TP[t["strat"]]/100)
    maker = t["reason"] == "TARGET" and through(s, t["close_ts"], tgt, t["side"])       # resting limit at the target; only counts if Bybit traded through it
    if t["reason"] == "TARGET" and not maker: fb += 1
    ex = tgt if maker else price_at(s, t["close_ts"])                                    # else market exit at the Bybit price at the Yellow exit timestamp
    gross = q*(ex-ent) if t["side"] == "long" else q*(ent-ex)
    fees = notional*TAKER + q*ex*(MAKER if maker else TAKER)
    fund = q*ent*funding(s, t["side"], t["open_ts"], t["close_ts"])
    net = gross - fees - fund; eq += net; peak = max(peak, eq); mdd = max(mdd, peak-eq)
    out.append(dict(ts=t["close_ts"], open_ts=t["open_ts"], sym=s, strat=t["strat"], side=t["side"], ent=ent, ex=ex, notional=notional, gross=gross, fees=fees, fund=fund, net=net, eq=eq, y=t["y_pnl"], reason=t["reason"], maker=maker))
n = len(out); wins = sum(1 for r in out if r["net"] > 0); days = (max(t["close_ts"] for t in trades) - min(t["open_ts"] for t in trades))/86400000
json.dump(out, open(D+f"replay_result_{LEV}x.json", "w"))

byS = {k: sum(r["net"] for r in out if r["strat"] == k) for k in ("WY-PULLBACK", "WY-REVERT")}
# margin availability on the real timeline: equity at each entry = 100 + realized net of trades already closed; used margin = open positions' notional/LEV
ev = sorted([(r["open_ts"], 1, r) for r in out] + [(r["ts"], 0, r) for r in out], key=lambda x: (x[0], x[1]))
used = 0.0; realized = EQ0; viol = 0; maxused = 0.0; minfree = 1e9
for ts_, kind, r in ev:
    m = r["notional"]/LEV
    if kind == 0: used -= m; realized += r["net"]
    else:
        used += m; maxused = max(maxused, used); minfree = min(minfree, realized-used)
        if used > realized: viol += 1
print(json.dumps(dict(lev=LEV, clip=CLIP, n=n, skipped=skipped, end=round(eq,4), net=round(eq-EQ0,4), ret=round((eq-EQ0),2), wins=wins, losses=n-wins, win=round(wins/n*100,1),
      avg=round((eq-EQ0)/n,4), best=round(max(r["net"] for r in out),4), worst=round(min(r["net"] for r in out),4), mdd=round(mdd,4), mdd_pct_start=round(mdd,2), mdd_pct_peak=round(mdd/peak*100,2) if False else None,
      fees=round(sum(r["fees"] for r in out),4), fund=round(sum(r["fund"] for r in out),4), tpd=round(n/days,1), pb=round(byS["WY-PULLBACK"],4), rv=round(byS["WY-REVERT"],4),
      maker=sum(1 for r in out if r["maker"]), margin_violations=viol, max_margin_used=round(maxused,2), min_free_equity=round(minfree,2), yellow_net=round(sum(t["y_pnl"] for t in trades),2))))
# peak-relative drawdown
e = EQ0; pk = EQ0; dd = 0.0
for r in out: e = r["eq"]; pk = max(pk, e); dd = max(dd, (pk-e)/pk*100)
print("max drawdown % of running peak:", round(dd, 2))
