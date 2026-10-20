#!/usr/bin/env python3
"""Deterministic tools and safety kernel for model-driven shadow agents.
These functions are NOT AI agents; the reasoning runtime is in agents.py.
No exchange client, keys or real-order path exists in either module.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import numpy as np

BASE = Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text())
if CFG["mode"] != "SHADOW_ONLY":
    raise RuntimeError("This build supports SHADOW_ONLY; there is no real-money mode")
STATE = Path(os.environ.get("WHY_LAB_STATE", str(BASE / "state")))
SOURCE = Path(CFG["source_dir"])
BAR = CFG["bar_ms"]
FAMILIES = ("momentum", "breakout", "mean_reversion", "range_rejection", "acceleration")
LOOKBACKS = (6, 12, 24, 48, 72, 96)
HORIZONS = (6, 12, 24, 48)
REGIMES = ("long", "sideways", "short")
THRESHOLDS = np.linspace(0.1, 2.3, 12)

def clock_ms(): return int(time.time() * 1000)
def packed(x): return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)

def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE / "lab.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("pragma journal_mode=WAL")
    db.execute("pragma busy_timeout=30000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, ts INTEGER, sender TEXT, recipient TEXT, topic TEXT, body TEXT);
    CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS strategies(id TEXT PRIMARY KEY, spec TEXT, stage TEXT, created_ms INTEGER, metrics TEXT, updated_ms INTEGER);
    CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY, strategy_id TEXT, book TEXT, market TEXT, side TEXT, entry REAL, notional REAL, tp REAL, sl REAL, opened_ms INTEGER, max_hold INTEGER, last_mark REAL, last_mark_ms INTEGER);
    CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, position_id TEXT UNIQUE, strategy_id TEXT, book TEXT, market TEXT, side TEXT, opened_ms INTEGER, closed_ms INTEGER, entry REAL, exit REAL, gross REAL, fees REAL, funding_reserve REAL, pnl REAL, reason TEXT);
    CREATE TABLE IF NOT EXISTS paper_seen(book TEXT, strategy_id TEXT, market TEXT, bar_ms INTEGER, PRIMARY KEY(book,strategy_id,market,bar_ms));
    CREATE INDEX IF NOT EXISTS message_topic ON messages(topic,id);
    CREATE INDEX IF NOT EXISTS trade_strategy ON trades(strategy_id,closed_ms);
    """)
    db.commit()
    return db

