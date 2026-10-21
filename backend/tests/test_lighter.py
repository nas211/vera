"""Deterministic simulated prices/models. No credentials, exchange writes or profits claimed."""
import json
import asyncio
import shutil
import time
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from vela.db import connect, get, put, transaction
from vela.lighter.config import PaperConfig
from vela.lighter.engine import LighterPaperAdapter, LighterLiveAdapter, identity
from vela.lighter.feed import Market, LighterFeed, SYMBOLS
from vela.lighter.app import create_app
from vela.agents import Agents, bars_for
from vela.settings import ROOT


@pytest.fixture
def desk(monkeypatch):
    clock=[1800000000.]
    monkeypatch.setattr(time,"time",lambda:clock[0])
    folder=ROOT/".runtime/lighter-showcase"/("test-"+uuid.uuid4().hex)
    cfg=PaperConfig(db_path=folder/"paper.sqlite3",latency_ms=300)
    db=connect(cfg.db_path)
    feed=LighterFeed();feed.connected=True;feed.error=""
    for i,(symbol,price,tick,step,min_qty) in enumerate([
        ("BTC/USD","10000",".1",".00001",".00007"),
        ("ETH/USD","1000",".01",".0001",".002"),
        ("SOL/USD","100",".001",".001",".055")]):
        m=Market(symbol,i,D(tick),D(step),D(min_qty),D(10),D(50),D(".012"))
        m.mark=m.index=D(price);m.stats_at=m.book_at=clock[0];m.nonce=1;m.funding_rate=D(".0001")
        m.bids={m.mark-m.tick:D(1000)};m.asks={m.mark+m.tick:D(1000)}
        feed.markets[symbol]=m
    adapter=LighterPaperAdapter(db,cfg,feed)
    token="a"*64;user=adapter.account(token)["address"]
    yield SimpleNamespace(clock=clock,db=db,config=cfg,feed=feed,engine=adapter,user=user,token=token)
    db.close();shutil.rmtree(folder)


def refresh(desk,seconds=.31):
    desk.clock[0]+=seconds
    for m in desk.feed.markets.values():
        m.stats_at=m.book_at=desk.clock[0]


def move(desk,symbol,price):
    m=desk.feed.markets[symbol];m.mark=m.index=D(str(price))
    m.bids={m.mark-m.tick:D(1000)};m.asks={m.mark+m.tick:D(1000)}
    refresh(desk)


def submit(desk,**changes):
    payload=dict(symbol="BTC/USD",side="long",type="market",notional="200",leverage="2",stopBps="50",targetBps="75")
    payload.update(changes)
    with transaction(desk.db):
        return desk.engine.submit(desk.user,payload,uuid.uuid4().hex)


def opened(desk,**changes):
    submit(desk,**changes);refresh(desk);desk.engine.tick()
    return desk.engine.getPosition(desk.user)[0]


def net_at_stop(p,rate):
    sign=1 if p["side"]=="long" else -1
    return D(p["realized_gross"])+(D(p["stop"])-D(p["entry"]))*D(p["qty"])*sign-D(p["paid_fees"])-D(p["stop"])*D(p["qty"])*rate


@pytest.mark.parametrize("side",["long","short"])
@pytest.mark.parametrize("symbol",SYMBOLS)
@pytest.mark.parametrize("budget",["2",".7","200"])
def test_global_stop_budget_precision_and_latency(desk,side,symbol,budget):
    desk.config.loss_budget=D(budget)
    desk.config.taker_fee=D(".001");desk.config.platform_fee=D(".0003")
    order=submit(desk,side=side,symbol=symbol,riskBudget=budget,notional="100000",leverage="3")
    desk.engine.tick()
    assert not desk.engine.getPosition(desk.user),"Latency must elapse before execution"
    refresh(desk);desk.engine.tick()
    p=desk.engine.getPosition(desk.user)[0];m=desk.feed.markets[symbol]
    assert D(p["qty"])%m.step==0 and D(p["stop"])%m.tick==0
    assert net_at_stop(p,D(".0013"))>=-min(D(2),D(budget))
    assert desk.db.execute("SELECT status FROM paper_orders WHERE id=?",(order["id"],)).fetchone()[0]=="filled"
    assert desk.db.execute("SELECT count(*) FROM outbox").fetchone()[0]==0


