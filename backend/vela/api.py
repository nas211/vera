import json
import os
import time
import uuid
from decimal import Decimal as D
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from web3 import Web3
from .settings import Settings, ROOT
from .db import connect, get
from .paper import PaperAdapter
from .agents import STRATEGIES
from .prices import quote
from .fees import spent
from . import practice


class QuoteRequest(BaseModel):
    address: str = Field(min_length=42,max_length=42)
    strategy: int = Field(ge=1,le=1)
    amountWei: str = Field(pattern=r"^[1-9][0-9]{0,30}$")

class PracticeRequest(BaseModel):
    amountDoge: str = Field(pattern=r"^[0-9]{1,6}(\.[0-9]{1,8})?$")


def create_app(settings=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Vela Testnet · Paper Trading", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=[
        "https://defivela.xyz", "https://defivela.uuuiiaaaa.chatgpt.site",
        "http://127.0.0.1:8787", "http://127.0.0.1:8769"],
        allow_methods=["GET","POST"], allow_headers=["Content-Type","X-Vela-Practice"])

    @contextmanager
    def database():
        db = connect(settings.db_path)
        try:
            yield db
        finally:
            db.close()

    @app.get("/health")
    def health_all():
        with database() as db:
            services = {r["name"]: dict(r) for r in db.execute("SELECT * FROM services")}
        for r in services.values():
            r["ok"] = bool(r["ok"]) and time.time() - r["ts"] < 90
        ready = all(services.get(k, {}).get("ok") for k in ("prices", "engine", "events", "operator", "agents"))
        reasons = [r["detail"] for r in services.values() if not r["ok"]]
        return dict(mode="testnet-paper", ready=ready, services=services, reasons=reasons)

    @app.get("/health/{service}")
    def health_one(service: str):
        if service not in ("prices", "engine", "events", "operator", "agents", "practice"):
            raise HTTPException(404)
        return health_all()["services"].get(service, dict(ok=False, detail="Not started"))

    @app.get("/api/config")
    def config():
        chain = settings.config
        deployment = chain.get("deployment")
        health = health_all()
        abi_name = "VelaVaultV2.abi.json" if deployment and deployment.get("version",1)>=2 else "VelaVault.abi.json"
        return dict(chain=chain, mode="testnet-paper", deployment=deployment,
                    defaultPortfolio="vault",
                    abi=json.loads((ROOT / "config" / abi_name).read_text()) if deployment else [],
                    ready=health["ready"], reasons=health["reasons"],
                    fees=dict(maxTransactionDoge=str(settings.max_tx_fee),
                              rolling24hBudgetDoge=str(settings.daily_fee_budget),
                              includesDataAndFinality=True),
                    risk=dict(maxLeverage=str(settings.max_leverage), feePerSide="0.0005",
                              slippage=str(settings.slippage), maxLossBudget=str(settings.loss_budget),
                              funding="ignored", stopBudgetIsFillGuarantee=False))

    @app.get("/api/strategies")
    def strategies():
        with database() as db:
            model_ready = bool(get(db, "ai_model_configured", False))
        return [dict(s, available=model_ready, modelReady=model_ready,
                     availabilityReason="Research model connected" if model_ready else
                     "Signal and risk agents running · AI research model not connected")
                for s in STRATEGIES]

    @app.post("/api/start-quote",status_code=202)
    def start_quote(body: QuoteRequest):
        if not Web3.is_address(body.address) or int(body.amountWei)>10**30:
            raise HTTPException(400,"Invalid wallet or amount")
        if not health_all()["ready"]:
            raise HTTPException(503,"Trading is paused; no transaction submitted")
        with database() as db:
            if not get(db,"ai_model_configured",False):
                raise HTTPException(503,"AI research model is not connected")
            existing = db.execute("SELECT id FROM start_quotes WHERE user=? AND strategy=? AND value_wei=? "
                "AND status='pending' AND created>?", (body.address.lower(),body.strategy,body.amountWei,time.time()-90)).fetchone()
            if existing:
                return dict(id=existing["id"],status="pending")
            if db.execute("SELECT count(*) FROM start_quotes WHERE created>?",(time.time()-60,)).fetchone()[0]>=30:
                raise HTTPException(429,"Quote request limit reached; retry shortly")
            identifier=uuid.uuid4().hex
            db.execute("INSERT INTO start_quotes VALUES (?,?,?,?,?,'pending',NULL,NULL)",
                       (identifier,body.address.lower(),body.strategy,body.amountWei,time.time()))
        return dict(id=identifier,status="pending")

    @app.get("/api/start-quote/{identifier}")
    def get_start_quote(identifier: str):
        if len(identifier)!=32:
            raise HTTPException(404)
        with database() as db:
            row=db.execute("SELECT * FROM start_quotes WHERE id=?",(identifier,)).fetchone()
        if not row:
            raise HTTPException(404)
        result=json.loads(row["result"]) if row["result"] else None
        expired=(result and int(result["deadline"])<time.time()) or (row["status"]=="pending" and time.time()-row["created"]>90)
        return dict(status="expired" if expired else row["status"],quote=result,error=row["error"])

    @app.get("/api/network-fees")
    def network_fees():
        with database() as db:
            cost=spent(db)
        return dict(scope="Dedicated operator wallet: rolling 24 hours plus pending reservations",doge=str(D(cost)/10**18),
                    budgetDoge=str(settings.daily_fee_budget),perTransactionDoge=str(settings.max_tx_fee))

    @contextmanager
    def practice_database(token):
        if not settings.practice_enabled:
            raise HTTPException(404, "Not available")
        try:
            practice.identity(token)
        except ValueError:
            raise HTTPException(401,"Practice session required") from None
        db=connect(practice.practice_settings(settings).db_path)
        try:
            yield db
        finally:
            db.close()

    @app.get("/api/practice")
    def practice_account(x_vela_practice: str | None = Header(default=None)):
        with practice_database(x_vela_practice) as db:
            result=practice.snapshot(db,x_vela_practice,settings)
        h=health_all()["services"]
        result["ready"]=all(h.get(k,{}).get("ok") for k in ("prices","practice","agents"))
        result["reason"]="" if result["ready"] else "Waiting for live prices and the AI engine"
        return result

    @app.post("/api/practice/start")
    def practice_start(body: PracticeRequest,x_vela_practice: str | None = Header(default=None)):
        with practice_database(x_vela_practice) as db:
            h=health_all()["services"]
            if not all(h.get(k,{}).get("ok") for k in ("prices","practice","agents")):
                raise HTTPException(503,"Waiting for live prices and the AI engine")
            try:
                practice.start(db,x_vela_practice,body.amountDoge)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
            return practice.snapshot(db,x_vela_practice,settings)

    @app.post("/api/practice/stop")
    def practice_stop(x_vela_practice: str | None = Header(default=None)):
        with practice_database(x_vela_practice) as db:
            practice.stop(db,x_vela_practice)
            return practice.snapshot(db,x_vela_practice,settings)

    @app.get("/api/practice/leaderboard")
    def practice_board():
        if not settings.practice_enabled:
            raise HTTPException(404, "Not available")
        db=connect(practice.practice_settings(settings).db_path)
        try:
            rows=db.execute("""SELECT count(*) trades,coalesce(sum(pnl_micro),0) pnl,
              coalesce(sum(CASE WHEN pnl_micro>0 THEN 1 ELSE 0 END),0) wins,
              count(DISTINCT user) users FROM trades WHERE reason!='engineering_smoke'""").fetchone()
            return dict(scope="All-time closed practice trades across all users; simulated fees included; engineering checks excluded",
                rows=[dict(name="Vela AI Agents",trades=rows["trades"],pnl=str(D(rows["pnl"])/1_000_000),
                           winRate=rows["wins"]/rows["trades"],users=rows["users"])] if rows["trades"] else [])
        finally:
            db.close()

    @app.get("/api/prices")
    def prices():
        with database() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM prices")]
        for r in rows:
            r["safe"] = bool(r["safe"]) and -5 <= time.time() - r["ts"] <= settings.max_price_age
        return rows

    @app.get("/api/users/{address}")
    def account(address: str):
        if not Web3.is_address(address):
            raise HTTPException(400, "Invalid EVM address")
        with database() as db:
            row = db.execute("SELECT * FROM users WHERE address=?", (address.lower(),)).fetchone()
            if not row:
                return dict(user=None, positions=[], trades=[], equity=[], scope="current session")
            user = dict(row)
            adapter = PaperAdapter(db, settings)
            positions = adapter.getPosition(address)
            for p in positions:
                try:
                    mark = adapter.getPrice(p["symbol"])
                    sign = D(1) if p["side"] == "long" else D(-1)
                    p["mark"] = str(mark)
                    p["unrealized"] = str((mark - D(p["entry"])) * D(p["qty"]) * sign - D(p["entry_fee_left"]))
                except ValueError:
                    p["mark"] = p["unrealized"] = None
            try:
                equity = adapter.equity(user)
                user["equity"] = str(equity)
                user["pnl"] = str(equity - D(user["initial"]))
                user["pnlPercent"] = str((equity / D(user["initial"]) - 1) * 100)
                user["dogeEquivalent"] = str(max(D(0), equity) / quote(db, "DOGE/USD"))
            except ValueError:
                user.update(equity=None, pnl=None, pnlPercent=None, dogeEquivalent=None)
            trades = [dict(r) for r in db.execute("SELECT * FROM trades WHERE session=? ORDER BY closed DESC LIMIT 100", (user["session"],))]
            history = [dict(r) for r in db.execute("SELECT ts,equity,pnl FROM equity WHERE session=? ORDER BY ts DESC LIMIT 500", (user["session"],))][::-1]
            decision = get(db, "ai-decision:" + address.lower())
            if decision and decision.get("session") != user["session"]:
                decision = None
            return dict(user=user, positions=positions, trades=trades, equity=history, decision=decision, scope="current session")

    @app.get("/api/leaderboard")
    def leaderboard():
        with database() as db:
            rows = list(db.execute("""SELECT 1 AS strategy,count(*) trades,sum(pnl_micro) pnl_micro,
                sum(CASE WHEN pnl_micro>0 THEN 1 ELSE 0 END) wins,count(DISTINCT user) users
                FROM trades WHERE reason!='engineering_smoke'
                HAVING count(*)>0"""))
        return dict(scope="All-time closed strategy paper trades; all users; fees included; engineering checks excluded; funding ignored",
                    rows=[dict(strategy=r["strategy"], name=next((s["name"] for s in STRATEGIES if s["id"] == r["strategy"]), "Unknown"),
                               trades=r["trades"], wins=r["wins"], users=r["users"],
                               pnl=str(D(r["pnl_micro"]) / 1_000_000), winRate=r["wins"] / r["trades"]) for r in rows])

    @app.get("/api/logs/{service}")
    def logs(service: str):
        if service not in ("prices", "engine", "events", "operator", "agents", "worker"):
            raise HTTPException(404)
        with database() as db:
            return [dict(r) for r in db.execute("SELECT ts,level,message,detail FROM logs WHERE service=? ORDER BY id DESC LIMIT 50", (service,))]

    app.mount("/", StaticFiles(directory=ROOT / "site/dist", html=True), name="site")
    return app


app = create_app()
