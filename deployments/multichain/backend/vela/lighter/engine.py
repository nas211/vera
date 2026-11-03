"""Depth-based execution on the existing Vela ledger and universal stop module.

Every mutation is local SQLite. There is intentionally no exchange writer or signer.
"""
import hashlib
import json
import re
import time
import uuid
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING

from ..db import get, put, transaction, packed
from ..paper import PaperAdapter, ExecutionAdapter, money, up_money, MICRO
from ..reuse import loss_stop
from .feed import decimal, SYMBOLS


def identity(token):
    if not isinstance(token,str) or not re.fullmatch(r"[a-f0-9]{64}",token):
        raise ValueError("Paper session required")
    return "paper:" + hashlib.sha256(token.encode()).hexdigest()


def rounded(value, tick, up=False):
    return (value/tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*tick


class LighterLiveAdapter(ExecutionAdapter):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("LIVE disabled. Authenticated execution is not implemented.")
    def open(self, *args): raise NotImplementedError
    def close(self, *args): raise NotImplementedError
    def getPosition(self, *args): raise NotImplementedError
    def getPrice(self, *args): raise NotImplementedError


class LighterPaperAdapter(PaperAdapter):
    def __init__(self, db, settings, feed):
        if settings.mode != "PAPER":
            raise ValueError("LIVE disabled")
        super().__init__(db, settings, on_chain=False)
        self.feed = feed
        db.executescript("""
        CREATE TABLE IF NOT EXISTS paper_orders(id TEXT PRIMARY KEY,user TEXT,session TEXT,
          client_id TEXT,created REAL,due REAL,status TEXT,payload TEXT,result TEXT,error TEXT,
          UNIQUE(user,client_id));
        CREATE TABLE IF NOT EXISTS paper_costs(id INTEGER PRIMARY KEY,user TEXT,session TEXT,
          position_id TEXT,ts REAL,kind TEXT,amount TEXT);
        CREATE TABLE IF NOT EXISTS paper_activity(id INTEGER PRIMARY KEY,user TEXT,ts REAL,kind TEXT,detail TEXT);
        CREATE INDEX IF NOT EXISTS paper_orders_status ON paper_orders(status,due);
        CREATE INDEX IF NOT EXISTS paper_costs_session ON paper_costs(session);
        CREATE INDEX IF NOT EXISTS paper_activity_user ON paper_activity(user,id);
        """)

    def account(self, token):
        user = identity(token)
        with transaction(self.db):
            row = self.db.execute("SELECT * FROM users WHERE address=?",(user,)).fetchone()
            if row:
                return dict(row)
            if self.db.execute("SELECT count(*) FROM users").fetchone()[0] >= 500:
                raise ValueError("Showcase account capacity reached")
            self.db.execute("""INSERT INTO users(address,session,strategy,status,initial,balance,doge_in,started,start_tx)
              VALUES (?,?,1,'active',?,?,'0',?,'paper')""",(user,uuid.uuid4().hex,
              str(self.settings.initial_balance),str(self.settings.initial_balance),time.time()))
            put(self.db,"paper-prefs:"+user,dict(autonomous=False,symbol="BTC/USD",leverage="2"))
        return dict(self.db.execute("SELECT * FROM users WHERE address=?",(user,)).fetchone())

    def activity(self,user,kind,detail):
        self.db.execute("INSERT INTO paper_activity(user,ts,kind,detail) VALUES (?,?,?,?)",(user,time.time(),kind,detail))

    def pause_entries(self,user):
        prefs=get(self.db,"paper-prefs:"+user,{})
        put(self.db,"paper-prefs:"+user,dict(prefs,autonomous=False,generation=prefs.get("generation",0)+1))
        put(self.db,"ai-approved:"+user,None)
        put(self.db,"ai-stage:"+user,{})
        for row in self.db.execute("SELECT id,payload FROM paper_orders WHERE user=? AND status='pending'",(user,)).fetchall():
            if not json.loads(row["payload"]).get("reduceOnly"):
                self.db.execute("UPDATE paper_orders SET status='cancelled' WHERE id=?",(row["id"],))
        self.activity(user,"agent_paused","New entries stopped; existing positions remain protected")

    def market(self,symbol):
        m = self.feed.markets.get(symbol)
        if m is None or not m.fresh():
            raise ValueError("Fresh market price, funding and sequenced depth required")
        return m

    def getPrice(self,symbol):
        return self.market(symbol).mark

    def meta(self,p):
        return get(self.db,"paper-position:"+p["id"],{})

    def protect(self,p):
        m = self.feed.markets.get(p["symbol"])
        if not m:
            raise ValueError("Market specification unavailable")
        meta = self.meta(p)
        exit_fee = max(self.settings.taker_fee+self.settings.platform_fee,D(meta.get("exitFee","0")))
        budget=min(D(2),self.settings.loss_budget,D(meta.get("riskBudget","2")))
        if budget<=MICRO:
            raise ValueError("Risk budget is below accounting precision")
        return loss_stop.stop_price(entry=p["entry"],qty=p["qty"],multiplier=1,direction=p["side"],
            paid_fees=p["paid_fees"],realized_gross=p["realized_gross"],exit_fee=exit_fee,
            strategy_stop=p["stop"],tick=m.tick,configured=budget-MICRO,
            previous_stop=p["stop"])

    def costs(self,user,pid,**values):
        totals=self.cost_totals(user["address"])
        for kind,amount in values.items():
            self.db.execute("INSERT INTO paper_costs(user,session,position_id,ts,kind,amount) VALUES (?,?,?,?,?,?)",
                            (user["address"],user["session"],pid,time.time(),kind,str(amount)))
            totals[kind]=str(D(totals.get(kind,"0"))+amount)
        put(self.db,"paper-cost-totals:"+user["address"],totals)

    def cost_totals(self,user):
        totals=get(self.db,"paper-cost-totals:"+user)
        if totals is None:
            totals={}
            for row in self.db.execute("SELECT kind,amount FROM paper_costs WHERE user=?",(user,)):
                totals[row["kind"]]=str(D(totals.get(row["kind"],"0"))+D(row["amount"]))
        return totals

    def execution(self,m,side,qty,limit=None,maker=False):
        if not m.fresh():
            raise ValueError("Stale market data")
        remaining, total = qty, D(0)
        levels = m.levels(side)
        for price,size in levels:
            amount = min(size,remaining)
            total += price*amount
            remaining -= amount
            if remaining == 0:
                break
        if remaining > 0:
            raise ValueError("Insufficient visible orderbook depth")
        sign = D(1) if side == "buy" else D(-1)
        mid = (max(m.bids)+min(m.asks))/2
        vwap = total/qty
        raw = vwap*(1+sign*(self.settings.slippage+self.settings.spread))
        price = rounded(raw,m.tick,side=="buy")
        if limit is not None:
            if (price-limit)*sign > 0:
                raise ValueError("Limit waiting for executable depth")
            # Conservative maker assumption: no queue priority or price improvement claim.
            if maker:
                price = limit
        spread = max(D(0),(levels[0][0]-mid)*sign*qty) + vwap*qty*self.settings.spread
        impact = max(D(0),(vwap-levels[0][0])*sign*qty)
        slip = max(D(0),(price-vwap)*sign*qty-vwap*qty*self.settings.spread)
        return price, dict(spread=money(spread),depthImpact=money(impact),slippage=money(slip))

    def submit(self,user,payload,client_id):
        if not re.fullmatch(r"[a-zA-Z0-9:_-]{1,120}",client_id):
            raise ValueError("Invalid order identifier")
        prior=self.db.execute("SELECT * FROM paper_orders WHERE user=? AND client_id=?",(user,client_id)).fetchone()
        if prior:
            return dict(prior)
        protective=payload.get("reduceOnly") and payload.get("reason") in ("stop","target","liquidation","timeout","invalid_persisted_risk")
        if not protective and self.db.execute("SELECT count(*) FROM paper_orders WHERE user=? AND created>?",(user,time.time()-60)).fetchone()[0]>=30:
            raise ValueError("Order rate limit; retry shortly")
        payload=dict(payload)
        if payload.get("reduceOnly"):
            p=self.db.execute("SELECT * FROM positions WHERE id=? AND user=?",(payload.get("positionId"),user)).fetchone()
            if not p:
                raise ValueError("Position not found")
            fraction=decimal(payload.get("fraction",1),True)
            if fraction>1:
                raise ValueError("Invalid close fraction")
            payload.update(symbol=p["symbol"],side="short" if p["side"]=="long" else "long",type="market",fraction=str(fraction))
        else:
            if payload.get("symbol") not in SYMBOLS or payload.get("side") not in ("long","short") or payload.get("type") not in ("market","limit"):
                raise ValueError("Invalid paper order")
            leverage=decimal(payload.get("leverage",1),True)
            if not 1<=leverage<=min(D(3),self.settings.max_leverage):
                raise ValueError("Leverage exceeds Vela risk limit")
            notional=decimal(payload.get("notional",100),True)
            if not 10<=notional<=100000:
                raise ValueError("Notional must be between $10 and $100,000")
            for name,default in (("stopBps",50),("targetBps",75)):
                val=decimal(payload.get(name,default),True)
                if not 1<=val<=5000:
                    raise ValueError("Invalid stop/target distance")
                payload[name]=str(val)
            budget=loss_stop.loss_limit(decimal(payload.get("riskBudget",self.settings.loss_budget),True))
            payload.update(leverage=str(leverage),notional=str(notional),riskBudget=str(min(budget,self.settings.loss_budget)))
            if any(p["symbol"]==payload["symbol"] for p in self.getPosition(user)):
                raise ValueError("Close the existing position in this market first")
        m=self.market(payload["symbol"])
        if payload.get("reduceOnly") and rounded(D(p["qty"])*D(payload["fraction"]),m.step)<=0:
            raise ValueError("Partial close is below size precision")
        if protective:
            for row in self.db.execute("SELECT id,payload FROM paper_orders WHERE user=? AND status='pending'",(user,)).fetchall():
                other=json.loads(row["payload"])
                if other.get("positionId")==payload["positionId"]:
                    self.db.execute("UPDATE paper_orders SET status='cancelled',error='Superseded by risk exit' WHERE id=?",(row["id"],))
        pending=[json.loads(r[0]) for r in self.db.execute("SELECT payload FROM paper_orders WHERE user=? AND status='pending'",(user,))]
        if any(o["symbol"]==payload["symbol"] and bool(o.get("reduceOnly"))==bool(payload.get("reduceOnly")) for o in pending):
            raise ValueError("An order for this market is already pending")
        if payload["type"]=="limit":
            limit=decimal(payload.get("limitPrice"),True)
            if not m.tick<=limit<=D("1000000000"):
                raise ValueError("Limit price outside supported range")
            if limit % m.tick:
                raise ValueError("Limit price does not match market tick")
            payload["limitPrice"]=str(limit)
            payload["maker"]=limit<min(m.asks) if payload["side"]=="long" else limit>max(m.bids)
        now=time.time()
        identifier=uuid.uuid4().hex
        self.db.execute("INSERT INTO paper_orders VALUES (?,?,?,?,?,?,'pending',?,NULL,NULL)",
            (identifier,user,self.db.execute("SELECT session FROM users WHERE address=?",(user,)).fetchone()[0],
             client_id,now,now+self.settings.latency_ms/1000,packed(payload)))
        self.activity(user,"order_queued",f"{payload['symbol']} {payload['side']} · {payload['type']} · PAPER")
        return dict(self.db.execute("SELECT * FROM paper_orders WHERE id=?",(identifier,)).fetchone())

    def cancel(self,user,identifier):
        row=self.db.execute("SELECT * FROM paper_orders WHERE id=? AND user=?",(identifier,user)).fetchone()
        if not row:
            raise ValueError("Order not found")
        if json.loads(row["payload"]).get("reason") in ("stop","liquidation","invalid_persisted_risk"):
            raise ValueError("Risk exits cannot be cancelled")
        self.db.execute("UPDATE paper_orders SET status='cancelled' WHERE id=? AND status='pending'",(identifier,))
        self.activity(user,"order_cancelled","Pending simulated order cancelled")

    def open(self,user,signal):
        """Existing Agents.signal() contract; preserving approved strategy geometry."""
        prefs=get(self.db,"paper-prefs:"+user,{})
        if signal["symbol"] not in prefs.get("symbols",[prefs.get("symbol")]):
            raise ValueError("Signal market differs from selected market")
        return self.submit(user,dict(symbol=signal["symbol"],side=signal["side"],type="market",
            leverage=prefs.get("leverage","2"),notional="200",stopBps=signal["stop_bps"],
            targetBps=signal["target_bps"],hold=signal["hold"],agent=True,agentGeneration=prefs.get("generation",0)),
            "ai:"+hashlib.sha256(signal["signal_id"].encode()).hexdigest())

    def fill_open(self,user,order):
        if order.get("agent"):
            prefs=get(self.db,"paper-prefs:"+user["address"],{})
            if not prefs.get("autonomous") or order.get("agentGeneration",0)!=prefs.get("generation",0):
                raise ValueError("AI entries were paused")
        m=self.market(order["symbol"])
        if any(p["symbol"]==m.symbol for p in self.getPosition(user["address"])):
            raise ValueError("Position already exists")
        sign=D(1) if order["side"]=="long" else D(-1)
        leverage=decimal(order["leverage"],True)
        if leverage>min(D(3),self.settings.max_leverage,m.max_leverage):
            raise ValueError("Leverage limit changed")
        budget=min(D(2),self.settings.loss_budget,D(order["riskBudget"]))
        maker=order.get("maker",False)
        fee_rate=(self.settings.maker_fee if maker else self.settings.taker_fee)+self.settings.platform_fee
        exit_fee=self.settings.taker_fee+self.settings.platform_fee
        reserve_fee=max(exit_fee,D(".0004"))  # Preserve inherited stricter risk reserve.
        per_unit=m.mark*(D(order["stopBps"])/10000+fee_rate+reserve_fee+self.settings.slippage+self.settings.spread)
        qty=rounded(min(D(order["notional"])/m.mark,(budget-D(".000004"))/per_unit),m.step)
        if qty<m.min_qty:
            raise ValueError("Risk budget is below the market minimum size")
        limit=D(order["limitPrice"]) if order["type"]=="limit" else None
        entry,costs=self.execution(m,"buy" if sign>0 else "sell",qty,limit,maker)
        if entry*qty<m.min_notional:
            raise ValueError("Risk-sized order is below market minimum notional")
        fee=up_money(entry*qty*fee_rate)
        free=self.available(user)
        if entry*qty/leverage+fee>free:
            raise ValueError("Insufficient simulated available balance")
        now=time.time()
        p=dict(id=uuid.uuid4().hex,user=user["address"],session=user["session"],symbol=m.symbol,side=order["side"],
            qty=str(qty),entry=str(entry),stop=str(entry*(1-sign*D(order["stopBps"])/10000)),
            target=str(rounded(entry*(1+sign*D(order["targetBps"])/10000),m.tick,sign>0)),
            entry_fee_left=str(fee),paid_fees=str(fee),realized_gross="0",opened=now,
            expires=now+min(21600,max(10,float(order.get("hold",21600)))),leverage=str(leverage))
        meta=dict(riskBudget=str(budget),exitFee=str(exit_fee),fundingRemaining="0",fundingTotal="0",
                  fundingAt=now,fundingGapSeconds=0,agent=bool(order.get("agent")),tick=str(m.tick))
        put(self.db,"paper-position:"+p["id"],meta)
        p["stop"]=str(self.protect(p))
        if (entry-D(p["stop"]))*sign<=0:
            raise ValueError("Fees leave no valid protected position")
        self.db.execute("INSERT INTO positions("+",".join(p)+") VALUES ("+",".join("?" for _ in p)+")",tuple(p.values()))
        self.costs(user,p["id"],tradingFee=fee,**costs)
        self.activity(user["address"],"position_opened",f"{m.symbol} {p['side']} · stop {p['stop']} · fee-inclusive trigger budget ${budget}")
        return p

    def available(self,user):
        equity=D(user["balance"])
        margin=D(0)
        for p in self.getPosition(user["address"]):
            mark=self.getPrice(p["symbol"])
            sign=D(1) if p["side"]=="long" else D(-1)
            equity+=(mark-D(p["entry"]))*D(p["qty"])*sign-D(p["entry_fee_left"])
            margin+=D(p["entry"])*D(p["qty"])/D(p["leverage"])
        return max(D(0),equity-margin)

    def close(self,position_id,reason,fraction=D(1)):
        """Internal fill only, called after an order's latency elapses."""
        row=self.db.execute("SELECT * FROM positions WHERE id=?",(position_id,)).fetchone()
        if not row:
            return None
        p=dict(row); meta=self.meta(p); m=self.market(p["symbol"])
        fraction=decimal(fraction,True)
        if fraction>1:
            raise ValueError("Invalid close fraction")
        old_qty=D(p["qty"])
        qty=old_qty if fraction==1 else rounded(old_qty*fraction,m.step)
        if qty<=0:
            raise ValueError("Partial close is below size precision")
        sign=D(1) if p["side"]=="long" else D(-1)
        px,costs=self.execution(m,"sell" if sign>0 else "buy",qty)
        gross=money((px-D(p["entry"]))*qty*sign)
        exit_fee=up_money(px*qty*(self.settings.taker_fee+self.settings.platform_fee))
        entry_fee=D(p["entry_fee_left"]) if qty==old_qty else money(D(p["entry_fee_left"])*qty/old_qty)
        funding=D(meta.get("fundingRemaining","0")) if qty==old_qty else money(D(meta.get("fundingRemaining","0"))*qty/old_qty)
        pnl=gross-entry_fee-exit_fee-funding
        user=dict(self.db.execute("SELECT * FROM users WHERE address=?",(p["user"],)).fetchone())
        t=dict(id=uuid.uuid4().hex,user=p["user"],session=p["session"],strategy=user["strategy"],position_id=p["id"],
               symbol=p["symbol"],side=p["side"],qty=str(qty),entry=p["entry"],exit=str(px),gross=str(gross),
               fees=str(entry_fee+exit_fee),pnl=str(pnl),pnl_micro=int(pnl*1_000_000),opened=p["opened"],closed=time.time(),
               reason=reason,chain_status="offchain")
        self.db.execute("INSERT INTO trades("+",".join(t)+") VALUES ("+",".join("?" for _ in t)+")",tuple(t.values()))
        put(self.db,"paper-trade:"+t["id"],dict(funding=str(funding),fundingGapSeconds=meta.get("fundingGapSeconds",0)))
        # Funding was already debited as it accrued; do not debit it twice.
        self.db.execute("UPDATE users SET balance=? WHERE address=?",(str(D(user["balance"])+gross-entry_fee-exit_fee),p["user"]))
        self.costs(user,p["id"],tradingFee=exit_fee,**costs)
        if qty==old_qty:
            self.db.execute("DELETE FROM positions WHERE id=?",(p["id"],))
        else:
            p.update(qty=str(old_qty-qty),entry_fee_left=str(D(p["entry_fee_left"])-entry_fee),
                     paid_fees=str(D(p["paid_fees"])+exit_fee),realized_gross=str(D(p["realized_gross"])+gross))
            meta["fundingRemaining"]=str(D(meta.get("fundingRemaining","0"))-funding)
            put(self.db,"paper-position:"+p["id"],meta)
            p["stop"]=str(self.protect(p))
            self.db.execute("UPDATE positions SET qty=?,entry_fee_left=?,paid_fees=?,realized_gross=?,stop=? WHERE id=?",
                            (p["qty"],p["entry_fee_left"],p["paid_fees"],p["realized_gross"],p["stop"],p["id"]))
        self.activity(p["user"],"position_closed",f"{p['symbol']} · {reason} · realized ${pnl} including allocated funding")
        return t

    def funding(self,now):
        for row in self.db.execute("SELECT * FROM positions").fetchall():
            p=dict(row); meta=self.meta(p); m=self.feed.markets.get(p["symbol"])
            previous=float(meta.get("fundingAt",now)); elapsed=max(0,now-previous)
            meta["fundingAt"]=now
            if not m or not m.fresh(now) or elapsed>5:
                meta["fundingGapSeconds"]=meta.get("fundingGapSeconds",0)+elapsed
                if meta["fundingGapSeconds"]>1:
                    put(self.db,"paper-funding-incomplete:"+p["user"],True)
            elif elapsed>0:
                sign=D(1) if p["side"]=="long" else D(-1)
                # Keep decimal precision during accrual; round only display and trade allocations.
                amount=m.index*D(p["qty"])*m.funding_rate*D(str(elapsed))/3600*sign
                user=dict(self.db.execute("SELECT * FROM users WHERE address=?",(p["user"],)).fetchone())
                self.db.execute("UPDATE users SET balance=? WHERE address=?",(str(D(user["balance"])-amount),p["user"]))
                meta["fundingRemaining"]=str(D(meta.get("fundingRemaining","0"))+amount)
                meta["fundingTotal"]=str(D(meta.get("fundingTotal","0"))+amount)
                self.costs(user,p["id"],funding=amount)
            put(self.db,"paper-position:"+p["id"],meta)

    def risk_tick(self,now):
        for row in self.db.execute("SELECT * FROM positions").fetchall():
            p=dict(row)
            try:
                m=self.market(p["symbol"])
                stop=self.protect(p)
                meta=self.meta(p)
                meta["riskBudget"]=str(min(D(2),self.settings.loss_budget,D(meta.get("riskBudget","2"))))
                meta["exitFee"]=str(max(self.settings.taker_fee+self.settings.platform_fee,D(meta.get("exitFee","0"))))
                put(self.db,"paper-position:"+p["id"],meta)
                self.db.execute("UPDATE positions SET stop=? WHERE id=?",(str(stop),p["id"]))
                sign=D(1) if p["side"]=="long" else D(-1)
                position_equity=D(p["entry"])*D(p["qty"])/D(p["leverage"])+(m.mark-D(p["entry"]))*D(p["qty"])*sign-D(p["entry_fee_left"])-D(meta.get("fundingRemaining","0"))
                maintenance=m.mark*D(p["qty"])*m.maintenance
                reason=("liquidation" if position_equity<=maintenance else "stop" if (m.mark-stop)*sign<=0
                        else "target" if (m.mark-D(p["target"]))*sign>=0 else "timeout" if now>=p["expires"] else None)
                if reason:
                    # The trigger is latched into an order; later recovery cannot remove it.
                    self.submit(p["user"],dict(reduceOnly=True,positionId=p["id"],reason=reason),"risk:"+p["id"])
            except ValueError:
                # Explicitly retain the position when no valid market fill exists.
                continue

    def tick(self):
        now=time.time()
        with transaction(self.db):
            self.funding(now)
            self.risk_tick(now)
        for row in self.db.execute("SELECT * FROM paper_orders WHERE status='pending' AND due<=? ORDER BY due,created",(now,)).fetchall():
            order=json.loads(row["payload"])
            if not order.get("reduceOnly") and now-row["created"]>(604800 if order["type"]=="limit" else 30):
                self.db.execute("UPDATE paper_orders SET status='expired',error='Order lifetime elapsed' WHERE id=?",(row["id"],))
                self.activity(row["user"],"order_expired","Order lifetime elapsed before execution")
                continue
            try:
                with transaction(self.db):
                    if order.get("reduceOnly"):
                        result=self.close(order["positionId"],order.get("reason","manual"),D(order["fraction"]))
                    else:
                        user=dict(self.db.execute("SELECT * FROM users WHERE address=?",(row["user"],)).fetchone())
                        result=self.fill_open(user,order)
                    self.db.execute("UPDATE paper_orders SET status='filled',result=? WHERE id=?",(packed(result),row["id"]))
            except ValueError as exc:
                message=str(exc)
                transient=any(word in message for word in ("Fresh","Stale","depth"))
                waiting=((order.get("reduceOnly") and transient) or (order["type"]=="limit" and
                         ("Limit waiting" in message or "depth" in message or "Fresh" in message or "Stale" in message)))
                status="pending" if waiting else "rejected"
                if not order.get("reduceOnly") and now-row["created"]>(604800 if order["type"]=="limit" else 30):
                    status="expired"
                self.db.execute("UPDATE paper_orders SET status=?,error=? WHERE id=?",(status,message,row["id"]))
                if status!="pending":
                    self.activity(row["user"],"order_"+status,message)

    def snapshot(self,user):
        row=dict(self.db.execute("SELECT * FROM users WHERE address=?",(user,)).fetchone())
        positions=self.getPosition(user)
        unrealized=D(0); fresh=True; margin=D(0)
        for p in positions:
            meta=self.meta(p); p["funding"]=meta.get("fundingRemaining","0")
            p["fundingGapSeconds"]=meta.get("fundingGapSeconds",0)
            p["riskBudget"]=str(min(D(2),self.settings.loss_budget,D(meta.get("riskBudget","2"))))
            try:
                m=self.market(p["symbol"]); sign=D(1) if p["side"]=="long" else D(-1)
                pnl=(m.mark-D(p["entry"]))*D(p["qty"])*sign-D(p["entry_fee_left"])
                p.update(mark=str(m.mark),unrealized=str(money(pnl)),fresh=True)
                unrealized+=pnl
            except ValueError:
                p.update(mark=None,unrealized=None,fresh=False);fresh=False
            margin+=D(p["entry"])*D(p["qty"])/D(p["leverage"])
        equity=D(row["balance"])+unrealized if fresh else None
        trades=[]
        for t in self.db.execute("SELECT * FROM trades WHERE user=? ORDER BY closed DESC LIMIT 100",(user,)):
            trade=dict(t);trade.update(get(self.db,"paper-trade:"+trade["id"],{}));trades.append(trade)
        totals=dict(trades=0,wins=0,losses=0,realized=D(0))
        today=dict(trades=0,wins=0,losses=0,realized=D(0))
        midnight=int(time.time())//86400*86400
        for t in self.db.execute("SELECT pnl,closed FROM trades WHERE user=?",(user,)):
            for result in ([totals,today] if t["closed"]>=midnight else [totals]):
                pnl=D(t["pnl"]);result["trades"]+=1;result["wins"]+=int(pnl>0);result["losses"]+=int(pnl<0);result["realized"]+=pnl
        for result in (totals,today):
            result["realized"]=str(result["realized"])
        costs=self.cost_totals(user)
        orders=[]
        for o in self.db.execute("SELECT * FROM paper_orders WHERE user=? ORDER BY created DESC LIMIT 30",(user,)):
            o=dict(o);o["payload"]=json.loads(o["payload"]);orders.append(o)
        activity=[dict(r) for r in self.db.execute("SELECT ts,kind,detail FROM paper_activity WHERE user=? ORDER BY id DESC LIMIT 40",(user,))]
        history=[dict(r) for r in reversed(self.db.execute("SELECT ts,equity FROM equity WHERE user=? ORDER BY ts DESC LIMIT 240",(user,)).fetchall())]
        decision=get(self.db,"ai-decision:"+user)
        if decision and decision.get("session")!=row["session"]:
            decision=None
        return dict(mode="PAPER",initial=row["initial"],balance=row["balance"],
                    fundingIncomplete=bool(get(self.db,"paper-funding-incomplete:"+user,False)),
                    equity=str(money(equity)) if equity is not None else None,
                    unrealized=str(money(unrealized)) if fresh else None,
                    pnl=str(money(equity-D(row["initial"]))) if equity is not None else None,
                    available=str(money(max(D(0),equity-margin))) if equity is not None else None,
                    positions=positions,trades=trades,orders=orders,activity=activity,history=history,
                    totals=totals,today=today,scope="All-time paper account; today means UTC; trade counts count closed fills",
                    costs={k:str(money(v)) for k,v in costs.items()},decision=decision,
                    preferences=get(self.db,"paper-prefs:"+user,{}))
