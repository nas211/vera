import asyncio
from decimal import Decimal as D
import httpx
from eth_account.messages import encode_defunct
from autodefi.api import create_app
from autodefi.runtime import Runtime
from vela.db import transaction
from conftest import ALICE,BOB,funded,fill
from test_api_ai import FixtureModel
from test_completion_safety import ticking


def test_authenticated_reduction_idempotency_history_and_wallet_isolation(env):
    funded(env);position=fill(env)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    paper=app.state.paper;db=app.state.db
    async def scenario():
        headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin,headers=headers) as c:
            assert (await c.get('/api/history/trades')).status_code==401
            async def login(key):
                challenge=(await c.post('/api/auth/nonce',json={'address':key.address,'chainId':6281971})).json()
                signature=key.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
                assert (await c.post('/api/auth/verify',json=dict(address=key.address,nonce=challenge['nonce'],signature=signature))).status_code==200
            await login(BOB)
            path=f"/api/trading/positions/{position['id']}/close"
            body=dict(fraction='.5',confirm=True)
            assert (await c.post(path,json=body,headers={'Idempotency-Key':'foreign-reduction'})).status_code==400
            assert (await c.get('/api/history/orders')).json()['items']==[]
            await login(ALICE)
            await c.post('/api/trading/stop',json={})
            for _ in range(2):
                response=await c.post(path,json=body,headers={'Idempotency-Key':'reduce-half-once'})
                assert response.status_code==200,response.text
            assert db.execute("SELECT count(*) FROM paper_orders WHERE client_id='reduce-half-once'").fetchone()[0]==1
            assert (await c.post(path,json={'fraction':'1','confirm':True},headers={'Idempotency-Key':'reduce-half-once'})).status_code==400
            db.execute('UPDATE paper_orders SET due=0');paper.tick()
            assert D(paper.getPosition(env['owner'])[0]['qty'])<D(position['qty'])
            result=(await c.get('/api/history/trades')).json()
            assert len(result['items'])==1 and result['items'][0]['reason']=='owner_reduction'
            first=(await c.get('/api/history/orders?limit=1')).json()
            assert first['nextCursor']
            second=(await c.get('/api/history/orders',params={'limit':1,'before':first['nextCursor']})).json()
            assert second['items'][0]['history_cursor']<first['items'][0]['history_cursor']
            assert (await c.get('/api/history/orders?before=0')).status_code==422
    try:asyncio.run(ticking(scenario))
    finally:app.state.db.close()


def test_equity_observations_survive_restart_and_skip_stale_positions(env):
    funded(env);fill(env)
    runtime=Runtime(env['config'],env['db'],env['feed'],env['paper'],FixtureModel(),None)
    now=env['feed'].markets['BTC/USD'].stats_at
    runtime.sample_equity(now)
    runtime.sample_equity(now+1)
    restarted=Runtime(env['config'],env['db'],env['feed'],env['paper'],FixtureModel(),None)
    restarted.sample_equity(now+2)
    assert len(env['paper'].snapshot(env['owner'])['history'])==1
    env['feed'].markets['BTC/USD'].book_at=0
    restarted.sample_equity(now+61)
    assert len(env['paper'].snapshot(env['owner'])['history'])==1
    env['feed'].markets['BTC/USD'].book_at=now
    restarted.sample_equity(now+62)
    rows=env['paper'].snapshot(env['owner'])['history']
    assert len(rows)==2 and rows[0]['ts']==now and rows[1]['ts']==now+62
