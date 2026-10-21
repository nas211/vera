"""Reusable paper runtime and routes, attached to the original Vela API."""
import asyncio
import fcntl
import json
import time
import threading
from contextlib import asynccontextmanager
from decimal import Decimal as D
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

from ..agents import Agents, VelaModel, bars_for, features
from ..db import connect, get, put, transaction
from ..settings import ROOT
from ..prices import quote
from .config import PaperConfig
from .engine import LighterPaperAdapter, identity
from .feed import LighterFeed, SYMBOLS
from .monitor import review_position, consume_exits
from ..portal import Portal, attach_portal


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientId: str = Field(min_length=1,max_length=120)
    symbol: Literal["BTC/USD","ETH/USD","SOL/USD"] = "BTC/USD"
    side: Literal["long","short"] = "long"
    type: Literal["market","limit"] = "market"
    notional: str = "100"
    leverage: str = "2"
    stopBps: str = "50"
    targetBps: str = "75"
    limitPrice: str | None = None
    riskBudget: str = "2"


class Close(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientId: str = Field(min_length=1,max_length=120)
    fraction: str = "1"


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: Literal["BTC/USD","ETH/USD","SOL/USD"]
    leverage: Literal["1","2","3"] = "2"
    autonomous: bool = False


class Runtime:
    def __init__(self,settings,feed=None,model=None,portal_origin=None):
        self.settings=settings
        settings.db_path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=settings.db_path.with_suffix(".lock").open("a")
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("A paper engine already owns this database; use one server worker") from None
        self.feed=feed or LighterFeed()
        self.db=connect(settings.db_path)
        self.adapter=LighterPaperAdapter(self.db,settings,self.feed)
        self.model=model or VelaModel()
        self.model_lock=threading.Lock()
        self.model.execution_policy=(f"Vela simulates live orderbook depth with {settings.slippage*10000}bp adverse slippage, "
            f"{settings.spread*10000}bp additional half-spread, {settings.latency_ms}ms latency, "
            f"{settings.maker_fee*10000}bp maker, {settings.taker_fee*10000}bp taker and "
            f"{settings.platform_fee*10000}bp platform fees per side. Funding is a prorated hourly estimate. ")
        self.error=""
        self.last_cycle=0
        self.portal=Portal(self,origin=portal_origin)

    def agents(self,db):
        def gate(db,symbol):
            try:
                quote(db,symbol,15)
                return True
            except ValueError:
                return False
        return Agents(db,self.model,
            eligible=lambda u:get(db,"paper-prefs:"+u["address"],{}).get("autonomous",False),
            markets=lambda u:get(db,"paper-prefs:"+u["address"],{}).get("symbols",[get(db,"paper-prefs:"+u["address"],{}).get("symbol","BTC/USD")]),entry_gate=gate,
            revision=lambda u:get(db,"paper-prefs:"+u["address"],{}).get("generation",0))

    def sync_data(self):
        with transaction(self.db):
            for m in self.feed.markets.values():
                self.db.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)",
                    (m.symbol,str(m.mark),m.stats_at,"lighter-public",int(m.fresh()),"" if m.fresh() else "stale"))
                if m.candles:
                    self.db.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)",
                        [(m.symbol,r["ts"],r["o"],r["h"],r["l"],r["c"]) for r in m.candles])

    async def engine_loop(self):
        last_equity=last_history=0
        while True:
            try:
                # Feed mutations, engine cycles and API commands all run on this event loop.
                for m in self.feed.markets.values():
                    self.db.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)",
                        (m.symbol,str(m.mark),m.stats_at,"lighter-public",int(m.fresh()),"" if m.fresh() else "stale"))
                if time.time()-last_history>30:
                    self.sync_data();last_history=time.time()
                self.portal.expire_agents()
                self.adapter.tick()
                consume_exits(self.adapter)
                agents=self.agents(self.db)
                for row in self.db.execute("SELECT * FROM users WHERE status='active'").fetchall():
                    user=dict(row)
                    prefs=get(self.db,"paper-prefs:"+user["address"],{})
                    if prefs.get("autonomous") and not self.adapter.getPosition(user["address"]):
                        signal=agents.signal(user)
                        if signal:
                            try:
                                self.adapter.open(user["address"],signal)
                            except ValueError:
                                pass
                    decision=get(self.db,"ai-decision:"+user["address"])
                    if decision and get(self.db,"paper-logged-decision:"+user["address"])!=decision["at"]:
                        answer=decision["answer"]
                        self.adapter.activity(user["address"],"AI "+decision["phase"],str(answer.get("reason") or answer.get("summary") or answer.get("action") or answer.get("verdict") or "Research complete")[:1200])
                        put(self.db,"paper-logged-decision:"+user["address"],decision["at"])
                    if time.time()-last_equity>5:
                        snapshot=self.adapter.snapshot(user["address"])
                        if snapshot["equity"] is not None:
                            self.db.execute("INSERT INTO equity(user,session,ts,equity,pnl) VALUES (?,?,?,?,?)",
                                (user["address"],user["session"],time.time(),snapshot["equity"],snapshot["pnl"]))
                if time.time()-last_equity>5:
                    last_equity=time.time()
                self.error="";self.last_cycle=time.time()
            except Exception as exc:
                self.error="Paper engine interrupted; orders paused ("+type(exc).__name__+")"
            await asyncio.sleep(.25)

    def model_cycle(self):
        # Never hold a SQLite transaction or block price/stop processing during model I/O.
        db=connect(self.settings.db_path)
        try:
            with self.model_lock:
                if not review_position(db,self.model):
                    self.agents(db).tick_ai()
        finally:
            db.close()

    async def model_loop(self):
        while True:
            if self.model.ready:
                await asyncio.to_thread(self.model_cycle)
            await asyncio.sleep(2)

    def markets(self):
        return dict(mode="PAPER",liveEnabled=False,connected=self.feed.connected,
                    reconnects=self.feed.reconnects,error=self.error or self.feed.error,
                    serverTime=time.time(),engineAt=self.last_cycle,modelReady=bool(self.model.ready),
                    modelConnection=get(self.db,"ai-connection"),
                    markets=[m.public() for m in self.feed.markets.values()],config=self.settings.public())


