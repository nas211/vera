import asyncio
from decimal import Decimal as D
import httpx
from eth_account.messages import encode_defunct
from autodefi.api import create_app
from test_api_ai import FixtureModel
from conftest import ALICE, BOB


def test_wallet_staking_lifecycle_idempotency_and_disabled_gate(env,monkeypatch):
    env['config'].demo_staking=True
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    from autodefi.staking import DemoStaking
    original=DemoStaking.__init__;clock=[0]
    def init(self,db,paper):original(self,db,paper,clock=lambda:1000000000+clock[0])
    monkeypatch.setattr(DemoStaking,'__init__',init)
    async def scenario():
        h={'Origin':env['config'].origin,'X-ADP-Client':'web'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,headers=h) as c:
            assert (await c.get('/api/staking')).status_code==401
            async def login(wallet):
                challenge=(await c.post('/api/auth/nonce',json={'address':wallet.address,'chainId':6281971})).json()
                signature=wallet.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
                r=await c.post('/api/auth/verify',json={'address':wallet.address,'nonce':challenge['nonce'],'signature':signature});assert r.status_code==200
            await login(ALICE)
            r=await c.post('/api/trading/allocate',json={'amount':'1000','confirm':True},headers={'Idempotency-Key':'stake-capital'});assert r.status_code==200
            body={'amount':'500','confirm':True};hkey={'Idempotency-Key':'stake-deposit'}
            r=await c.post('/api/staking/deposit',json=body,headers=hkey);assert r.status_code==200,r.text
            assert (await c.post('/api/staking/deposit',json=body,headers=hkey)).json()==r.json()
            clock[0]=31536000
            assert (await c.get('/api/staking')).json()['claimable']=='25.000000'
            claim=await c.post('/api/staking/claim',json={'confirm':True},headers={'Idempotency-Key':'stake-claim'});assert claim.status_code==200
            assert (await c.post('/api/staking/claim',json={'confirm':True},headers={'Idempotency-Key':'stake-claim'})).json()==claim.json()
            await login(BOB)
            assert (await c.get('/api/staking')).json()['principal']=='0'
            assert (await c.post('/api/staking/withdraw',json=body,headers={'Idempotency-Key':'foreign-withdraw'})).status_code==400
            await login(ALICE)
            r=await c.post('/api/staking/withdraw',json=body,headers={'Idempotency-Key':'stake-withdraw'});assert r.status_code==200
            snap=(await c.get('/api/account')).json()['paper']
            assert D(snap['balance'])==1025 and D(snap['pnl'])==0
            assert (await c.get('/api/staking')).json()['claimed']=='25.000000'
            env['config'].demo_staking=False
            assert (await c.post('/api/staking/deposit',json=body,headers={'Idempotency-Key':'disabled-deposit'})).status_code==503
    try:asyncio.run(scenario())
    finally:app.state.db.close()
