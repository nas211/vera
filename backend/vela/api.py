import json
import os
import time
from decimal import Decimal as D
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from web3 import Web3
from .settings import Settings, ROOT
from .db import connect, get
from .paper import PaperAdapter
from .agents import STRATEGIES
from .prices import quote


def create_app(settings=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Vela Testnet · Paper Trading", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=[
        "https://defivela.xyz", "https://defivela.uuuiiaaaa.chatgpt.site",
        "http://127.0.0.1:8787", "http://127.0.0.1:8769"],
        allow_methods=["GET"], allow_headers=["Content-Type"])

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
        ready = all(services.get(k, {}).get("ok") for k in ("prices", "engine", "events", "operator"))
        return dict(mode="testnet-paper", ready=ready, services=services)

    @app.get("/health/{service}")
    def health_one(service: str):
        if service not in ("prices", "engine", "events", "operator", "agents"):
            raise HTTPException(404)
        return health_all()["services"].get(service, dict(ok=False, detail="Not started"))

    @app.get("/api/config")
    def config():
        chain = settings.config
        deployment = chain.get("deployment")
        return dict(chain=chain, mode="testnet-paper", deployment=deployment,
                    abi=json.loads((ROOT / "config/VelaVault.abi.json").read_text()) if deployment else [],
                    ready=health_all()["ready"],
                    risk=dict(maxLeverage=str(settings.max_leverage), feePerSide="0.0005",
                              slippage=str(settings.slippage), maxLossBudget=str(settings.loss_budget),
                              funding="ignored", stopBudgetIsFillGuarantee=False))

    @app.get("/api/strategies")
    def strategies():
        with database() as db:
            model_ready = bool(get(db, "ai_model_configured", False))
        return [dict(s, available=True, modelReady=model_ready,
                     availabilityReason="Research model connected" if model_ready else
                     "Signal and risk agents running · AI research model not connected")
                for s in STRATEGIES]

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
            return dict(user=user, positions=positions, trades=trades, equity=history, scope="current session")

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
