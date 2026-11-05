"""Explicit wallet Start persists independently of browser sessions; no live orders."""
import asyncio
import time
import pytest
import httpx
from eth_account.messages import encode_defunct
from autodefi.api import create_app
from autodefi.trading import GovernedPaper
from vela.db import get,put,transaction
from conftest import ALICE,BOB,funded,fill,order
from test_api_ai import FixtureModel

async def login(client,account):
    c=(await client.post('/api/auth/nonce',json={'address':account.address,'chainId':6281971})).json()
    r=await client.post('/api/auth/verify',json={'address':account.address,'nonce':c['nonce'],
        'signature':account.sign_message(encode_defunct(text=c['message'])).signature.hex()})
    assert r.status_code==200,r.text

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('end',['stop','expiry','permission','revision','generation'])
def test_explicit_start_survives_logout_and_reconstruction_but_not_revocation(env,side,end):
    funded(env);position=fill(env,side)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,
            headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}) as c:
            await login(c,ALICE)
            assert (await c.post('/api/trading/start',json={'confirm':True})).status_code==200
            before=(await c.get('/api/account')).json()['paper']
            assert (await c.post('/api/logout',json={})).status_code==200
            assert (await c.get('/api/account')).status_code==401
            # New worker instance + expired/revoked browser rows, same durable DB.
            app.state.db.execute('DELETE FROM auth_sessions')
            worker=GovernedPaper(app.state.db,env['config'].paper(),env['feed'],require_session=True)
            worker.risk_governor()
            assert worker.run_authorized(env['owner'])
            assert get(app.state.db,'paper-prefs:'+env['owner'])['autonomous']
            assert worker.snapshot(env['owner'])['balance']==before['balance']
            with transaction(app.state.db):
                worker.submit(env['owner'],{**order(side),'symbol':'ETH/USD'},'after-browser-closed')
            app.state.db.execute("UPDATE paper_orders SET due=0 WHERE status='pending'");worker.tick()
            assert len(worker.getPosition(env['owner']))==2
            await login(c,BOB)
            assert (await c.get('/api/account')).json()['paper']['balance']=='0'
            assert (await c.post('/api/trading/stop',json={})).status_code==200
            assert worker.run_authorized(env['owner']) # Another wallet cannot stop Alice.
            await login(c,ALICE)
            assert len((await c.get('/api/account')).json()['paper']['positions'])==2
            if end=='stop':assert (await c.post('/api/trading/stop',json={})).status_code==200
            elif end=='expiry':
                consent=get(app.state.db,'agent-authorization:'+env['owner']);consent['expiresAt']=time.time()-1
                put(app.state.db,'agent-authorization:'+env['owner'],consent)
            elif end=='permission':worker.configure(env['owner'],dict(confirm=True,allocation='500',positionSize='100',maxLoss='1',leverage='1'))
            elif end=='revision':
                put(app.state.db,'trading-permission:'+env['owner'],dict(revision=99,expiresAt=str(int(time.time())+999),allocation='500000000',positionSize='100000000',maxLoss='1000000',leverage='1',markets='7'))
            else:
                prefs=get(app.state.db,'paper-prefs:'+env['owner']);prefs['generation']+=1
                put(app.state.db,'paper-prefs:'+env['owner'],prefs)
            worker.risk_governor()
            assert not get(app.state.db,'paper-prefs:'+env['owner'])['autonomous']
            with pytest.raises(ValueError):worker.submit(env['owner'],order(side),'must-not-open')
            for p in worker.getPosition(env['owner']):
                with transaction(app.state.db):worker.submit(env['owner'],dict(reduceOnly=True,positionId=p['id'],fraction='1'),'exit:'+p['id'])
            app.state.db.execute('UPDATE paper_orders SET due=0');worker.tick()
            assert not worker.getPosition(env['owner'])
    try:asyncio.run(run())
    finally:app.state.db.close()