def attach_lighter(app,settings=None,feed=None,model=None,start_background=True,
                   portal_origin=None,redirect_legacy=True):
    """Register on one app so routing, middleware and lifespan share one runtime."""
    settings=settings or PaperConfig.from_env()
    previous_lifespan=app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with previous_lifespan(app):
            runtime=Runtime(settings,feed,model,portal_origin=portal_origin)
            app.state.runtime=runtime
            tasks=[]
            if start_background:
                tasks=[asyncio.create_task(fn()) for fn in (runtime.feed.refresh,runtime.feed.stream,runtime.engine_loop,runtime.model_loop)]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                runtime.db.close()
                runtime.lock.close()

    app.router.lifespan_context=lifespan

    def rt():return app.state.runtime

    def auth(token):
        wallet_only()
        try:
            address=identity(token)
        except ValueError:
            raise HTTPException(401,"Paper session required") from None
        if not rt().db.execute("SELECT 1 FROM users WHERE address=?",(address,)).fetchone():
            raise HTTPException(401,"Create a paper session first")
        return address

    def wallet_only():
        # The old local engineering desk uses bearer capabilities and creates
        # prefunded accounts. Public Vela uses signed wallets and zero balances.
        if rt().portal.secure:
            raise HTTPException(401,"Connect your wallet through /api/portal")

    def guard():
        if rt().error:
            raise HTTPException(503,rt().error)

    @app.get("/api/paper/markets")
    async def markets():return rt().markets()

    @app.get("/api/paper/health")
    async def health():
        return dict(mode="PAPER",liveEnabled=False,modelReady=bool(rt().model.ready),
            marketData=bool(rt().feed.markets) and all(m.fresh() for m in rt().feed.markets.values()),
            engineReady=time.time()-rt().last_cycle<3,error=rt().error)

    @app.post("/api/paper/session")
    async def session(x_vela_paper:str|None=Header(default=None)):
        wallet_only()
        try:
            row=rt().adapter.account(x_vela_paper)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        return rt().adapter.snapshot(row["address"])

    @app.get("/api/paper/account")
    async def account(x_vela_paper:str|None=Header(default=None)):
        return rt().adapter.snapshot(auth(x_vela_paper))

    @app.post("/api/paper/orders",status_code=202)
    async def order(body:Order,x_vela_paper:str|None=Header(default=None)):
        address=auth(x_vela_paper);guard()
        payload=body.model_dump(exclude_none=True);client_id=payload.pop("clientId")
        try:
            with transaction(rt().db):
                return rt().adapter.submit(address,payload,client_id)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    @app.post("/api/paper/orders/{identifier}/cancel")
    async def cancel(identifier:str,x_vela_paper:str|None=Header(default=None)):
        address=auth(x_vela_paper)
        try:
            rt().adapter.cancel(address,identifier)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None
        return rt().adapter.snapshot(address)

    @app.post("/api/paper/positions/{identifier}/close",status_code=202)
    async def close(identifier:str,body:Close,x_vela_paper:str|None=Header(default=None)):
        address=auth(x_vela_paper);guard()
        try:
            with transaction(rt().db):
                return rt().adapter.submit(address,dict(reduceOnly=True,positionId=identifier,fraction=body.fraction),body.clientId)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    @app.post("/api/paper/agent")
    async def agent(body:Preferences,x_vela_paper:str|None=Header(default=None)):
        address=auth(x_vela_paper);guard()
        prefs=body.model_dump()
        if body.autonomous and not rt().model.ready:
            raise HTTPException(503,"AI model is not configured. Manual paper trading remains available.")
        old=get(rt().db,"paper-prefs:"+address,{})
        if old.get("symbol")!=body.symbol or not body.autonomous:
            rt().adapter.pause_entries(address)
        prefs['generation']=get(rt().db,'paper-prefs:'+address,{}).get('generation',0)
        put(rt().db,"paper-prefs:"+address,prefs)
        rt().adapter.activity(address,"agent_started" if body.autonomous else "agent_paused",
            f"{body.symbol} · research, review and direction required" if body.autonomous else "Autonomous entries paused; position risk controls remain active")
        return rt().adapter.snapshot(address)

    @app.get("/api/paper/analysis/{symbol}")
    async def analysis(symbol:Literal["BTC","ETH","SOL"]):
        full=symbol+"/USD";runtime=rt()
        bars=bars_for(runtime.db,full,time.time())
        candidates=[s for s in runtime.agents(runtime.db).signal_candidates(time.time()) if s["symbol"]==full]
        return dict(symbol=full,modelReady=bool(runtime.model.ready),closedCandles=len(bars),
                    features=features(bars) if bars else None,candidates=candidates,
                    explanation="Existing Vela reversion/pullback signals; model review required before autonomous execution.",
                    status="Model review available" if runtime.model.ready else "HOLD · AI model not configured")

    @app.websocket("/api/paper/stream")
    async def stream(socket:WebSocket):
        # Public market data only. Portfolio capabilities are never put in URLs.
        await socket.accept()
        try:
            while True:
                await socket.send_json(rt().markets())
                await asyncio.sleep(.5)
        except (WebSocketDisconnect,RuntimeError):
            pass

    @app.get("/paper")
    async def paper():return FileResponse(ROOT/"site/dist/paper.html")

    @app.get("/trade")
    async def portal():return FileResponse(ROOT/"site/dist/portal.html")

    @app.get("/earn")
    async def earn():return FileResponse(ROOT/"site/dist/earn.html")

    @app.get("/earn/")
    async def earn_canonical():return RedirectResponse("/earn")

    # Old wallet bookmarks join the same demo onboarding. Do not expose the
    # separate wallet-funded vault journey through the paper showcase server.
    if redirect_legacy:
        @app.get("/mydoge.html")
        async def legacy_wallet():return RedirectResponse("/trade")

    attach_portal(app,Order,Close)
    return app


def create_app(settings=None,feed=None,model=None,start_background=True):
    # Compatibility for existing deterministic tests and older local commands.
    app=FastAPI(title="Vela Paper Trading",docs_url=None,redoc_url=None,openapi_url=None)
    attach_lighter(app,settings,feed,model,start_background)
    app.mount("/",StaticFiles(directory=ROOT/"site/dist",html=True),name="site")
    return app


app=create_app()
