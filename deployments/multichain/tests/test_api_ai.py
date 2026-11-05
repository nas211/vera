import asyncio,json,time
from decimal import Decimal as D
import httpx,pytest
from eth_account.messages import encode_defunct
from eth_account import Account
from eth_abi import encode
from web3 import Web3
from autodefi.api import create_app
from autodefi.trading import agents_for,ProtocolModel
from autodefi.chain import certificate
from autodefi.config import CHAIN_ID
from vela.db import transaction,get,put
from conftest import ALICE,BOB,ATTESTOR,funded,save_vault,fixture_feed

class FixtureModel:
    ready=True;model='DETERMINISTIC_FIXTURE_NOT_LIVE_AI'
    def __init__(self):self.calls=[]
    def ask(self,role,evidence,schema):
        self.calls.append(role)
        if role=='research':return dict(strategies=[dict(name='Fixture only',thesis='Deterministic integration verification, not a market forecast',market='BTC/USD',mode='swing',side='long',match='all',conditions=[dict(feature='return_1',op='gt',value=-1)],stop_bps=50,target_bps=100,max_hold_seconds=7200,entry='taker',breakeven_bps=0,trail_bps=0)],reason='Fixture')
        if role=='review':return dict(verdict='confirm',reason='Fixture',sentiment=0)
        return dict(side='long',action='open',reason='Fixture')

def test_reused_ai_three_stages_and_paper_fill(env):
    funded(env);db=env['db'];model=FixtureModel()
    for m in env['feed'].markets.values():
        for c in m.candles:db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(m.symbol,c['ts'],c['o'],c['h'],c['l'],c['c']))
    agents=agents_for(db,model,env['paper'])
    for _ in range(3):put(db,'ai-last-call',0);agents.tick_ai()
    assert model.calls==['research','review','direction']
    user=db.execute('SELECT * FROM users WHERE address=?',(env['owner'],)).fetchone();signal=agents.signal(user)
    assert signal and signal['symbol']=='BTC/USD'
    with transaction(db):env['paper'].open(env['owner'],signal)
    db.execute('UPDATE paper_orders SET due=0');env['paper'].tick()
    assert len(env['paper'].getPosition(env['owner']))==1
    with transaction(db):env['paper'].pause_entries(env['owner'])
    assert agents.signal(user) is None

def test_no_model_no_fake_decision_and_no_secret_inheritance(env,monkeypatch):
    monkeypatch.delenv('ADP_MODEL_KEY',raising=False);monkeypatch.delenv('ADP_MODEL',raising=False)
    for prefix in ('VELA_','VERA_PROTOCOL_'):
        for name in ('MODEL','MODEL_KEY','MODEL_ENDPOINT'):monkeypatch.delenv(prefix+name,raising=False)
    monkeypatch.setenv('WHY_DISCOVERY_MODEL_KEY','DO_NOT_USE')
    model=ProtocolModel();assert not model.ready and model.key==''
    funded(env)
    with pytest.raises(ValueError,match='not configured'):env['paper'].start(env['owner'],model)

def test_certificate_domain_and_signature(env):
    v=env['v'];c=certificate(env['config'],v,1,v['principalMicro'])
    digest=Web3.keccak(encode(['bytes32','uint256','address','address','uint256','uint8','bytes32','uint256','uint256','uint256','uint64'],[Web3.keccak(text='ADP_DEMO_V1'),CHAIN_ID,v['address'],v['owner'],v['loanNonce'],1,bytes.fromhex(v['loanId'][2:]),int(v['principalMicro']),0,0,c['deadline']]))
    assert Account.recover_message(encode_defunct(primitive=digest),signature=c['signature'])==ATTESTOR.address
    v['settlementSigner']=BOB.address
    with pytest.raises(ValueError,match='differs'):certificate(env['config'],v,1,800000000)

def test_authenticated_http_lifecycle_and_isolation(env):
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def scenario():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as client:
                assert (await client.get('/api/account')).status_code==401
                h={'Origin':env['config'].origin,'X-ADP-Client':'web'}
                assert (await client.post('/api/auth/nonce',json={})).status_code==403
                r=await client.post('/api/auth/nonce',headers=h,json=dict(address=ALICE.address,chainId=CHAIN_ID));assert r.status_code==200
                c=r.json();sig=ALICE.sign_message(encode_defunct(text=c['message'])).signature.hex()
                r=await client.post('/api/auth/verify',headers=h,json=dict(address=ALICE.address,nonce=c['nonce'],signature=sig));assert r.status_code==200
                assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
                assert (await client.get('/api/session')).json()['address']==env['owner']
                assert (await client.get('/api/account',headers={'X-Wallet-Address':BOB.address})).status_code==403
                assert (await client.get('/api/account')).json()['paper']['balance']=='0'
                h['Idempotency-Key']='api-borrow-1'
                r=await client.post('/api/borrow',headers=h,json={});assert r.status_code==200,r.text
                assert (await client.post('/api/borrow',headers=h,json={})).json()['loan']['original']=='800'
                assert (await client.get('/api/account')).json()['paper']['balance']=='800'
                assert (await client.post('/api/settle',headers={**h,'Idempotency-Key':'api-settle-1'},json={'confirm':False})).status_code==400
                assert (await client.get('/api/health')).json()['liveEnabled'] is False
                put(app.state.db,'ai-connection',dict(ok=True,at=time.time()-301,model='fixture'))
                assert (await client.get('/api/health')).json()['modelConnection']['fresh'] is False
                put(app.state.db,'ai-connection',dict(ok=True,at=time.time(),model='fixture'))
                assert (await client.get('/api/health')).json()['modelConnection']['fresh'] is True
                assert (await client.post('/api/live/order',headers=h,json={})).status_code==404
                assert (await client.post('/api/deposit/real',headers=h,json={})).status_code==404
                assert (await client.post('/api/logout',headers=h,json={})).status_code==200
                assert (await client.get('/api/account')).status_code==401
        finally:beat.cancel()
    asyncio.run(scenario());app.state.db.close()