def test_permission_alone_does_not_grant_durable_execution(env):
    funded(env)
    env['paper'].configure(env['owner'],dict(confirm=True,allocation='500',positionSize='100',maxLoss='2',leverage='1'))
    assert not env['paper'].run_authorized(env['owner'])
    with pytest.raises(ValueError,match='Start trading'):env['paper'].authorize_run(env['owner'])

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('outcome',['profit','loss'])
def test_authorized_profit_sweep_survives_browser_logout_and_only_uses_realized_net_profit(env,side,outcome):
    from decimal import Decimal as D
    from autodefi.repayment import Repayment
    funded(env)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    db=app.state.db;paper=app.state.paper;repayment=Repayment(db,paper)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,
            headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}) as c:
            await login(c,ALICE)
            assert (await c.post('/api/trading/start',json={'confirm':True})).status_code==200
            with transaction(db):repayment.configure(env['owner'],dict(confirm=True,sweepPercent='100',frequency='off',minimum='0'))
            assert (await c.post('/api/logout',json={})).status_code==200
            assert get(db,'repayment-policy:'+env['owner'])['enabled']
            with transaction(db):paper.submit(env['owner'],order(side),'sweep-entry')
            db.execute('UPDATE paper_orders SET due=0');paper.tick()
            p=paper.getPosition(env['owner'])[0]
            market=env['feed'].markets['BTC/USD']
            move=D('1.01') if (side=='long')==(outcome=='profit') else D('.99')
            market.mark*=move;market.index=market.mark
            market.bids={market.mark-D('.01'):D(10000)};market.asks={market.mark+D('.01'):D(10000)}
            repayment.tick()
            assert D(repayment.status(env['owner'])['debt'])==800 # Unrealized gains cannot pay debt.
            with transaction(db):paper.close(p['id'],'test-'+outcome)
            net=repayment.realized_net(env['owner']);repayment.tick()
            debt=D(repayment.status(env['owner'])['debt'])
            if outcome=='profit':assert net>0 and D('0')<800-debt<=net
            else:assert net<0 and debt==800
            paid=800-debt;repayment.tick();assert 800-D(repayment.status(env['owner'])['debt'])==paid
            await login(c,ALICE)
            restored=(await c.get('/api/account')).json()
            assert D(restored['health']['debt'])==debt
            assert restored['paper']['today']['trades']==1
    try:asyncio.run(run())
    finally:db.close()


def test_paper_only_settlement_closes_direct_account_and_refuses_loans(env):
    from autodefi.repayment import TradingCapital
    funded(env)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,
            headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}) as c:
            await login(c,ALICE)
            r=await c.post('/api/trading/settle',json={'confirm':True},headers={'Idempotency-Key':'loan-denied'})
            assert r.status_code==400 and 'loan repayment' in r.text
            await login(c,BOB)
            owner=BOB.address.lower();paper=app.state.paper;db=app.state.db
            with transaction(db):
                TradingCapital(db,paper).allocate(owner,'500',True)
                paper.configure(owner,dict(confirm=True,allocation='500',positionSize='100',maxLoss='1',leverage='1'))
            assert (await c.post('/api/trading/start',json={'confirm':True})).status_code==200
            with transaction(db):paper.submit(owner,{**order(),'riskBudget':'1'},'direct-entry')
            db.execute('UPDATE paper_orders SET due=0');paper.tick();assert paper.getPosition(owner)
            assert (await c.post('/api/trading/settle',json={'confirm':False},headers={'Idempotency-Key':'no-confirm'})).status_code==400
            for _ in range(2):
                result=await c.post('/api/trading/settle',json={'confirm':True},headers={'Idempotency-Key':'direct-settle'})
                assert result.status_code==200 and result.json()['onchain'] is False
            db.execute('UPDATE paper_orders SET due=0');paper.tick();paper.settle_tick()
            assert not paper.getPosition(owner)
            assert not paper.run_authorized(owner)
    try:asyncio.run(run())
    finally:app.state.db.close()
