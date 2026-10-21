"""Real ASGI requests/signatures with deterministic markets/model, no TCP or wallet UI."""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from decimal import Decimal as D

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from starlette.websockets import WebSocketDisconnect

from test_lighter import desk, refresh, move, net_at_stop
from test_portal import Model
from vela.api import create_app
from vela.db import get, transaction
from vela.portal import CHAIN, COOKIE

ORIGIN='https://defivela.xyz'
TOKEN='integration-fixture-token-never-production-123456'
HEADERS={'Host':'plot-adds-vertical-workforce.trycloudflare.com',
         'X-Vela-Site-Token':TOKEN,'X-Vela-Public-Origin':ORIGIN,
         'Origin':ORIGIN,'X-Vela-Portal':'1','X-Vela-Client-IP':'192.0.2.7'}


def run(scenario):
    # This restricted executor cannot wake an idle selector from a worker thread.
    # Keep scheduling normally; real ASGI/threadpool handlers still execute.
    loop=asyncio.new_event_loop()
    async def execute():
        async def heartbeat():
            while True:await asyncio.sleep(.02)
        task=asyncio.create_task(heartbeat())
        try:return await asyncio.wait_for(scenario(),20)
        finally:
            await loop.shutdown_asyncgens()
            await loop.shutdown_default_executor()
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    try:return loop.run_until_complete(execute())
    finally:loop.close()


@asynccontextmanager
async def connected(settings,desk,model=None):
    app=create_app(settings,paper_settings=desk.config,feed=desk.feed,model=model or Model(),
                   start_background=False,portal_origin=ORIGIN,proxy_token=TOKEN)
    async with app.router.lifespan_context(app):
        rt=app.state.runtime;rt.last_cycle=time.time()
        end=int(time.time())//300*300
        with transaction(rt.db):
            for m in desk.feed.markets.values():
                rt.db.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',
                    [(m.symbol,end-(250-i)*300,float(m.mark),float(m.mark)+1,float(m.mark)-1,float(m.mark)) for i in range(250)])
        rt.sync_data()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,headers=HEADERS) as client:
            yield app,client,rt


async def post(client,path,body=None,**kw):
    return await client.post('/api/portal/'+path,json={} if body is None else body,**kw)


async def login(client,key=None):
    key=key or Account.create()
    r=await post(client,'auth/challenge',{'address':key.address,'chainId':CHAIN})
    assert r.status_code==200,r.text
    challenge=r.json()
    assert 'URI: '+ORIGIN+'/trade' in challenge['message']
    signature='0x'+Account.sign_message(encode_defunct(text=challenge['message']),key.key).signature.hex().removeprefix('0x')
    signed={'challengeId':challenge['challengeId'],'signature':signature}
    response=await post(client,'auth/verify',signed)
    assert response.status_code==200,response.text
    return key,response,signed


def tick(rt,desk,seconds=.31):
    refresh(desk,seconds);rt.last_cycle=time.time();rt.sync_data();rt.adapter.tick()


def test_original_routes_and_new_routes_share_application(setup_db,desk):
    async def scenario():
        async with connected(setup_db[1],desk) as (app,client,rt):
            for path in ('/','/trade','/earn','/mydoge.html','/assets/portal.js'):
                r=await client.get(path);assert r.status_code==200,(path,r.text)
            assert (await client.get('/health')).json()['mode']=='testnet-paper'
            assert (await client.get('/api/config')).status_code==200
            legacy=(await client.get('/api/users/'+setup_db[2])).json()
            assert legacy['user']['balance']=='100'
            assert (await client.get('/api/paper/health')).json()['liveEnabled'] is False
            assert rt.settings.db_path!=setup_db[1].db_path
            assert (await client.get('/api/portal/account')).status_code==401
            assert (await client.get('/earn/')).headers['location']=='/earn'
    run(scenario)


@pytest.mark.parametrize('attack',['token','forwarded_only','origin','csrf','network','signature','replay'])
def test_public_wallet_auth_rejects_spoofing(setup_db,desk,attack):
    async def scenario():
        async with connected(setup_db[1],desk) as (_,client,rt):
            key=Account.create();body={'address':key.address,'chainId':CHAIN}
            if attack in ('token','forwarded_only','origin','csrf'):
                h={'token':{'X-Vela-Site-Token':'bad'},
                   'forwarded_only':{'X-Vela-Site-Token':'','X-Forwarded-Host':'defivela.xyz','X-Forwarded-Proto':'https'},
                   'origin':{'Origin':'https://evil.invalid'},'csrf':{'X-Vela-Portal':'0'}}[attack]
                assert (await post(client,'auth/challenge',body,headers=h)).status_code==403
            elif attack=='network':
                body['chainId']=1
                assert (await post(client,'auth/challenge',body)).status_code==409
            elif attack=='signature':
                challenge=(await post(client,'auth/challenge',body)).json()
                sig='0x'+Account.sign_message(encode_defunct(text=challenge['message']),Account.create().key).signature.hex().removeprefix('0x')
                assert (await post(client,'auth/verify',{'challengeId':challenge['challengeId'],'signature':sig})).status_code==401
            else:
                _,_,signed=await login(client)
                assert (await post(client,'auth/verify',signed)).status_code==401
    run(scenario)