def test_secure_cookie_https(env):
    env['config'].origin='https://defivela.xyz'
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as c:
                headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}
                challenge=(await c.post('/api/auth/nonce',headers=headers,json={'address':ALICE.address,'chainId':CHAIN_ID})).json()
                sig=ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
                r=await c.post('/api/auth/verify',headers=headers,json={'address':ALICE.address,'nonce':challenge['nonce'],'signature':sig})
                assert 'Secure' in r.headers['set-cookie']
                assert (await c.get('/api/session')).json()['address']==env['owner']
                assert (await c.get('/api/account')).status_code==200
                # Forwarded headers never override the configured signature/CSRF origin.
                rejected=await c.post('/api/auth/nonce',headers={**headers,'Origin':'https://foreign.invalid',
                    'X-Forwarded-Host':'defivela.xyz','X-Forwarded-Proto':'https'},
                    json={'address':ALICE.address,'chainId':CHAIN_ID})
                assert rejected.status_code==403
                await c.post('/api/logout',headers=headers,json={})
                assert (await c.get('/api/account')).status_code==401
        finally:beat.cancel()
    asyncio.run(run());app.state.db.close()

def test_websocket_market_route_contains_no_private_account_data(env):
    from starlette.websockets import WebSocketDisconnect
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def connect(origin):
        messages=[]
        scope=dict(type='websocket',asgi={'version':'3.0'},scheme='ws',path='/ws/markets',raw_path=b'/ws/markets',query_string=b'',headers=[(b'origin',origin.encode())],client=('127.0.0.1',123),server=('127.0.0.1',8802),subprotocols=[])
        async def receive():return {'type':'websocket.connect'}
        async def send(message):
            messages.append(message)
            if message['type']=='websocket.send':raise WebSocketDisconnect(1000)
        await app(scope,receive,send)
        return messages
    valid=asyncio.run(connect(env['config'].origin))
    payload=json.loads(next(m['text'] for m in valid if m['type']=='websocket.send'))
    assert payload['privateData'] is False and len(payload['markets'])==3
    assert 'balance' not in json.dumps(payload) and env['owner'] not in json.dumps(payload)
    invalid=asyncio.run(connect('https://foreign.invalid'))
    assert invalid[0]['type']=='websocket.close' and invalid[0]['code']==1008
    app.state.db.close()

def test_real_lending_is_disabled():
    from autodefi.real_lending import RealLendingDisabled
    with pytest.raises(RuntimeError,match='disabled'):RealLendingDisabled()

def test_vera_public_routes_brand_and_private_api_boundary(env):
    """Real ASGI handlers and built assets, not a network/browser test."""
    import re
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as c:
                for path in ('/','/vault','/overview','/borrow','/trading','/repay','/how-it-works'):
                    response=await c.get(path)
                    assert response.status_code==200 and '<title>VELA — Multichain AI-Powered DeFi Trading</title>' in response.text
                    asset=re.search(r'<script type="module" src="([^"]+)"',response.text)[1]
                    assert (await c.get(asset)).status_code==200
                config=(await c.get('/api/config')).json()
                assert config['brand']['name']=='VELA' and config['mode']=='PAPER' and config['lending']=='DEMO'
                assert config['liveEnabled'] is False and config['realFunding'] is False
                assert 'attestor_key' not in config and 'modelKey' not in config
                assert (await c.get('/api/account')).status_code==401
                h={'Origin':env['config'].origin,'X-ADP-Client':'web'}
                challenge=(await c.post('/api/auth/nonce',headers=h,json={'address':ALICE.address,'chainId':CHAIN_ID})).json()
                assert 'VELLA: authenticate only.' in challenge['message']
        finally:beat.cancel()
    asyncio.run(run());app.state.db.close()


def test_product_pages_and_legacy_trade_use_available_built_assets(env):
    import re
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin) as c:
            for path in ('/paper','/earn'):
                r=await c.get(path);assert r.status_code==200
                for asset in re.findall(r'(?:src|href)="(/product-assets/[^\"]+)"',r.text):
                    assert (await c.get(asset)).status_code==200,asset
            redirect=await c.get('/trade?source=flow')
            assert redirect.status_code==307 and redirect.headers['location']=='/trading?source=flow'
            assert (await c.get('/not-a-route')).status_code==404
    try:asyncio.run(run())
    finally:app.state.db.close()