def get(db, key, default=None):
    row = db.execute("select value from kv where key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default

def put(db, key, value):
    db.execute("insert or replace into kv values(?,?)", (key, packed(value)))

def message(db, sender, recipient, topic, body):
    db.execute("insert into messages(ts,sender,recipient,topic,body) values(?,?,?,?,?)",
               (clock_ms(), sender, recipient, topic, packed(body)))

def heartbeat(db, agent, **extra):
    put(db, "heartbeat:" + agent, dict(ts=clock_ms(), **extra))
    db.commit()

def ai_authorized(db, role, predicate=None):
    decision=get(db,"ai_decision:"+role,{})
    fresh=0 <= clock_ms()-decision.get("ts",0) <= CFG["ai"]["authorization_seconds"]*1000
    return bool(fresh and (predicate(decision) if predicate else True))

def rolling_mean(x, period):
    out = np.full(len(x), np.nan)
    if len(x) >= period:
        c = np.concatenate(([0.0], np.cumsum(x)))
        out[period-1:] = (c[period:] - c[:-period]) / period
    return out

def load_bars(limit=4000):
    """Only completed, covered bars; retain gaps so validation can reject them."""
    data = {}
    cutoff = clock_ms() // BAR * BAR
    for path in sorted((SOURCE / "mark_candles").glob("*.jsonl")):
        rows = {}
        with path.open() as f:
            for line in f:
                try:
                    r = json.loads(line); b = r["bar"]
                    if int(b[0]) + BAR > cutoff or len(b) < 5: continue
                    a = np.array(b[:5], dtype=float)
                    good = not r.get("partial", True) and r.get("gap_max_s", 999) <= 15
                    if not np.isfinite(a).all() or min(a[1:]) <= 0: continue
                    if a[2] < max(a[1], a[4]) or a[3] > min(a[1], a[4]): continue
                    rows[int(b[0])] = (*a, float(good))
                except (ValueError, TypeError, KeyError): continue
        if len(rows) >= 200:
            data[path.stem] = np.array([rows[t] for t in sorted(rows)[-limit:]], dtype=float)
    return data

def features(bars):
    c = bars[:, 4]; n = len(c)
    ret = np.zeros(n); ret[1:] = np.log(c[1:] / c[:-1]) * 1e4
    vol = np.sqrt(np.maximum(rolling_mean(ret * ret, 24), 1.0))
    z = {}
    for look in LOOKBACKS:
        past = np.full(n, np.nan); past[look:] = c[:-look]
        mean = rolling_mean(c, look)
        std = np.sqrt(np.maximum(rolling_mean(c*c, look) - mean*mean, 1e-12))
        momentum = np.log(c / past) * 1e4 / (vol * math.sqrt(look))
        high = np.full(n, np.nan); low = np.full(n, np.nan)
        for i in range(look, n):
            high[i] = np.max(bars[i-look:i, 2]); low[i] = np.min(bars[i-look:i, 3])
        breakout = np.where(c > high, (c / high - 1)*1e4/vol,
                            np.where(c < low, (c/low-1)*1e4/vol, 0.0))
        reject = (2*c - bars[:, 2] - bars[:, 3]) / np.maximum(bars[:, 2]-bars[:, 3], c*1e-6)
        reject *= np.abs((c-mean)/std)
        lag = np.full(n, np.nan); lag[look:] = momentum[:-look]
        z[("momentum", look)] = momentum
        z[("breakout", look)] = breakout
        z[("mean_reversion", look)] = -(c-mean)/std
        z[("range_rejection", look)] = reject
        z[("acceleration", look)] = momentum-lag
    trend = np.zeros(n); trend[48:] = np.log(c[48:]/c[:-48])*1e4 / np.maximum(vol[48:]*math.sqrt(48), 1)
    regime = np.where(trend > .6, 0, np.where(trend < -.6, 2, 1))
    good = bars[:, 5].astype(bool)
    gaps = np.concatenate(([True], np.diff(bars[:,0]) != BAR)) | ~good
    # Any hole in the longest feature window invalidates the observation.
    bad = np.convolve(gaps.astype(int), np.ones(193,dtype=int), mode="full")[:n] > 0
    usable = good & ~bad
    return dict(signals=z, regime=regime, vol=vol, usable=usable)

def summarize(pnls):
    x = np.array(pnls, dtype=float); n = len(x)
    if not n: return dict(trades=0, pnl=0.0, win_rate=0.0, pf=0.0, expectancy=0.0, t=0.0, max_drawdown=0.0)
    wins=float(x[x>0].sum()); losses=float(-x[x<=0].sum())
    eq=np.concatenate(([0.0],np.cumsum(x)))
    t=float(x.mean() / (x.std(ddof=1) / math.sqrt(n))) if n>1 and x.std(ddof=1)>1e-9 else 0.0
    return dict(trades=n,pnl=round(float(x.sum()),6),win_rate=round(float((x>0).mean()),4),
                pf=round(min(wins/losses if losses else (99.0 if wins else 0.0),99),4),
                expectancy=round(float(x.mean()),6),t=round(t,4),max_drawdown=round(float((np.maximum.accumulate(eq)-eq).max()),6))

def make_spec(base, tp_i, sl_i):
    return dict(family=base[0],lookback=base[1],threshold=round(base[2],6),side=base[3],
                regime=base[4],hold_bars=base[5],tp_bps=15+5*tp_i,sl_bps=15+5*sl_i)

def identity(spec): return hashlib.sha256(packed(spec).encode()).hexdigest()[:20]

def cost_bps(): return 2*CFG["paper"]["taker_bps"]+2*CFG["paper"]["spread_bps"]

def backtest(spec, data, cached, begin, end):
    """Signals at closed bar i; entries at i+1 open; stop first on ambiguous bars.
    Each split starts flat. Trades at gaps are rejected, never bridged.
    """
    result=[]; side=1 if spec["side"]=="long" else -1
    for market,b in data.items():
        f=cached[market]; left=int(len(b)*begin); right=int(len(b)*end)
        i=max(left,193); signal=f["signals"][(spec["family"],spec["lookback"])]
        while i+spec["hold_bars"]+1<right:
            if not f["usable"][i] or f["regime"][i]!=REGIMES.index(spec["regime"]) or side*signal[i]<spec["threshold"]:
                i+=1; continue
            j=i+1; final=j+spec["hold_bars"]-1
            if not np.all(b[j:final+1,5]) or not np.all(np.diff(b[i:final+1,0])==BAR):
                i=final+1; continue
            entry=b[j,1]*(1+side*CFG["paper"]["spread_bps"]/1e4)
            tp=entry*(1+side*spec["tp_bps"]/1e4); sl=entry*(1-side*spec["sl_bps"]/1e4)
            exit_px=b[final,4]; closed=final
            for k in range(j,final+1):
                if side*(b[k,1]-sl)<=0:
                    exit_px=b[k,1];closed=k;break
                stopped=b[k,3]<=sl if side==1 else b[k,2]>=sl
                target=b[k,2]>=tp if side==1 else b[k,3]<=tp
                if stopped: exit_px=sl;closed=k;break
                if target: exit_px=tp;closed=k;break
            exit_px *= 1-side*CFG["paper"]["spread_bps"]/1e4
            gross=side*(exit_px/entry-1)*CFG["paper"]["notional_usd"]
            fee=CFG["paper"]["notional_usd"]*CFG["paper"]["taker_bps"]/1e4*(1+exit_px/entry)
            reserve=CFG["paper"]["notional_usd"]*CFG["paper"]["funding_reserve_bps_per_8h"]/1e4*(closed-j+1)*BAR/28800000
            result.append(gross-fee-reserve); i=closed+1
    return summarize(result)

def build_hist(data, cached):
    bases=[]; hist=[]; counts=[]
    for family in FAMILIES:
      for look in LOOKBACKS:
       for threshold in THRESHOLDS:
        for side in ("long","short"):
         sign=1 if side=="long" else -1
         for reg in REGIMES:
          for horizon in HORIZONS:
            h=np.zeros(65)
            for market,b in data.items():
                f=cached[market]; stop=int(len(b)*CFG["research"]["train_fraction"])
                idx=np.arange(193,max(193,stop-horizon-1))
                if not len(idx):continue
                mask=f["usable"][idx] & f["usable"][idx+horizon] & (f["regime"][idx]==REGIMES.index(reg))
                mask &= sign*f["signals"][(family,look)][idx]>=threshold
                idx=idx[mask]
                if not len(idx):continue
                r=sign*(b[idx+horizon+1,1]/b[idx+1,1]-1)*1e4
                bins=np.clip(np.rint((r+800)/1600*64).astype(int),0,64)
                h+=np.bincount(bins,minlength=65)
            bases.append((family,look,float(threshold),side,reg,horizon));hist.append(h);counts.append(float(h.sum()))
    eligible=[i for i,count in enumerate(counts) if count>=20]
    return [bases[i] for i in eligible],np.ascontiguousarray([hist[i] for i in eligible],dtype=np.float64),np.array([counts[i] for i in eligible],dtype=np.float64)

def load_screen():
    lib=ctypes.CDLL(str(BASE/"screen.so")); fn=lib.screen
    ptr=np.ctypeslib.ndpointer(dtype=np.float64,flags="C_CONTIGUOUS")
    iptr=np.ctypeslib.ndpointer(dtype=np.int64,flags="C_CONTIGUOUS")
    fn.argtypes=[ptr,ptr,ctypes.c_int,ctypes.c_int64,ctypes.c_int64,ctypes.c_int,ctypes.c_double,ptr,ctypes.c_int,iptr,ptr]
    fn.restype=ctypes.c_int64
    return fn

def research_once(db, memo):
    cycle_started=time.perf_counter()
    data=load_bars()
    if len(data)<3: heartbeat(db,"screen_tool",state="waiting_for_covered_candles");return
    fingerprint=hashlib.sha256(packed({k:[len(v),int(v[-1,0])] for k,v in data.items()}).encode()).hexdigest()
    if memo.get("fingerprint")!=fingerprint:
        cached={k:features(v) for k,v in data.items()}
        before=time.perf_counter();bases,hist,counts=build_hist(data,cached)
        memo.update(fingerprint=fingerprint,bases=bases,hist=hist,counts=counts,build_seconds=time.perf_counter()-before)
    bases,hist,counts=memo["bases"],memo["hist"],memo["counts"]
    if not bases:heartbeat(db,"screen_tool",state="insufficient_signal_samples");return
    total=len(bases)*6400;cursor=get(db,"search_cursor",0)%total
    advice=get(db,"family_advice",{});weights=np.array([advice.get(b[0],1.0) for b in bases],dtype=float)
    ids=np.full(96,-1,dtype=np.int64);scores=np.zeros(96)
    # Disperse sequential work across families; preserve a deterministic cursor.
    each=CFG["research"]["permutations_per_cycle"]//len(FAMILIES)
    picks=[];evaluated=0;visited=0;begin=time.perf_counter()
    if "screen" not in memo:memo["screen"]=load_screen()
    for fi,family in enumerate(FAMILIES):
        family_indices=[i for i,b in enumerate(bases) if b[0]==family]
        if not family_indices:continue
        offset=family_indices[0];family_bases=len(family_indices)
        local=(cursor//len(FAMILIES))%(family_bases*6400)
        start=offset*6400+local
        remaining=each
        while remaining:
            requested=min(remaining,(offset+family_bases)*6400-start)
            n=memo["screen"](hist,counts,len(bases),start,requested,65,cost_bps(),weights,96,ids,scores)
            if n<0:raise RuntimeError("screen allocation failed")
            evaluated+=n;visited+=requested;remaining-=requested
            for cid,score in zip(ids[:max(1,CFG["research"]["shortlist"]//5)],scores):
                if cid>=0 and score>0:
                    base=bases[int(cid)//6400];spec=make_spec(base,(int(cid)%6400)//80,int(cid)%80)
                    picks.append((spec,float(score)))
            start=offset*6400
    duration=time.perf_counter()-begin
    cumulative=get(db,"permutations_evaluated",0)+evaluated
    put(db,"search_cursor",cursor+visited);put(db,"permutations_evaluated",cumulative)
    # The numerical tool offers evidence, never autonomously submits a strategy.
    put(db,"screen_shortlist",dict(ts=clock_ms(),dataset=fingerprint,candidates=[dict(id=identity(spec),spec=spec,proxy_score=score) for spec,score in picks]))
    put(db,"research_stats",dict(ts=clock_ms(),permutations_this_cycle=evaluated,total_evaluations=cumulative,
          screen_seconds=round(duration,4),kernel_evaluations_per_minute=round(evaluated/max(duration,.001)*60),
          scheduled_target_per_minute=CFG["research"]["permutations_per_cycle"],histogram_build_seconds=round(memo["build_seconds"],3),
          visited_this_cycle=visited,cycle_wall_seconds=round(time.perf_counter()-cycle_started,4),
          cycle_scored_per_minute=round(evaluated/max(time.perf_counter()-cycle_started,.001)*60),
          candidates_shortlisted=len(picks),dataset=fingerprint,markets=len(data),bars=sum(len(v) for v in data.values()),
          eligible_parameter_space=total,
          meaning="proxy screens; exact path simulations and forward paper results are tracked separately"))
    heartbeat(db,"screen_tool",state="screening",evaluations=cumulative)

def validate_candidates(db,data,cached):
    rows=db.execute("select * from strategies where stage='RESEARCH' order by created_ms limit 8").fetchall()
    a=CFG["research"]["train_fraction"];b=a+CFG["research"]["validation_fraction"]
    for row in rows:
        spec=json.loads(row["spec"])
        metrics=json.loads(row["metrics"])
        train=backtest(spec,data,cached,0,a);valid=backtest(spec,data,cached,a,b);holdout=backtest(spec,data,cached,b,1)
        # Historical holdout is exploratory. Independent forward gate controls promotion.
        accepted=train["trades"]>=20 and train["pnl"]>0 and valid["trades"]>=8 and valid["pf"]>=1.05 and holdout["trades"]>=5 and holdout["pnl"]>0
        metrics.update(train=train,validation=valid,holdout=holdout,holdout_kind="exploratory_multiple_testing",forward_start_ms=clock_ms())
        stage="NEEDS_CRITIC" if accepted else "REJECTED"
        db.execute("update strategies set stage=?,metrics=?,updated_ms=? where id=?",(stage,packed(metrics),clock_ms(),row["id"]))
        message(db,"validation_tool","critic" if accepted else "research","validation_evidence",dict(id=row["id"],stage=stage,train=train,validation=valid,
                advice="collect independent forward evidence" if accepted else "reject weak chronological validation"))
        # Holdout results intentionally never returned to research as a tuning signal.
    heartbeat(db,"validation_tool",validated=len(rows))

def fit_direction(data,cached):
    """Learn long/sideways/short from past features and resolved future labels.
    A market-wide median prevents a single coin dominating the direction read.
    """
    common=set.intersection(*(set(v[:,0].astype(int)) for v in data.values()))
    timeline=sorted(common)
    if len(timeline)<240:return dict(ts=clock_ms(),regime="sideways",confidence=0.0,reason="insufficient_history",stale=False)
    matrices=[];prices=[];valids=[]
    for market,b in data.items():
        index={int(t):i for i,t in enumerate(b[:,0])};ix=np.array([index[t] for t in timeline])
        f=cached[market]
        m=np.column_stack([f["signals"][("momentum",look)][ix] for look in (6,24,48)])
        matrices.append(m);prices.append(b[ix,4]);valids.append(f["usable"][ix])
    x=np.median(np.array(matrices),axis=0);prices=np.array(prices)
    usable=np.all(np.array(valids),axis=0)&np.isfinite(x).all(axis=1)
    h=12;n=len(timeline);returns=np.median((prices[:,h:]/prices[:,:-h]-1)*1e4,axis=0)
    # Missing wall-clock intervals cannot become training labels.
    contiguous=np.array([timeline[i+h]-timeline[i]==h*BAR for i in range(n-h)])
    labels=np.where(returns>25,0,np.where(returns< -25,2,1))
    train_x=x[:-h][usable[:-h]&usable[h:]&contiguous];train_y=labels[usable[:-h]&usable[h:]&contiguous]
    if len(train_x)<120 or not usable[-1]:return dict(ts=clock_ms(),regime="sideways",confidence=0.0,reason="thin_or_gapped_history",stale=False)
    probs=[];details={};cut=int(len(train_x)*.8)
    # Evaluate recent chronological accuracy before refitting all resolved labels.
    def likelihood(z,tx,ty):
        out=[]
        for c in range(3):
            part=tx[ty==c]
            if len(part)<5:out.append(np.full(len(z),-1e6));continue
            mu=part.mean(axis=0);var=np.maximum(part.var(axis=0),.03)
            out.append(-.5*np.sum(np.log(2*np.pi*var)+(z-mu)**2/var,axis=1)+math.log(len(part)/len(tx)))
        log=np.stack(out,axis=1);log-=log.max(axis=1,keepdims=True)
        p=np.exp(log);return p/p.sum(axis=1,keepdims=True)
    accuracy=float((likelihood(train_x[cut:],train_x[:cut],train_y[:cut]).argmax(axis=1)==train_y[cut:]).mean())
    p=likelihood(x[-1:],train_x,train_y)[0];raw=int(p.argmax());conf=float(p[raw])
    # Also gate by observed agreement across markets and medium-term trend.
    breadth=float((np.array(matrices)[:,-1,1]>0).mean())
    if conf<CFG["direction"]["minimum_confidence"]:raw=1
    return dict(ts=clock_ms(),bar_ms=int(timeline[-1]),regime=REGIMES[raw],confidence=round(conf,4),
                probabilities={REGIMES[j]:round(float(p[j]),4) for j in range(3)},breadth_long=round(breadth,4),
                training_labels=len(train_x),chronological_accuracy=round(accuracy,4),
                model="three_class_gaussian_naive_bayes",reason="learned from past resolved one-hour returns",stale=False)

def read_quotes():
    """No HTTP or order methods. Read the recorder's atomic public-data snapshot."""
    try:
        snapshot=json.loads((SOURCE/"snapshot.json").read_text());now=clock_ms()
        if now-snapshot.get("ts",0)>15000 or not snapshot.get("feed",{}).get("connected"):return {},"stale_or_disconnected_snapshot"
        quotes={}
        for market,forming in snapshot.get("forming",{}).items():
            if not forming:continue
            ts=forming.get("last_ms",0);price=forming.get("c",0)
            if 0<=now-ts<=CFG["paper"]["max_quote_age_seconds"]*1000 and price>0:quotes[market]=(float(price),int(ts))
        return quotes,"ok" if quotes else "no_fresh_quotes"
    except (OSError,ValueError,TypeError):return {},"source_unavailable"

def open_paper(db,sid,spec,book,market,quote):
    price,ts=quote;sign=1 if spec["side"]=="long" else -1
    entry=price*(1+sign*CFG["paper"]["spread_bps"]/1e4)
    pid=hashlib.sha256(f"{book}:{sid}:{market}:{ts}".encode()).hexdigest()[:24]
    db.execute("insert into positions values(?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,sid,book,market,spec["side"],entry,CFG["paper"]["notional_usd"],
        entry*(1+sign*spec["tp_bps"]/1e4),entry*(1-sign*spec["sl_bps"]/1e4),ts,spec["hold_bars"],price,ts))
    message(db,"paper","feedback","paper_entry",dict(position=pid,strategy=sid,book=book,market=market,side=spec["side"],entry=entry,mode="SIMULATED"))

def close_paper(db,p,price,ts,reason):
    if not db.execute("select 1 from positions where id=?",(p["id"],)).fetchone():return
    sign=1 if p["side"]=="long" else -1
    exit_px=price*(1-sign*CFG["paper"]["spread_bps"]/1e4)
    gross=sign*(exit_px/p["entry"]-1)*p["notional"]
    fees=p["notional"]*CFG["paper"]["taker_bps"]/1e4*(1+exit_px/p["entry"])
    reserve=p["notional"]*CFG["paper"]["funding_reserve_bps_per_8h"]/1e4*max(0,ts-p["opened_ms"])/28800000
    pnl=gross-fees-reserve
    db.execute("insert or ignore into trades(position_id,strategy_id,book,market,side,opened_ms,closed_ms,entry,exit,gross,fees,funding_reserve,pnl,reason) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (p["id"],p["strategy_id"],p["book"],p["market"],p["side"],p["opened_ms"],ts,p["entry"],exit_px,gross,fees,reserve,pnl,reason))
    db.execute("delete from positions where id=?",(p["id"],))
    db.execute("delete from kv where key=?",("bar_cursor:"+p["id"],))
    message(db,"paper","feedback","paper_close",dict(strategy=p["strategy_id"],market=p["market"],book=p["book"],pnl=round(pnl,6),reason=reason))

def paper_cycle(db,data,cached,direction,quotes):
    for p in db.execute("select * from positions").fetchall():
        if p["market"] not in quotes:continue
        price,ts=quotes[p["market"]]
        if ts<=p["last_mark_ms"]:continue
        sign=1 if p["side"]=="long" else -1
        stage=db.execute("select stage from strategies where id=?",(p["strategy_id"],)).fetchone()
        if stage and stage[0]=="RETIRED":
            close_paper(db,p,price,ts,"RETIRED_RISK_EXIT");continue
        # Recover barriers missed between polls using fully covered completed bars.
        # The partial entry bar is excluded: pre-entry extrema cannot be reused.
        cursor=get(db,"bar_cursor:"+p["id"],p["opened_ms"]//BAR*BAR)
        recovered=False
        bars=data.get(p["market"],[])
        for b in bars:
            bar_ts=int(b[0])
            if bar_ts<=cursor or bar_ts<p["opened_ms"] or bar_ts+BAR>ts:continue
            if not b[5]:continue
            cursor=bar_ts
            stopped=b[3]<=p["sl"] if sign==1 else b[2]>=p["sl"]
            target=b[2]>=p["tp"] if sign==1 else b[3]<=p["tp"]
            if stopped or target:
                fill=(min(b[1],p["sl"]) if sign==1 else max(b[1],p["sl"])) if stopped else p["tp"]
                close_paper(db,p,float(fill),bar_ts+BAR,"STOP_CANDLE_RECONCILIATION" if stopped else "TARGET_CANDLE_RECONCILIATION")
                recovered=True;break
        if recovered:continue
        put(db,"bar_cursor:"+p["id"],cursor)
        stopped=sign*(price-p["sl"])<=0;target=sign*(price-p["tp"])>=0;timeout=ts-p["opened_ms"]>=p["max_hold"]*BAR
        if stopped or target or timeout:
            # Do not award a better-than-target fill when quotes jump past a limit.
            close_paper(db,p,p["tp"] if target and not stopped else price,ts,"STOP" if stopped else "TARGET" if target else "TIMEOUT")
        else:db.execute("update positions set last_mark=?,last_mark_ms=? where id=?",(price,ts,p["id"]))
    # All pending/open decisions require a fresh direction read and quote.
    if (not quotes or clock_ms()-direction.get("ts",0)>150000 or direction.get("stale",True)
            or direction.get("confidence",0)<CFG["direction"]["minimum_confidence"]
            or not ai_authorized(db,"direction") or not ai_authorized(db,"paper",lambda a:not a.get("pause",True))):
        heartbeat(db,"paper_tool",state="fresh_ai_authorization_and_data_required");return
    rows=db.execute("select * from strategies where stage in ('PAPER','LIVE_PAPER')").fetchall()
    now=clock_ms();day=now//86400000*86400000
    for stage,book in (("PAPER","research_paper"),("LIVE_PAPER","live_paper")):
        daily=db.execute("select coalesce(sum(pnl),0) from trades where book=? and closed_ms>=?",(book,day)).fetchone()[0]
        if daily<=-CFG["paper"]["max_daily_loss_usd"]:continue
        positions=db.execute("select * from positions where book=?",(book,)).fetchall()
        if daily+sum(mark_net(p,quotes.get(p["market"],(p["last_mark"],p["last_mark_ms"]))) for p in positions)<=-CFG["paper"]["max_daily_loss_usd"]:continue
        open_markets={p["market"] for p in positions};families={}
        for p in positions:
            st=db.execute("select spec from strategies where id=?",(p["strategy_id"],)).fetchone()
            if st:
                fam=json.loads(st[0])["family"];families[fam]=families.get(fam,0)+1
        # Rotate equal-risk candidates: rank evidence, never simply insertion order.
        eligible=[r for r in rows if r["stage"]==stage]
        eligible.sort(key=lambda r:json.loads(r["metrics"]).get("validation",{}).get("t",0),reverse=True)
        for row in eligible:
            spec=json.loads(row["spec"]);sid=row["id"]
            approval=get(db,"ai_decision:paper",{})
            if sid not in approval.get("strategy_ids",[]) or book not in approval.get("books",[]):continue
            if spec["regime"]!=direction["regime"]:continue
            if direction["regime"]!="sideways" and spec["side"]!=direction["regime"]:continue
            if len(open_markets)>=CFG["paper"]["max_open"] or families.get(spec["family"],0)>=CFG["paper"]["max_per_family"]:continue
            for market,b in data.items():
                t=int(b[-1,0]);f=cached[market]
                if market not in quotes or market in open_markets or now-(t+BAR)>BAR:continue
                seen=db.execute("select 1 from paper_seen where book=? and strategy_id=? and market=? and bar_ms=?",(book,sid,market,t)).fetchone()
                if seen:continue
                # Seen is recorded even when no signal, so restarts cannot replay a bar.
                db.execute("insert into paper_seen values(?,?,?,?)",(book,sid,market,t))
                sign=1 if spec["side"]=="long" else -1
                if not f["usable"][-1] or f["regime"][-1]!=REGIMES.index(spec["regime"]) or sign*f["signals"][(spec["family"],spec["lookback"])][-1]<spec["threshold"]:continue
                open_paper(db,sid,spec,book,market,quotes[market]);open_markets.add(market)
                families[spec["family"]]=families.get(spec["family"],0)+1
                if len(open_markets)>=CFG["paper"]["max_open"] or families[spec["family"]]>=CFG["paper"]["max_per_family"]:break
    heartbeat(db,"paper_tool",state="forward_paper",quotes=len(quotes))

def forward_metrics(db,sid,book="research_paper"):
    rows=db.execute("select * from trades where strategy_id=? and book=? order by closed_ms",(sid,book)).fetchall()
    metrics=summarize([r["pnl"] for r in rows])
    metrics["days"]=len({r["closed_ms"]//86400000 for r in rows})
    return metrics

def daily_correlation(db,left,right):
    # Use every calendar day in the union, including no-trade days.
    rows=db.execute("select strategy_id,closed_ms/86400000 as day,sum(pnl) as pnl from trades where strategy_id in (?,?) and book='research_paper' group by strategy_id,day",(left,right)).fetchall()
    series={left:{},right:{}}
    for r in rows:series[r["strategy_id"]][r["day"]]=r["pnl"]
    days=sorted(set(series[left])|set(series[right]))
    if len(days)<10:return None
    a=np.array([series[left].get(d,0) for d in days]);b=np.array([series[right].get(d,0) for d in days])
    if a.std()<1e-9 or b.std()<1e-9:return None
    return float(np.corrcoef(a,b)[0,1])

def promotion_cycle(db):
    rules=CFG["promotion"];now=clock_ms()
    for row in db.execute("select * from strategies where stage in ('PAPER','LIVE_PAPER')").fetchall():
        met=json.loads(row["metrics"]);fwd=forward_metrics(db,row["id"]);met["forward"]=fwd
        age=(now-met.get("forward_start_ms",row["created_ms"]))/86400000
        stage=row["stage"];reasons=[]
        if fwd["pnl"]<=-CFG["paper"]["max_strategy_loss_usd"]:stage="RETIRED";reasons=["strategy_loss_limit"]
        elif stage=="LIVE_PAPER":
            promoted=forward_metrics(db,row["id"],"live_paper");met["live_paper"]=promoted
            if promoted["pnl"]<=-CFG["paper"]["max_strategy_loss_usd"] or promoted["max_drawdown"]>rules["maximum_drawdown_usd"]:
                stage="RETIRED";reasons=["live_paper_risk_limit"]
        else:
            if age<rules["minimum_days"]:reasons.append("minimum_forward_age")
            if fwd["days"]<rules["minimum_days"]:reasons.append("minimum_observed_trading_days")
            if fwd["trades"]<rules["minimum_trades"]:reasons.append("minimum_forward_trades")
            if fwd["pf"]<rules["minimum_profit_factor"]:reasons.append("forward_profit_factor")
            if fwd["t"]<rules["minimum_expectancy_t"]:reasons.append("forward_expectancy_uncertain")
            if fwd["max_drawdown"]>rules["maximum_drawdown_usd"]:reasons.append("forward_drawdown")
            active=db.execute("select * from strategies where stage='LIVE_PAPER'").fetchall()
            if len(active)>=rules["max_active"]:reasons.append("promoted_book_full")
            for other in active:
                if json.loads(other["spec"])["family"]==json.loads(row["spec"])["family"]:reasons.append("family_already_represented")
                corr=daily_correlation(db,row["id"],other["id"])
                if corr is None or abs(corr)>rules["maximum_abs_correlation"]:reasons.append("correlation_unproven_or_high")
            if not reasons and not ai_authorized(db,"promotion",lambda a:row["id"] in a.get("strategy_ids",[])):
                reasons.append("ai_promotion_review_required")
            if not reasons:
                stage="LIVE_PAPER";met["promoted_ms"]=now
                message(db,"promotion_gate","paper","promote_live_paper",dict(id=row["id"],forward=fwd,real_orders=False))
        met["promotion_blockers"]=sorted(set(reasons))
        if stage!=row["stage"]:
            message(db,"promotion_gate","research","lifecycle_change",dict(id=row["id"],from_stage=row["stage"],to_stage=stage,reasons=reasons))
        db.execute("update strategies set stage=?,metrics=?,updated_ms=? where id=?",(stage,packed(met),now,row["id"]))
    heartbeat(db,"promotion_gate",state="live_paper_only",real_orders=False)

def feedback_cycle(db):
    weights={};evidence=[]
    for family in FAMILIES:
        rows=db.execute("select * from strategies").fetchall();valid=[];forward=[]
        for row in rows:
            spec=json.loads(row["spec"]);met=json.loads(row["metrics"])
            if spec["family"]!=family:continue
            if "validation" in met:valid.append(met["validation"]["t"])
            f=met.get("forward",{})
            if f.get("trades",0)>=10:forward.append(f.get("t",0))
        signal=float(np.mean(forward)) if forward else float(np.mean(valid)) if valid else 0.0
        # Adaptive allocation includes exploration floor; no test/holdout reward leakage.
        weights[family]=round(float(np.clip(1+signal*.1,.5,1.5)),4)
        evidence.append(dict(family=family,reward=round(signal,4),validation_samples=len(valid),forward_samples=len(forward)))
    put(db,"feedback_evidence",dict(weights=weights,evidence=evidence,holdout_used=False))
    message(db,"feedback_tool","feedback","performance_evidence",dict(weights=weights,evidence=evidence,holdout_used=False))
    heartbeat(db,"feedback_tool",families=len(weights))

def github_shadow_status():
    """Separate owner-authorized benchmark books; never eligible for auto-promotion.
    The publisher only reads isolated Freqtrade dry-run ledgers. No registry writes.
    """
    try:
        raw=json.loads((STATE / "github_shadow.json").read_text())
        if raw.get("mode")!="EXTERNAL_STRATEGY_DRY_RUN" or raw.get("live_promotion_allowed") is not False:
            return {"state":"invalid_report","live_promotion_allowed":False}
        age=clock_ms()-int(raw.get("ts",0))
        rows=[]
        for row in raw.get("strategies",[])[:6]:
            rows.append({k:row[k] for k in ("name","state","heartbeat_age_s","timeframe","pairs","analyzed_pairs",
                "closed_trades","open_trades","realized_pnl","profit_factor","realized_max_drawdown","read_status") if k in row})
        return dict(state="fresh" if 0<=age<=120000 else "stale",ts=raw.get("ts"),
                    mode="EXTERNAL_STRATEGY_DRY_RUN",live_promotion_allowed=False,
                    venue="Bybit public data",strategies=rows,
                    note="Separate native Freqtrade simulated fills at 1x. Realized P&L excludes open positions. "
                         "Not AI-approved or profitability-certified; not comparable directly to mark-price books.")
    except (OSError,ValueError,TypeError,KeyError):
        return {"state":"unavailable","live_promotion_allowed":False}

def status(db):
    stages={r[0]:r[1] for r in db.execute("select stage,count(*) from strategies group by stage")}
    books={}
    for book in ("research_paper","live_paper"):
        closed=db.execute("select pnl from trades where book=? order by closed_ms",(book,)).fetchall()
        positions=db.execute("select * from positions where book=?",(book,)).fetchall()
        unreal=sum(mark_net(p,(p["last_mark"],p["last_mark_ms"])) for p in positions)
        summary=summarize([r[0] for r in closed]);summary.update(open=len(positions),unrealized=round(unreal,6),equity=round(CFG["paper"]["equity0"]+summary["pnl"]+unreal,6))
        summary["stale_marks"]=sum(clock_ms()-p["last_mark_ms"]>CFG["paper"]["max_quote_age_seconds"]*1000 for p in positions)
        books[book]=summary
    return dict(mode="SHADOW_ONLY",real_order_capability=False,ts=clock_ms(),stages=stages,books=books,
                direction=get(db,"direction",{}),research=get(db,"research_stats",{}),
                source=get(db,"source_health",{}),heartbeats={r[0].split(":",1)[1]:json.loads(r[1]) for r in db.execute("select key,value from kv where key like 'heartbeat:%'")},
                ai=get(db,"ai_runtime",dict(state="not_started",model_connected=False)),
                model_usage=get(db,"model_usage",{}),
                provider_limits=get(db,"provider_limits",{}),provider_cooldown=get(db,"provider_cooldown",{}),
                model_error=get(db,"model_last_error",{}),github_shadow=github_shadow_status(),
                recent_messages=[dict(r,body=json.loads(r["body"])) for r in db.execute("select * from messages order by id desc limit 20")])

def mark_net(p,quote):
    price,ts=quote;sign=1 if p["side"]=="long" else -1
    exit_px=price*(1-sign*CFG["paper"]["spread_bps"]/1e4)
    gross=sign*(exit_px/p["entry"]-1)*p["notional"]
    fees=p["notional"]*CFG["paper"]["taker_bps"]/1e4*(1+exit_px/p["entry"])
    reserve=p["notional"]*CFG["paper"]["funding_reserve_bps_per_8h"]/1e4*max(0,ts-p["opened_ms"])/28800000
    return gross-fees-reserve

PAGE="""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>WHY Shadow Lab</title><style>body{font:15px system-ui;margin:32px auto;max-width:1050px;padding:0 20px;background:#10141a;color:#e9eef6}h1{font-size:26px}.tag{color:#f2bb62}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1b2330;padding:18px;border-radius:12px}a{color:#85b3ff}</style></head><body><h1>WHY Shadow Lab</h1><p class="tag">Simulation only · AI agents require a configured model · no real orders</p><p>Research AI → Critic AI → Direction AI → Paper AI → Promotion AI to Live Paper → Feedback AI</p><p>Numerical search/backtest tools are NOT agents. Proxy screen counts are separate from AI decisions, exact backtests and observed forward results.</p><pre id="summary">Loading…</pre><h2>Agent conversation</h2><pre id="conversation"></pre><script>async function tick(){let s=await(await fetch('/api/status',{cache:'no-store'})).json();let m=s.recent_messages;delete s.recent_messages;document.getElementById('summary').textContent=JSON.stringify(s,null,2);document.getElementById('conversation').textContent=m.map(x=>`${new Date(x.ts).toISOString()} ${x.sender} → ${x.recipient}: ${x.topic}\\n${JSON.stringify(x.body)}`).join('\\n\\n')}tick();setInterval(tick,10000)</script></body></html>"""

def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/","/api/status","/health"):
                self.send_error(404);return
            db=connect()
            try:payload=PAGE if self.path=="/" else packed(status(db))
            finally:db.close()
            content=payload.encode();self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8" if self.path=="/" else "application/json")
            self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(content)))
            self.end_headers();self.wfile.write(content)
        def log_message(self,*args):pass
    ThreadingHTTPServer(("127.0.0.1",CFG["dashboard_port"]),Handler).serve_forever()

def research_loop(once=False):
    db=connect();memo={}
    while True:
        before=time.time()
        try:research_once(db,memo)
        except Exception as e:
            db.rollback();traceback.print_exc();heartbeat(db,"screen_tool",state="error",error=str(e)[:200])
        if once:break
        time.sleep(max(1,CFG["research"]["cycle_seconds"]-(time.time()-before)))

def runner_loop(once=False):
    db=connect();data={};cached={};last_data=last_direction=last_agents=0;direction=get(db,"direction",{})
    if not once:Thread(target=serve,daemon=True).start()
    while True:
        try:
            now=time.time()
            if now-last_data>=60:
                data=load_bars();cached={k:features(v) for k,v in data.items()};last_data=now
            if len(data)>=3 and now-last_direction>=CFG["direction"]["refresh_seconds"]:
                evidence=fit_direction(data,cached);put(db,"direction_evidence",evidence)
                heartbeat(db,"direction_statistics_tool",regime=evidence["regime"]);last_direction=now
            if data and now-last_agents>=60:
                validate_candidates(db,data,cached);promotion_cycle(db);feedback_cycle(db);last_agents=now
            quotes,health=read_quotes();put(db,"source_health",dict(ts=clock_ms(),state=health,fresh_quotes=len(quotes)))
            direction=get(db,"direction",{})
            if data:paper_cycle(db,data,cached,direction,quotes)
            db.commit()
            heartbeat(db,"supervisor",mode="SHADOW_ONLY",markets=len(data))
        except Exception as e:
            db.rollback();traceback.print_exc();heartbeat(db,"supervisor",state="error",error=str(e)[:200])
        if once:break
        time.sleep(CFG["paper"]["poll_seconds"])

def benchmark():
    db=connect();memo={};research_once(db,memo)
    print(json.dumps(get(db,"research_stats",{}),indent=2))

def main():
    parser=argparse.ArgumentParser();parser.add_argument("command",choices=("research","run","status","benchmark"));parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    if args.command=="research":research_loop(args.once)
    elif args.command=="run":runner_loop(args.once)
    elif args.command=="benchmark":benchmark()
    else:
        db=connect();print(json.dumps(status(db),indent=2));db.close()

if __name__=="__main__":main()
