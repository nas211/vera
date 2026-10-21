"""Wallet signatures are real cryptographic fixtures; markets/model are deterministic.

No wallet funding, external network, model service or production ledger is used.
"""
import asyncio
import time
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from test_lighter import desk, refresh, move, net_at_stop
from vela.db import get, put, transaction
from vela.api import create_app
from vela.settings import Settings
from vela.lighter.engine import LighterPaperAdapter
from vela.portal import CHAIN, COOKIE, digest

class Model:
    ready=True
    model='deterministic-fixture'
    side='long'
    before_answer=None
    def __init__(self):self.calls=[]
    def ask(self,role,evidence,schema):
        self.calls.append(role)
        if self.before_answer:self.before_answer()
        if role=='research':
            spec=dict(name='Fixture',thesis='Deterministic regression only',market='BTC/USD',mode='swing',side=self.side,
                match='all',conditions=[dict(feature='volatility',op='gt',value=-1)],stop_bps=50,target_bps=75,
                max_hold_seconds=7200,entry='taker',breakeven_bps=0,trail_bps=0)
            return dict(strategies=[spec],reason='Fixture research')
        if role=='review':return dict(verdict='confirm',reason='Fixture review',sentiment=0)
        return dict(action='open',side=self.side,reason='Fixture direction after review')

@pytest.fixture
def portal(desk,monkeypatch):
    # Background loops are disabled. Permit the test thread to inspect the
    # lifespan connection between synchronous HTTP calls; production stays strict.
    import sqlite3
    real_connect=sqlite3.connect
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:real_connect(*a,**dict(kw,check_same_thread=False)))
    monkeypatch.setenv('VELA_PORTAL_ORIGIN','http://testserver')
    model=Model();app=create_app(Settings(db_path=desk.config.db_path.parent/'legacy.sqlite3'),
        paper_settings=desk.config,feed=desk.feed,model=model,start_background=False,
        portal_origin='http://testserver')
    with TestClient(app) as client:
        client.headers.update({'Origin':'http://testserver','X-Vela-Portal':'1'})
        rt=app.state.runtime;rt.last_cycle=time.time()
        end=int(time.time())//300*300
        with transaction(rt.db):
            for m in desk.feed.markets.values():
                rt.db.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',
                    [(m.symbol,end-(250-i)*300,float(m.mark),float(m.mark)+1,float(m.mark)-1,float(m.mark)) for i in range(250)])
        rt.sync_data()
        yield SimpleNamespace(client=client,rt=rt,desk=desk,model=model)

def post(p,path,body=None):return p.client.post('/api/portal/'+path,json={} if body is None else body)
def login(p,key=None):
    key=key or Account.create()
    challenge=post(p,'auth/challenge',dict(address=key.address,chainId=CHAIN))
    assert challenge.status_code==200,challenge.text
    c=challenge.json();sig='0x'+Account.sign_message(encode_defunct(text=c['message']),key.key).signature.hex().removeprefix('0x')
    response=post(p,'auth/verify',dict(challengeId=c['challengeId'],signature=sig))
    assert response.status_code==200,response.text
    return key,response.json(),dict(challengeId=c['challengeId'],signature=sig)
def deposit(p,amount='1000',client_id=None):
    r=post(p,'deposit',dict(clientId=client_id or uuid.uuid4().hex,amount=amount,asset='USDC'))
    assert r.status_code==200,r.text
    return r.json()
def tick(p,seconds=.31):
    refresh(p.desk,seconds);p.rt.last_cycle=time.time();p.rt.sync_data();p.rt.adapter.tick()
def order(p,side='long',symbol='BTC/USD',**kw):
    r=post(p,'orders',dict(clientId=uuid.uuid4().hex,symbol=symbol,side=side,notional='300',**kw))
    assert r.status_code==202,r.text
    return r.json()
def snap(p):return p.client.get('/api/portal/account').json()

