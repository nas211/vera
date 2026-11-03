import asyncio
from decimal import Decimal as D
import httpx
import pytest
from autodefi.api import create_app
from autodefi.trading import agents_for
from vela.db import get, put
from vela.lighter.engine import identity
from test_api_ai import FixtureModel

TOKEN='a'*64
OTHER='b'*64


@pytest.mark.parametrize('side',['long','short'])
def test_desk_session_manual_fill_partials_and_persistence(env, side):
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    db,paper=app.state.db,app.state.paper
    owner=identity(TOKEN)
    async def scenario():
        headers={'Origin':env['config'].origin,'X-ADP-Client':'web','X-Vela-Paper':TOKEN}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,headers=headers) as c:
            first=await c.post('/api/paper/session',json={});assert first.status_code==200,first.text
            assert D(first.json()['balance'])==10000 and not first.json()['preferences']['autonomous']
            assert (await c.post('/api/paper/session',json={})).json()['balance']==first.json()['balance']
            body=dict(clientId='manual-order-1',symbol='BTC/USD',side=side,type='market',notional='100',leverage='2',stopBps='50',targetBps='100',riskBudget='2')
            placed=await c.post('/api/paper/orders',json=body);assert placed.status_code==202,placed.text
            assert (await c.post('/api/paper/orders',json=body)).json()==placed.json()
            assert (await c.post('/api/paper/orders',json={**body,'notional':'101'})).status_code==400
            db.execute('UPDATE paper_orders SET due=0');paper.tick()
            p=paper.getPosition(owner)[0]
            sign=D(1 if side=='long' else -1)
            loss=(D(p['entry'])-D(p['stop']))*D(p['qty'])*sign+D(p['paid_fees'])+D(p['stop'])*D(p['qty'])*D('.0004')
            assert loss<=2
            await c.post('/api/paper/session',headers={'X-Vela-Paper':OTHER},json={})
            denied=await c.post('/api/paper/positions/'+p['id']+'/close',headers={'X-Vela-Paper':OTHER},json={'clientId':'foreign-close','fraction':'1'})
            assert denied.status_code==400
            for i,fraction in enumerate(['.5','1']):
                r=await c.post('/api/paper/positions/'+p['id']+'/close',json={'clientId':'close-part-'+str(i),'fraction':fraction});assert r.status_code==202,r.text
                db.execute('UPDATE paper_orders SET due=0');paper.tick()
            assert not paper.getPosition(owner)
            snap=(await c.get('/api/paper/account')).json()
            assert len(snap['trades'])==2 and snap['today']['trades']==2
            assert D(snap['balance'])==D(10000)+sum(D(t['pnl']) for t in snap['trades'])
            assert (await c.post('/api/paper/session',json={})).json()['balance']==snap['balance']
            assert (await c.get('/api/paper/account',headers={'X-Vela-Paper':'c'*64})).status_code==401
            # A desk capability is not a signed wallet session.
            assert (await c.get('/api/account',cookies={'adp_session':TOKEN})).status_code==401
    try:asyncio.run(scenario())
    finally:db.close()


def test_desk_ai_pipeline_and_stop_preserve_manual_access(env):
    model=FixtureModel();app=create_app(env['config'],feed=env['feed'],model=model)
    db,paper=app.state.db,app.state.paper;owner=identity(TOKEN)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,
            headers={'Origin':env['config'].origin,'X-ADP-Client':'web','X-Vela-Paper':TOKEN}) as c:
            await c.post('/api/paper/session',json={})
            for m in env['feed'].markets.values():
                db.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',[(m.symbol,x['ts'],x['o'],x['h'],x['l'],x['c']) for x in m.candles])
            r=await c.post('/api/paper/agent',json={'symbol':'BTC/USD','leverage':'1','autonomous':True});assert r.status_code==200,r.text
            agents=agents_for(db,model,paper)
            for _ in range(3):put(db,'ai-last-call',0);agents.tick_ai()
            assert model.calls==['research','review','direction']
            user=db.execute('SELECT * FROM users WHERE address=?',(owner,)).fetchone()
            signal=agents.signal(user);assert signal
            paper.open(owner,signal);db.execute('UPDATE paper_orders SET due=0');paper.tick()
            assert paper.getPosition(owner)
            r=await c.post('/api/paper/agent',json={'symbol':'BTC/USD','leverage':'1','autonomous':False})
            assert r.status_code==200 and not r.json()['preferences']['autonomous'] and r.json()['positions']
            assert agents.signal(user) is None
            db.execute('UPDATE auth_sessions SET expires=0 WHERE address=?',(owner,))
            assert (await c.get('/api/paper/account')).status_code==401
            paper.risk_governor();assert paper.getPosition(owner)
    try:asyncio.run(scenario())
    finally:db.close()


def test_desk_rejects_foreign_origin_and_forged_execution_fields(env):
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin) as c:
            assert (await c.post('/api/paper/session',json={},headers={'X-Vela-Paper':TOKEN})).status_code==403
            headers={'Origin':env['config'].origin,'X-ADP-Client':'web','X-Vela-Paper':TOKEN}
            assert (await c.post('/api/paper/session',json={},headers=headers)).status_code==200
            r=await c.post('/api/paper/orders',headers=headers,json=dict(clientId='forged-order',symbol='BTC/USD',side='long',agent=True))
            assert r.status_code==422
            r=await c.post('/api/paper/orders',headers=headers,json=dict(clientId='oversized-order',symbol='BTC/USD',side='short',notional='201'))
            assert r.status_code==400
    try:asyncio.run(scenario())
    finally:app.state.db.close()


@pytest.mark.parametrize('side',['long','short'])
def test_desk_restart_recovers_position_and_auth_without_new_credit(env,side):
    owner=identity(TOKEN)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    headers={'Origin':env['config'].origin,'X-ADP-Client':'web','X-Vela-Paper':TOKEN}
    async def open_position():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,headers=headers) as c:
            await c.post('/api/paper/session',json={})
            r=await c.post('/api/paper/orders',json=dict(clientId='restart-order',symbol='BTC/USD',side=side,notional='100',riskBudget='.75'))
            assert r.status_code==202
    asyncio.run(open_position())
    app.state.db.execute('UPDATE paper_orders SET due=0');app.state.paper.tick()
    before=app.state.paper.getPosition(owner)[0];balance=app.state.paper.snapshot(owner)['balance']
    app.state.db.close()
    restored=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    try:
        restored.state.paper.tick()
        after=restored.state.paper.getPosition(owner)[0]
        assert after['id']==before['id'] and after['qty']==before['qty']
        assert (D(after['stop'])>=D(before['stop']) if side=='long' else D(after['stop'])<=D(before['stop']))
        assert D(restored.state.paper.meta(after)['riskBudget'])==D('.75')
        async def recover():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(restored),base_url=env['config'].origin,headers=headers) as c:
                r=await c.get('/api/paper/account');assert r.status_code==200
                assert r.json()['balance']==balance and len(r.json()['positions'])==1
                assert (await c.post('/api/paper/session',json={})).json()['balance']==balance
        asyncio.run(recover())
    finally:restored.state.db.close()