@pytest.mark.parametrize("side",["long","short"])
def test_partial_exits_restart_reconcile_stricter_budget_and_pnl(desk,side):
    desk.config.taker_fee=D(".0007")
    p=opened(desk,side=side,riskBudget="1.2",notional="400")
    move(desk,p["symbol"],D(p["entry"])*(D(".999") if side=="long" else D("1.001")))
    with transaction(desk.db):
        t=desk.engine.close(p["id"],"partial",D(".4"))
    assert D(t["pnl"])<0
    desk.db.execute("UPDATE positions SET stop=? WHERE id=?",("1" if side=="long" else "1000000",p["id"]))
    restarted=LighterPaperAdapter(desk.db,desk.config,desk.feed)
    restarted.tick();p=restarted.getPosition(desk.user)[0]
    assert net_at_stop(p,D(".0007"))>=-D("1.2")
    previous=D(p["stop"])
    # Account settings cannot raise the budget captured at entry.
    desk.config.loss_budget=D(500)
    restarted.tick();p=restarted.getPosition(desk.user)[0]
    assert D(p["stop"])==previous
    tighter=D(p["entry"])+(D("-.1") if side=="long" else D(".1"))
    desk.db.execute("UPDATE positions SET stop=? WHERE id=?",(str(tighter),p["id"]))
    assert (restarted.protect(dict(desk.db.execute("SELECT * FROM positions").fetchone()))-tighter)*(1 if side=="long" else -1)>=0
    with transaction(desk.db):restarted.close(p["id"],"manual")
    snapshot=restarted.snapshot(desk.user)
    assert D(snapshot["balance"])==D(snapshot["initial"])+sum(D(t["pnl"]) for t in snapshot["trades"])
    assert not snapshot["positions"] and snapshot["totals"]["trades"]==2


def test_new_accounts_are_all_protected_no_account_override(desk):
    for token in ("b"*64,"c"*64):
        desk.user=desk.engine.account(token)["address"]
        p=opened(desk,riskBudget="999999",notional="100000",leverage="3")
        assert net_at_stop(p,D(0))>=-D(2)
        assert desk.engine.meta(p)["riskBudget"]=="2"


@pytest.mark.parametrize("side",["long","short"])
@pytest.mark.parametrize("kind",["stop","target","liquidation"])
def test_exit_triggers_latch_respect_latency_and_gaps(desk,side,kind):
    p=opened(desk,side=side)
    sign=D(1) if side=="long" else D(-1)
    price=D(p["stop"])-sign*5 if kind=="stop" else D(p["target"])+sign*5
    if kind=="liquidation":price=D(p["entry"])*(D(".45") if side=="long" else D("1.55"))
    move(desk,p["symbol"],price);desk.engine.tick()
    assert desk.engine.getPosition(desk.user),"Triggered stop must wait for configured latency"
    pending=desk.db.execute("SELECT * FROM paper_orders WHERE status='pending'").fetchone()
    assert json.loads(pending["payload"])["reason"]==kind
    if kind in ("stop","liquidation"):
        with pytest.raises(ValueError):desk.engine.cancel(desk.user,pending["id"])
    refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)
    t=dict(desk.db.execute("SELECT * FROM trades").fetchone())
    assert t["reason"]==kind
    if kind=="liquidation":assert D(t["pnl"])<-D(2),"Budget must never fabricate fills at stop prices"