def test_demo_pages_and_legacy_wallet_routes(portal):
    for path in ('/', '/trade', '/earn', '/earn.html'):
        response=portal.client.get(path)
        assert response.status_code==200
        assert response.headers['content-type'].startswith('text/html')
    assert 'Staking &amp; APY' in portal.client.get('/earn').text
    # The unified application preserves the existing wallet page and its assets.
    assert portal.client.get('/mydoge.html?wallet=metamask').status_code==200
    for path,target in (('/earn/','/earn'),):
        response=portal.client.get(path,follow_redirects=False)
        assert response.status_code==307
        assert response.headers['location']==target
    # Aliases do not authenticate, start trading or change demo balances.
    assert portal.rt.db.execute('SELECT count(*) FROM portal_sessions').fetchone()[0]==0

def test_signature_one_use_origin_binding_cookie_and_empty_account(portal):
    key,a,signed=login(portal)
    assert a['wallet']==key.address.lower() and a['balance']=='0' and a['positions']==[]
    assert a['demo'] and a['realFundingEnabled'] is False
    assert a['today']['trades']==a['totals']['trades']==0
    assert post(portal,'auth/verify',signed).status_code==401
    assert portal.client.get('/api/portal/account').headers['cache-control']=='no-store'
    assert portal.rt.db.execute('SELECT id FROM portal_sessions').fetchone()[0]==digest(portal.client.cookies[COOKIE])

@pytest.mark.parametrize('case',['network','signer','expired','origin','host','csrf'])
def test_reject_invalid_auth(portal,case):
    key=Account.create()
    if case in ('origin','host','csrf'):
        headers={'origin':'https://evil.invalid'} if case=='origin' else {'host':'evil.invalid'} if case=='host' else {'x-vela-portal':'0'}
        assert portal.client.post('/api/portal/auth/challenge',json=dict(address=key.address,chainId=CHAIN),headers=headers).status_code==403
        return
    c=post(portal,'auth/challenge',dict(address=key.address,chainId=1 if case=='network' else CHAIN))
    if case=='network':assert c.status_code==409;return
    c=c.json()
    if case=='expired':refresh(portal.desk,301)
    sig=Account.sign_message(encode_defunct(text=c['message']),(Account.create() if case=='signer' else key).key).signature.hex()
    assert post(portal,'auth/verify',dict(challengeId=c['challengeId'],signature='0x'+sig.removeprefix('0x'))).status_code==401

def test_deposit_retries_isolation_and_cash_flows_not_profit(portal):
    key,_,_=login(portal);a=deposit(portal,client_id='same-request')
    assert a['balance']=='1000' and a['pnl']=='0.000000'
    assert deposit(portal,client_id='same-request')['balance']=='1000'
    assert post(portal,'deposit',dict(clientId='same-request',amount='2000')).status_code==409
    assert post(portal,'deposit',dict(clientId='unsupported',amount='100',asset='DOGE')).status_code==409
    other,b,_=login(portal);assert other.address!=key.address and b['balance']=='0'
    login(portal,key);assert snap(portal)['balance']=='1000'
    r=post(portal,'withdraw',dict(clientId='withdraw-once',amount='400')).json()
    assert r['balance']=='600' and D(r['pnl'])==0
    assert post(portal,'withdraw',dict(clientId='withdraw-once',amount='400')).json()['balance']=='600'
    assert post(portal,'withdraw',dict(clientId='overdraw',amount='700')).status_code==409

@pytest.mark.parametrize('amount',['NaN','Infinity','-1','0','0.5','100001','1.0000001'])
def test_demo_amount_validation(portal,amount):
    login(portal)
    assert post(portal,'deposit',dict(clientId='invalid-amount',amount=amount)).status_code==422
    assert snap(portal)['balance']=='0'

def test_start_checks_model_balance_feed_engine_and_preferences(portal):
    login(portal)
    assert post(portal,'start').status_code==409
    deposit(portal);portal.model.ready=False
    assert post(portal,'start').status_code==503
    portal.model.ready=True;portal.rt.error='fixture failure'
    assert post(portal,'start').status_code==503
    portal.rt.error='';portal.rt.feed.markets['BTC/USD'].book_at=0
    assert post(portal,'start').status_code==503
    tick(portal)
    assert post(portal,'start').json()['preferences']['autonomous'] is True
    assert post(portal,'preferences',dict(leverage=100)).status_code==422
    assert post(portal,'preferences',dict(leverage=3)).json()['preferences']['leverage']=='3'
    assert post(portal,'withdraw',dict(clientId='active-withdraw',amount='20')).status_code==409

