"""ASGI integration against real handlers and isolated SQLite fixtures."""
import asyncio
from decimal import Decimal as D
import httpx
from eth_account.messages import encode_defunct
from autodefi.api import create_app
from autodefi.config import CHAIN_ID
from conftest import ALICE,BOB,funded
from test_api_ai import FixtureModel
from vela.db import get


def test_modular_api_auth_idempotency_receipts_and_logout(env):
    funded(env)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}
        async def login(c,key):
            challenge=(await c.post('/api/auth/nonce',headers=headers,json={'address':key.address,'chainId':CHAIN_ID})).json()
            signature=key.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
            response=await c.post('/api/auth/verify',headers=headers,json={'address':key.address,'nonce':challenge['nonce'],'signature':signature})
            assert response.status_code==200
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as c:
                assert (await c.get('/api/config')).json()['moduleApiVersion']==1
                await login(c,ALICE)
                async def post(path,body,key):return await c.post(path,headers={**headers,'Idempotency-Key':key},json=body)
                body={'amount':'100','confirm':True}
                a=await post('/api/repay',body,'api-partial-repay');b=await post('/api/repay',body,'api-partial-repay')
                assert a.status_code==200,a.text
                assert a.json()==b.json() and D(a.json()['loan']['principal'])==700
                assert a.json()['disclosure']=='Simulated repayment; no stablecoin transferred'
                assert (await post('/api/repay',{'amount':'1','confirm':False},'api-no-confirm')).status_code==400
                assert (await c.post('/api/repay',headers={**headers,'Idempotency-Key':'foreign-wallet','X-Wallet-Address':BOB.address},json=body)).status_code==403
                result=await post('/api/repayment/policy',{'confirm':True,'sweepPercent':'25','frequency':'weekly','maximum':'10','minimum':'50'},'api-policy-create')
                assert result.status_code==200,result.text
                snapshot=(await c.get('/api/account')).json()
                assert snapshot['repayment']['policy']['enabled'] and D(snapshot['agent']['loan']['principal'])==700
                assert (await post('/api/trading/allocate',{'amount':'100','confirm':True},'no-faucet-with-debt')).status_code==400
                await c.post('/api/logout',headers=headers,json={})
                assert (await c.get('/api/account')).status_code==401
                assert not get(app.state.db,'repayment-policy:'+env['owner'])['enabled']
                await login(c,BOB)
                allocation=await post('/api/trading/allocate',{'amount':'500','confirm':True},'api-direct-capital')
                assert allocation.status_code==200,allocation.text
                assert (await post('/api/trading/allocate',{'amount':'500','confirm':True},'api-direct-capital')).json()==allocation.json()
                snapshot=(await c.get('/api/account')).json()
                assert snapshot['vault'] is None and snapshot['health'] is None and snapshot['paper']['balance']=='500'
                result=await post('/api/trading/permission',dict(confirm=True,allocation='500',positionSize='100',maxLoss='1',leverage='1'),'api-paper-permission')
                assert result.status_code==200,result.text
                result=await post('/api/trading/start',{'confirm':True},'api-direct-start')
                assert result.status_code==200,result.text
                result=await post('/api/trading/withdraw',{'amount':'100','confirm':True},'api-direct-withdraw')
                assert result.status_code==200 and result.json()['transferable'] is False
                assert (await c.get('/api/account')).json()['paper']['balance']=='400'
                app.state.db.execute('UPDATE auth_sessions SET expires=0')
                assert (await post('/api/trading/allocate',{'amount':'1','confirm':True},'expired-session')).status_code==401
                assert app.state.db.execute('SELECT balance FROM users WHERE address=?',(env['owner'],)).fetchone()[0] in ('700','700.000000')
        finally:
            # Keep the loop ticking through thread cleanup in socket-restricted
            # test runners; the production event loop is not changed.
            await asyncio.get_running_loop().shutdown_default_executor()
            beat.cancel()
    asyncio.run(run());app.state.db.close()


def test_prepared_build_served_with_matching_assets_without_changing_preview(env):
    import json,re
    from autodefi.config import ROOT
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    preview=(ROOT/'site/dist/index.html').read_bytes()
    manifest=json.loads((ROOT/'site/prepared/build-manifest.json').read_text())
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as c:
                for route in ('/','/vault'):
                    r=await c.get(route)
                    assert r.status_code==200 and r.content==(ROOT/'site/prepared/index.html').read_bytes()
                for filename in manifest['outputs']:
                    r=await c.get('/'+filename.removeprefix('prepared/'))
                    assert r.status_code==200 and r.content==(ROOT/'site'/filename).read_bytes()
                assert (await c.get('/api/account')).status_code==401
        finally:
            await asyncio.get_running_loop().shutdown_default_executor()
            beat.cancel()
    asyncio.run(run());app.state.db.close()
    assert preview==(ROOT/'site/dist/index.html').read_bytes()