@pytest.mark.parametrize("side",["long","short"])
def test_resting_limit_cancel_fill_and_price_guarantee(desk,side):
    limit=D(9990) if side=="long" else D(10010)
    o=submit(desk,side=side,type="limit",limitPrice=str(limit))
    refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)
    desk.engine.cancel(desk.user,o["id"])
    assert desk.db.execute("SELECT status FROM paper_orders WHERE id=?",(o["id"],)).fetchone()[0]=="cancelled"
    o=submit(desk,side=side,type="limit",limitPrice=str(limit))
    move(desk,"BTC/USD",limit-5 if side=="long" else limit+5);desk.engine.tick()
    p=desk.engine.getPosition(desk.user)[0]
    assert D(p["entry"])==limit
    assert D(p["paid_fees"])==0


def test_depth_slippage_costs_never_double_charged_and_market_minimum(desk):
    m=desk.feed.markets["BTC/USD"]
    m.asks={D("10000.1"):D(".001"),D("10000.2"):D(2)}
    p=opened(desk)
    snapshot=desk.engine.snapshot(desk.user)
    assert D(snapshot["costs"]["spread"])>0
    assert D(snapshot["costs"]["depthImpact"])>0
    assert D(snapshot["costs"]["slippage"])>0
    assert snapshot["costs"]["tradingFee"]=="0.000000"
    with transaction(desk.db):t=desk.engine.close(p["id"],"manual")
    assert D(t["pnl"])==D(t["gross"])
    assert D(desk.engine.snapshot(desk.user)["balance"])==D(10000)+D(t["pnl"])
    submit(desk,notional="10",riskBudget=".00001")
    refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)


@pytest.mark.parametrize("side",["long","short"])
def test_funding_sign_units_partial_allocation_restart_and_gaps(desk,side):
    p=opened(desk,side=side);initial=D(desk.engine.snapshot(desk.user)["balance"])
    sign=D(1) if side=="long" else D(-1)
    for _ in range(3):
        refresh(desk,1);desk.engine.tick()
    meta=desk.engine.meta(p)
    expected=D(10000)*D(p["qty"])*D(".0001")*3/3600*sign
    assert abs(D(meta["fundingRemaining"])-expected)<D("1e-20")
    assert abs(D(desk.engine.snapshot(desk.user)["balance"])-(initial-expected))<D("1e-20")
    with transaction(desk.db):first=desk.engine.close(p["id"],"partial",D(".5"))
    with transaction(desk.db):second=desk.engine.close(p["id"],"manual")
    snap=desk.engine.snapshot(desk.user)
    assert abs(D(snap["balance"])-D(snap["initial"])-D(first["pnl"])-D(second["pnl"]))<D("1e-20")
    p=opened(desk,side=side)
    refresh(desk,1000);desk.engine.tick()
    assert desk.engine.meta(p)["fundingGapSeconds"]==1000
    assert D(desk.engine.meta(p)["fundingRemaining"])==0


def test_stale_data_never_fills_and_recovery_protects_existing_position(desk):
    p=opened(desk)
    move(desk,p["symbol"],D(p["stop"])-10)
    m=desk.feed.markets[p["symbol"]];m.book_at=0
    desk.engine.tick()
    assert desk.engine.getPosition(desk.user)
    assert desk.engine.snapshot(desk.user)["equity"] is None
    with pytest.raises(ValueError):submit(desk,symbol="BTC/USD")
    refresh(desk);desk.engine.tick();refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)


def test_duplicate_orders_insufficient_margin_and_nonfinite_inputs(desk):
    o=submit(desk)
    with transaction(desk.db):
        again=desk.engine.submit(desk.user,json.loads(o["payload"]),o["client_id"])
    assert again["id"]==o["id"]
    assert desk.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0]==1
    for changes in ({"notional":"NaN"},{"notional":"invalid"},{"leverage":"99"},{"stopBps":"Infinity"},{"riskBudget":"0"},{"side":"oops"}):
        with pytest.raises(ValueError):submit(desk,**changes)
    desk.db.execute("UPDATE users SET balance='1'")
    refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)
    assert "balance" in desk.db.execute("SELECT error FROM paper_orders").fetchone()[0]


