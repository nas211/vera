import time
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from vela import practice
from vela.api import create_app
from vela.db import get, put, health
from vela.agents import Agents

TOKEN = "a" * 64
OTHER = "b" * 64


def test_practice_start_trade_stop_isolated_from_wallet_and_chain(setup_db):
    source, settings, wallet = setup_db
    p = practice.Practice(source,settings,SimpleNamespace(ready=False))
    try:
        p.tick()
        first=practice.start(p.db,TOKEN,"1000")
        assert D(first["initial"]) == 200
        assert practice.start(p.db,TOKEN,"50000")["session"] == first["session"]
        trade=p.adapter.open(first["address"],dict(symbol="DOGE/USD",side="long",stop_bps="100",
            target_bps="150",hold=7200,signal_id="practice-local-fixture"))
        assert practice.snapshot(p.db,OTHER,settings)["user"] is None
        practice.stop(p.db,OTHER)
        assert p.db.execute("SELECT status FROM users").fetchone()[0]=="active"
        # A stale feed must not fabricate a closing price.
        practice.stop(p.db,TOKEN)
        source.execute("UPDATE prices SET safe=0")
        p.tick()
        assert p.adapter.getPosition(first["address"])
        assert p.db.execute("SELECT status FROM users").fetchone()[0]=="stopping"
        source.execute("UPDATE prices SET safe=1,value='.21',ts=? WHERE symbol='DOGE/USD'",(time.time(),))
        p.tick()
        result=practice.snapshot(p.db,TOKEN,settings)
        assert result["user"]["status"]=="settled"
        assert len(result["trades"])==1
        assert D(result["trades"][0]["pnl"])>0
        assert result["trades"][0]["chain_status"]=="offchain"
        assert result["trades"][0]["tx_hash"] is None
        assert p.db.execute("SELECT count(*) FROM outbox").fetchone()[0]==0
        assert source.execute("SELECT count(*) FROM outbox").fetchone()[0]==0
        assert source.execute("SELECT balance FROM users WHERE address=?",(wallet,)).fetchone()[0]=="100"
    finally:
        p.close()


def test_practice_api_browser_capability_and_no_funding_required(setup_db):
    source,settings,_=setup_db
    settings.practice_enabled=True  # Explicit engineering mode only.
    p=practice.Practice(source,settings,SimpleNamespace(ready=False))
    try:
        p.tick()
        for service in ("practice","prices","agents"):
            health(source,service,True,"fixture")
        client=TestClient(create_app(settings))
        assert client.get("/api/practice").status_code==401
        headers={"X-Vela-Practice":TOKEN}
        r=client.post("/api/practice/start",headers=headers,json={"amountDoge":"1000"})
        assert r.status_code==200 and r.json()["user"]["status"]=="active"
        assert client.get("/api/practice",headers={"X-Vela-Practice":OTHER}).json()["user"] is None
        assert client.post("/api/practice/start",headers=headers,json={"amountDoge":"NaN"}).status_code==422
        assert client.post("/api/practice/start",headers=headers,json={"amountDoge":"100001"}).status_code==409
        r=client.post("/api/practice/stop",headers=headers,json={})
        assert r.status_code==200 and r.json()["user"]["status"]=="stopping"
        p.tick()
        assert client.get("/api/practice",headers=headers).json()["user"]["status"]=="settled"
        assert client.get("/api/config").json()["defaultPortfolio"]=="vault"
        assert p.db.execute("SELECT count(*) FROM outbox").fetchone()[0]==0
    finally:
        p.close()


def test_research_review_direction_required_and_session_bound(setup_db):
    db,settings,address=setup_db
    now=time.time()
    last=int(now//300)*300-300
    for i in range(250):
        db.execute("INSERT INTO candles VALUES ('DOGE/USD',?,?,?,?,?)",
                   (last-(249-i)*300,.2,.21,.19,.2))
    spec=dict(name="Bounded fixture",thesis="Test the approval pipeline",market="DOGE/USD",mode="swing",
              side="long",match="all",conditions=[dict(feature="range_position",op="gt",value=.1)],
              stop_bps=100,target_bps=150,max_hold_seconds=7200,entry="taker",breakeven_bps=0,trail_bps=0)
    calls=[]
    class Model:
        ready=True
        model="unit-fixture"
        def ask(self,role,evidence,schema):
            calls.append(role)
            return dict(strategies=[spec],reason="fixture") if role=="research" else \
                dict(verdict="confirm",reason="fixture",sentiment=0) if role=="review" else \
                dict(side="long",action="open",reason="fixture")
    agents=Agents(db,Model())
    user=dict(db.execute("SELECT * FROM users").fetchone())
    assert agents.signal(user) is None
    for expected in ("research","review","direction"):
        put(db,"ai-last-call",0)
        agents.tick_ai()
        assert calls[-1]==expected
        if expected!="direction":assert agents.signal(user) is None
    assert agents.signal(user)["side"]=="long"
    user["session"]="different-session"
    assert agents.signal(user) is None
    assert len(get(db,"ai-calls"))==3


def test_warmup_repairs_recent_gap_even_with_many_old_candles(setup_db):
    from vela.agents import bars_for
    from vela.prices import PriceFeed
    db,settings,_=setup_db
    boundary=int(time.time()//300)*300
    for i in range(1,301):
        if i != 7:
            db.execute("INSERT INTO candles VALUES ('DOGE/USD',?,?,?,?,?)",
                       (boundary-i*300,.2,.21,.19,.2))
    assert not bars_for(db,"DOGE/USD",time.time())
    class Client:
        def get(self,*args,**kwargs):
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:[
                [(boundary-i*300)*1000,".2",".21",".19",".2"] for i in range(300,0,-1)])
    PriceFeed(db,settings,Client()).bootstrap_candles()
    assert len(bars_for(db,"DOGE/USD",time.time()))==250