@pytest.mark.parametrize('side',['long','short'])
def test_stop_cancels_entries_preserves_position_risk_and_restart(portal,side):
    _,a,_=login(portal);deposit(portal)
    portal.rt.settings.taker_fee=D('.001')
    order(portal,side,riskBudget='.7');tick(portal)
    user='wallet:'+a['wallet'];p=portal.rt.adapter.getPosition(user)[0]
    pending=order(portal,side,symbol='ETH/USD')
    paused=post(portal,'stop').json()
    assert len(paused['positions'])==1 and not paused['preferences']['autonomous']
    assert portal.rt.db.execute('SELECT status FROM paper_orders WHERE id=?',(pending['id'],)).fetchone()[0]=='cancelled'
    assert post(portal,'withdraw',dict(clientId='exposed-withdraw',amount='20')).status_code==409
    # A partial close followed by corrupted persisted stop must retain the stricter cap.
    assert post(portal,f"positions/{p['id']}/close",dict(clientId='partial-close',fraction='.5')).status_code==202
    tick(portal)
    portal.rt.db.execute('UPDATE positions SET stop=? WHERE id=?',('1' if side=='long' else '999999',p['id']))
    portal.rt.adapter=LighterPaperAdapter(portal.rt.db,portal.rt.settings,portal.rt.feed)
    portal.rt.adapter.tick();p=portal.rt.adapter.getPosition(user)[0]
    assert net_at_stop(p,D('.001'))>=-D('.7')
    move(portal.desk,p['symbol'],D(p['stop'])+(-1 if side=='long' else 1))
    portal.rt.adapter.tick();tick(portal)
    a=snap(portal);assert not a['positions'] and a['totals']['trades']==2
    assert abs(D(a['balance'])-D(a['initial'])-sum(D(t['pnl']) for t in a['trades']))<D('.000001')
    assert portal.rt.db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0

@pytest.mark.parametrize('side',['long','short'])
def test_authenticated_ai_pipeline_to_protected_execution(portal,side):
    portal.model.side=side;_,a,_=login(portal);deposit(portal)
    assert post(portal,'start').status_code==200
    for _ in range(3):
        portal.rt.agents(portal.rt.db).tick_ai();tick(portal,6)
    user=dict(portal.rt.db.execute('SELECT * FROM users WHERE address=?',('wallet:'+a['wallet'],)).fetchone())
    signal=portal.rt.agents(portal.rt.db).signal(user)
    assert signal and signal['side']==side
    portal.rt.adapter.open(user['address'],signal);tick(portal)
    p=snap(portal)['positions'][0];assert p['side']==side
    assert net_at_stop(portal.rt.adapter.getPosition(user['address'])[0],D(0))>=-D(2)
    assert snap(portal)['decision']['answer']['reason']=='Fixture direction after review'
    assert post(portal,'close-all',dict(clientId='close-all-fixture',confirm=False)).status_code==409
    assert post(portal,'close-all',dict(clientId='close-all-fixture',confirm=True)).status_code==200
    tick(portal);assert snap(portal)['positions']==[]

def test_stop_during_model_response_discards_approval(portal):
    _,a,_=login(portal);deposit(portal);post(portal,'start')
    user='wallet:'+a['wallet']
    portal.model.before_answer=lambda:portal.rt.adapter.pause_entries(user)
    portal.rt.agents(portal.rt.db).tick_ai()
    assert not get(portal.rt.db,'paper-prefs:'+user)['autonomous']
    assert not get(portal.rt.db,'ai-approved:'+user)
    assert not get(portal.rt.db,'ai-stage:'+user)

def test_stop_during_start_connectivity_prevents_late_start(portal):
    _,a,_=login(portal);deposit(portal);user='wallet:'+a['wallet']
    portal.model.before_answer=lambda:portal.rt.adapter.pause_entries(user)
    assert post(portal,'start').status_code==409
    assert not snap(portal)['preferences']['autonomous']