def test_config_live_disabled_and_wallet_database_inaccessible():
    with pytest.raises(NotImplementedError):LighterLiveAdapter()
    for kwargs in ({"mode":"LIVE"},{"db_path":ROOT/".runtime/vela.sqlite3"},{"max_leverage":"10"},
                   {"taker_fee":"NaN"},{"maker_fee":"-.1"},{"latency_ms":999999}):
        with pytest.raises(ValueError):PaperConfig(**kwargs)
    assert PaperConfig(loss_budget="999").loss_budget==2
    assert PaperConfig().maker_fee==PaperConfig().taker_fee==0


def book_message(desk,kind="subscribed/order_book",nonce=1,begin=0,**kw):
    return dict(type=kind,channel="order_book:0",timestamp=int(desk.clock[0]*1000),order_book=dict(
        nonce=nonce,begin_nonce=begin,bids=[dict(price="9999.9",size="2")],asks=[dict(price="10000.1",size="2")],**kw))


def test_websocket_snapshot_deltas_deletes_gap_recovery_and_stats(desk):
    f=desk.feed;m=f.markets["BTC/USD"]
    f.message(book_message(desk))
    delta=book_message(desk,"update/order_book",nonce=2,begin=1)
    delta["order_book"]["bids"]=[dict(price="9999.9",size="0"),dict(price="9999.8",size="3")]
    f.message(delta)
    assert D("9999.9") not in m.bids and m.bids[D("9999.8")]==3
    with pytest.raises(ValueError):f.message(book_message(desk,"update/order_book",nonce=4,begin=3))
    assert m.nonce is None and not m.fresh()
    f.message(book_message(desk,nonce=100));assert m.fresh()
    f.message(dict(type="update/market_stats",channel="market_stats:0",timestamp=int(desk.clock[0]*1000),
        market_stats=dict(mark_price="10001",index_price="10002",current_funding_rate=".0012",funding_rate="-.0010",funding_timestamp=desk.clock[0]*1000)))
    assert m.funding_rate==D(".000012") and m.last_funding_rate==D("-.000010")
    stale=book_message(desk);stale["timestamp"]-=60000
    with pytest.raises(ValueError):f.message(stale)
    assert not m.fresh()


def test_api_capability_isolation_no_wallet_or_live_route(desk):
    model=SimpleNamespace(ready=False)
    app=create_app(desk.config,desk.feed,model,start_background=False)
    with TestClient(app) as client:
        h={"X-Vela-Paper":desk.token}
        assert client.get("/api/paper/account").status_code==401
        assert client.post("/api/paper/session",headers=h,json={}).status_code==200
        assert client.post("/api/paper/session",headers={"X-Vela-Paper":"invalid"},json={}).status_code==400
        assert client.get("/api/paper/account",headers={"X-Vela-Paper":"b"*64}).status_code==401
        payload=dict(clientId="api-test",symbol="BTC/USD",side="short",type="market",notional="100")
        result=client.post("/api/paper/orders",headers=h,json=payload)
        assert result.status_code==202
        assert client.post("/api/paper/orders",headers=h,json=dict(payload,mode="LIVE")).status_code==422
        refresh(desk)
        client.portal.call(app.state.runtime.adapter.tick)
        a=client.get("/api/paper/account",headers=h).json()
        assert len(a["positions"])==1 and a["mode"]=="PAPER"
        client.post("/api/paper/session",headers={"X-Vela-Paper":"b"*64},json={})
        assert client.post(f"/api/paper/positions/{a['positions'][0]['id']}/close",headers={"X-Vela-Paper":"b"*64},
                           json=dict(clientId="steal",fraction="1")).status_code==409
        assert client.post("/api/paper/agent",headers=h,json=dict(symbol="BTC/USD",leverage="2",autonomous=True)).status_code==503
        assert client.post("/api/start-quote",json={}).status_code in (404,405)
        assert client.post("/api/v1/sendTx",json={}).status_code in (404,405)
        assert client.get("/.env").status_code==404
        assert client.get("/paper").status_code==200
        with client.websocket_connect("/api/paper/stream") as ws:
            data=ws.receive_json();assert data["mode"]=="PAPER" and not data["liveEnabled"]