def test_secure_session_deposit_restart_switch_and_disconnect(setup_db,desk):
    async def scenario():
        async with connected(setup_db[1],desk) as (_,client,rt):
            key,response,_=await login(client)
            cookie=response.headers['set-cookie']
            for flag in ('Secure','HttpOnly','SameSite=strict','Path=/api/portal'):assert flag in cookie
            assert response.json()['wallet']==key.address.lower()
            r=await post(client,'deposit',dict(clientId='persistent-deposit',amount='1000',asset='USDC'))
            assert r.status_code==200 and r.json()['balance']=='1000'
            assert not r.json()['realFundingEnabled']
            assert (await post(client,'deposit',dict(clientId='persistent-deposit',amount='1000'))).json()['balance']=='1000'
            assert (await post(client,'deposit',dict(clientId='persistent-deposit',amount='2000'))).status_code==409
            cookies=client.cookies
        # The same signed session and balance survive a full application lifespan restart.
        async with connected(setup_db[1],desk) as (_,client,rt):
            client.cookies.update(cookies)
            r=await client.get('/api/portal/account')
            assert r.json()['balance']=='1000' and r.json()['wallet']==key.address.lower()
            assert r.headers['cache-control']=='no-store'
            assert (await post(client,'withdraw',dict(clientId='withdraw-once',amount='250'))).json()['balance']=='750'
            assert (await post(client,'withdraw',dict(clientId='withdraw-once',amount='250'))).json()['balance']=='750'
            _,other,_=await login(client)
            assert other.json()['balance']=='0' and other.json()['wallet']!=key.address.lower()
            await login(client,key)
            assert (await client.get('/api/portal/account')).json()['balance']=='750'
            assert (await post(client,'auth/logout')).status_code==200
            assert (await client.get('/api/portal/account')).status_code==401
            assert rt.db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
        assert setup_db[0].execute('SELECT balance FROM users WHERE address=?',(setup_db[2],)).fetchone()[0]=='100'
    run(scenario)


@pytest.mark.parametrize('side',['long','short'])
def test_integrated_ai_start_protected_position_pnl_and_stop(setup_db,desk,side):
    async def scenario():
        model=Model();model.side=side
        async with connected(setup_db[1],desk,model) as (_,client,rt):
            key,_,_=await login(client)
            await post(client,'deposit',dict(clientId='ai-demo-deposit',amount='1000'))
            rt.settings.taker_fee=D('.001')
            started=await post(client,'start');assert started.status_code==200,started.text
            for _ in range(3):rt.agents(rt.db).tick_ai();tick(rt,desk,6)
            user=dict(rt.db.execute('SELECT * FROM users WHERE address=?',('wallet:'+key.address.lower(),)).fetchone())
            signal=rt.agents(rt.db).signal(user);assert signal and signal['side']==side
            rt.adapter.open(user['address'],signal);tick(rt,desk)
            p=rt.adapter.getPosition(user['address'])[0]
            assert net_at_stop(p,D('.001'))>=-D(2)
            before=(await client.get('/api/portal/account')).json()['pnl']
            move(desk,p['symbol'],D(p['entry'])+(1 if side=='long' else -1));tick(rt,desk)
            after=(await client.get('/api/portal/account')).json()['pnl'];assert before!=after
            # Stop cancels pending entries while preserving the existing position and its stop.
            pending=await post(client,'orders',dict(clientId='pending-entry',symbol='ETH/USD',side=side,notional='100'))
            assert pending.status_code==202,pending.text
            stopped=await post(client,'stop');assert stopped.status_code==200
            assert not stopped.json()['preferences']['autonomous'] and len(stopped.json()['positions'])==1
            assert rt.db.execute('SELECT status FROM paper_orders WHERE id=?',(pending.json()['id'],)).fetchone()[0]=='cancelled'
            assert (await post(client,'close-all',dict(clientId='close-fixture',confirm=True))).status_code==200
            tick(rt,desk)
            account=(await client.get('/api/portal/account')).json()
            assert not account['positions'] and account['trades']
            # Public P&L is reported to six decimals; the ledger retains precision.
            assert abs(D(account['balance'])-D(account['initial'])-D(account['pnl']))<D('.000001')
            assert account['today']['trades']==account['totals']['trades']==1
            assert rt.db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
    run(scenario)


def test_authenticated_market_websocket_uses_shared_runtime(setup_db,desk):
    async def scenario():
        async with connected(setup_db[1],desk) as (app,_,rt):
            messages=[]
            scope={'type':'websocket','asgi':{'version':'3.0'},'scheme':'wss','path':'/api/paper/stream',
                   'raw_path':b'/api/paper/stream','query_string':b'', 'root_path':'',
                   'server':('backend',443),'client':('127.0.0.1',1),'subprotocols':[],
                   'headers':[(k.lower().encode(),v.encode()) for k,v in HEADERS.items()]}
            async def receive():return {'type':'websocket.connect'}
            async def send(message):
                messages.append(message)
                if message['type']=='websocket.send':raise WebSocketDisconnect()
            await app(scope,receive,send)
            assert messages[0]['type']=='websocket.accept'
            market=json.loads(messages[1]['text'])
            assert market['mode']=='PAPER' and not market['liveEnabled'] and len(market['markets'])==3
            scope['headers']=[(b'host',b'defivela.xyz')]
            messages.clear();await app(scope,receive,send)
            assert messages==[{'type':'websocket.close','code':1008}]
    run(scenario)


def test_refuse_shared_database_and_live_mode(setup_db,desk,monkeypatch):
    from vela.lighter.config import PaperConfig
    from vela.settings import Settings
    with pytest.raises(ValueError,match='separate'):
        create_app(Settings(db_path=desk.config.db_path),paper_settings=desk.config)
    monkeypatch.setenv('VELA_EXECUTION_MODE','LIVE')
    with pytest.raises(ValueError,match='LIVE is disabled'):create_app(setup_db[1])