def test_logout_and_expiry_pause_entries_and_retain_protection(portal):
    key,a,_=login(portal);deposit(portal);post(portal,'start');pending=order(portal)
    assert post(portal,'auth/logout').status_code==200
    assert portal.client.get('/api/portal/account').status_code==401
    user='wallet:'+a['wallet']
    assert not get(portal.rt.db,'paper-prefs:'+user)['autonomous']
    assert portal.rt.db.execute('SELECT status FROM paper_orders WHERE id=?',(pending['id'],)).fetchone()[0]=='cancelled'
    login(portal,key);post(portal,'start');pending=order(portal)
    portal.rt.db.execute('UPDATE portal_sessions SET expires=?',(time.time()-1,))
    portal.rt.portal.expire_agents();tick(portal)
    assert portal.rt.adapter.getPosition(user)==[]
    assert not get(portal.rt.db,'paper-prefs:'+user)['autonomous']

def test_reset_requires_idle_confirmation_archives_and_never_recredits(portal):
    _,a,_=login(portal);deposit(portal,client_id='before-reset')
    order(portal);tick(portal)
    assert post(portal,'reset',dict(clientId='reset-demo',confirm=True)).status_code==409
    post(portal,'close-all',dict(clientId='close-before-reset',confirm=True));tick(portal)
    assert post(portal,'reset',dict(clientId='reset-demo')).status_code==409
    reset=post(portal,'reset',dict(clientId='reset-demo',confirm=True)).json()
    assert reset['balance']=='0' and reset['today']['trades']==0 and reset['totals']['trades']==0
    assert deposit(portal,client_id='before-reset')['balance']=='0'
    assert len(reset['transactions'])==2
    assert post(portal,'reset',dict(clientId='reset-demo',confirm=True)).json()['balance']=='0'
    rows=portal.rt.db.execute("SELECT value FROM kv WHERE key LIKE 'portal-archive:%'").fetchall()
    assert len(rows)==1

def test_expired_model_state_and_daily_budget(portal):
    login(portal);deposit(portal)
    put(portal.rt.db,'ai-connection',dict(ok=True,at=time.time()-301))
    assert not snap(portal)['model']['connected']
    put(portal.rt.db,'ai-calls',[time.time()]*200)
    assert post(portal,'start').status_code==429

def test_old_pending_entries_block_withdrawal_and_expire_with_session(portal):
    _,a,_=login(portal);deposit(portal);pending=order(portal,type='limit',limitPrice='5000')
    user='wallet:'+a['wallet']
    for i in range(35):
        portal.rt.db.execute("INSERT INTO paper_orders VALUES (?,?,?,?,?,?,'cancelled','{}',NULL,NULL)",
            (uuid.uuid4().hex,user,'fixture',uuid.uuid4().hex,time.time()+i+1,time.time()))
    assert not any(o['status']=='pending' for o in snap(portal)['orders'])
    assert post(portal,'withdraw',dict(clientId='hidden-pending',amount='100')).status_code==409
    assert post(portal,'reset',dict(clientId='hidden-pending',confirm=True)).status_code==409
    portal.rt.db.execute('UPDATE portal_sessions SET expires=?',(time.time()-1,))
    portal.rt.portal.expire_agents()
    assert portal.rt.db.execute('SELECT status FROM paper_orders WHERE id=?',(pending['id'],)).fetchone()[0]=='cancelled'

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('action',['exit','hold','flip','error'])
def test_model_monitor_is_reduction_only_and_keeps_stop_controls(portal,side,action):
    from vela.lighter.monitor import review_position,consume_exits
    _,a,_=login(portal);deposit(portal);order(portal,side);tick(portal)
    user='wallet:'+a['wallet'];p=portal.rt.adapter.getPosition(user)[0]
    put(portal.rt.db,'paper-position:'+p['id'],dict(portal.rt.adapter.meta(p),agent=True))
    tick(portal,31)
    def answer(*args):
        if action=='error':raise RuntimeError('fixture failure')
        return dict(action=action,side=side,reason='Deterministic position review')
    portal.model.ask=answer
    assert review_position(portal.rt.db,portal.model)
    consume_exits(portal.rt.adapter);tick(portal)
    positions=portal.rt.adapter.getPosition(user)
    if action=='exit':
        assert not positions and snap(portal)['trades'][0]['reason']=='ai_exit'
    else:
        assert len(positions)==1 and positions[0]['side']==side
        assert net_at_stop(positions[0],D(0))>=-D(2)