def test_complete_existing_ai_pipeline_to_paper_execution(desk):
    calls=[]
    spec=dict(name="Fixture strategy",thesis="Deterministic test evidence only",market="BTC/USD",mode="swing",side="long",
        match="all",conditions=[dict(feature="volatility",op="gt",value=-1)],stop_bps=50,target_bps=75,
        max_hold_seconds=7200,entry="taker",breakeven_bps=0,trail_bps=0)
    class Model:
        ready=True;model="fixture-only"
        def ask(self,role,evidence,schema):
            calls.append((role,evidence))
            if role=="research":return dict(strategies=[spec],reason="Fixture observed 250 closed candles")
            if role=="review":return dict(verdict="confirm",reason="Fixture validation",sentiment=0)
            return dict(action="open",side="long",reason="Fixture approved after review")
    end=int(desk.clock[0])//300*300
    for i in range(250):
        desk.db.execute("INSERT INTO candles VALUES (?,?,?,?,?,?)",("BTC/USD",end-(250-i)*300,10000,10010,9990,10000))
    for m in desk.feed.markets.values():
        desk.db.execute("INSERT INTO prices VALUES (?,?,?,'lighter-public',1,'')",(m.symbol,str(m.mark),desk.clock[0]))
    put(desk.db,"paper-prefs:"+desk.user,dict(autonomous=True,symbol="BTC/USD",leverage="2"))
    agent=Agents(desk.db,Model(),eligible=lambda u:True,markets=lambda u:["BTC/USD"],entry_gate=lambda db,symbol:True)
    for _ in range(3):
        agent.tick_ai();refresh(desk,6)
    assert [role for role,evidence in calls]==["research","review","direction"]
    user=dict(desk.db.execute("SELECT * FROM users WHERE address=?",(desk.user,)).fetchone())
    signal=agent.signal(user)
    assert signal and signal["stop_bps"]=="50" and signal["target_bps"]=="75"
    desk.engine.open(desk.user,signal);refresh(desk);desk.engine.tick()
    p=desk.engine.getPosition(desk.user)[0]
    assert desk.engine.meta(p)["agent"]
    snapshot=desk.engine.snapshot(desk.user)
    assert snapshot["decision"]["answer"]["reason"]=="Fixture approved after review"
    move(desk,p["symbol"],D(p["target"])+1)
    desk.engine.tick();refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)
    assert len(desk.engine.snapshot(desk.user)["trades"])==1
    assert desk.db.execute("SELECT count(*) FROM outbox").fetchone()[0]==0


def test_history_and_win_loss_share_the_same_scopes(desk):
    p=opened(desk)
    with transaction(desk.db):desk.engine.close(p["id"],"manual")
    desk.db.execute("UPDATE trades SET closed=?",(desk.clock[0]-86400,))
    p=opened(desk)
    move(desk,p["symbol"],D(p["entry"])+10)
    with transaction(desk.db):desk.engine.close(p["id"],"manual")
    snap=desk.engine.snapshot(desk.user)
    assert snap["totals"]["trades"]==2 and snap["today"]["trades"]==1
    assert snap["totals"]["losses"]==1 and snap["today"]["losses"]==0
    assert snap["totals"]["wins"]==snap["today"]["wins"]==1


def test_protective_exits_override_pending_partials_and_rate_limits(desk):
    p=opened(desk)
    close=desk.engine.submit(desk.user,dict(reduceOnly=True,positionId=p["id"],fraction=".5"),"partial")
    for i in range(30):
        desk.db.execute("INSERT INTO paper_orders VALUES (?,?,?,?,?,?,'cancelled',?,NULL,NULL)",
            (uuid.uuid4().hex,desk.user,p["session"],f"rate-{i}",desk.clock[0],desk.clock[0],"{}"))
    move(desk,p["symbol"],D(p["stop"])-2)
    desk.engine.tick()
    assert desk.db.execute("SELECT status FROM paper_orders WHERE id=?",(close["id"],)).fetchone()[0]=="cancelled"
    refresh(desk);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)
    assert desk.db.execute("SELECT reason FROM trades").fetchone()[0]=="stop"


def test_old_market_orders_expire_before_any_restart_fill(desk):
    submit(desk);refresh(desk,31);desk.engine.tick()
    assert not desk.engine.getPosition(desk.user)
    assert desk.db.execute("SELECT status FROM paper_orders").fetchone()[0]=="expired"


def test_partial_close_dust_rejected_before_queue(desk):
    p=opened(desk)
    with pytest.raises(ValueError,match="precision"):
        desk.engine.submit(desk.user,dict(reduceOnly=True,positionId=p["id"],fraction=".0000000001"),"dust")
    assert not desk.db.execute("SELECT 1 FROM paper_orders WHERE status='pending'").fetchone()


def test_upstream_websocket_reconnect_resubscribes_and_responds_to_ping(desk,monkeypatch):
    from vela.lighter import feed as module
    sent=[];connections=[];backoffs=[]
    class Socket:
        def __init__(self,index):
            self.index=index
            self.messages=[{"type":"ping"},book_message(desk,nonce=1 if index==0 else 100)]
            if index==0:self.messages.append(book_message(desk,"update/order_book",nonce=4,begin=3))
        async def send(self,raw):sent.append(json.loads(raw))
        async def recv(self):
            if not self.messages:raise asyncio.CancelledError
            return json.dumps(self.messages.pop(0))
    class Connection:
        async def __aenter__(self):
            socket=Socket(len(connections));connections.append(socket);return socket
        async def __aexit__(self,*args):return False
    async def sleep(seconds):backoffs.append(seconds)
    monkeypatch.setattr(module,"connect",lambda *a,**k:Connection())
    monkeypatch.setattr(module.asyncio,"sleep",sleep)
    with pytest.raises(asyncio.CancelledError):asyncio.run(desk.feed.stream())
    assert len(connections)==2 and desk.feed.reconnects==1
    assert desk.feed.markets["BTC/USD"].nonce==100
    assert len([m for m in sent if m["type"]=="subscribe"])==12
    assert len([m for m in sent if m["type"]=="pong"])==2
    assert len(backoffs)==1 and 1<=backoffs[0]<=2
    assert {m["type"] for m in sent}=={"subscribe","pong"}


@pytest.mark.parametrize("side",["long","short"])
def test_reconciliation_persists_new_stricter_limit_across_restart(desk,side):
    p=opened(desk,side=side,notional="300")
    desk.config.loss_budget=D(".7")
    desk.engine.tick()
    assert desk.engine.meta(p)["riskBudget"]=="0.7"
    desk.config.loss_budget=D(2)
    adapter=LighterPaperAdapter(desk.db,desk.config,desk.feed)
    with transaction(desk.db):adapter.close(p["id"],"partial",D(".3"))
    adapter.tick()
    p=adapter.getPosition(desk.user)[0]
    assert net_at_stop(p,D(0))>=-D(".7")


def test_private_model_file_is_allowlisted_and_never_executes_shell(desk):
    import importlib.util
    spec=importlib.util.spec_from_file_location("paper_launcher",ROOT/"tools/run_lighter_paper.py")
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    path=desk.config.db_path.with_name("fixture.env")
    path.write_text('VELA_MODEL=fixture\nVELA_MODEL_KEY="$(echo no-execution)"\n')
    path.chmod(0o600)
    with pytest.raises(ValueError):launcher.load_model_file(path)  # whitespace invalid, never evaluated
    path.write_text('VELA_MODEL=fixture\nVELA_MODEL_KEY=fixture-only-not-a-secret\n')
    assert launcher.load_model_file(path)["VELA_MODEL"]=="fixture"
    path.chmod(0o644)
    with pytest.raises(ValueError):launcher.load_model_file(path)
    path.chmod(0o600)
    path.write_text('VELA_MODEL=fixture\nVELA_MODEL_KEY=fixture\nVELA_EXECUTION_MODE=LIVE\n')
    with pytest.raises(ValueError):launcher.load_model_file(path)
