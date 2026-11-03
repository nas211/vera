"""Seven-network API journeys; synthetic wallets, chain, model and Lighter frames.

Uses real authentication, ASGI handlers and shared ledgers. This is not browser,
genuine wallet, network-RPC or live-protection evidence.
"""
import asyncio
import copy
from decimal import Decimal as D

import httpx
import pytest

from autodefi.adapters import CHAINS, adapter, subject
from autodefi.api import create_app
from autodefi.trading import agents_for
from test_api_ai import FixtureModel
from test_multichain import identity, sign
from vela.db import put, transaction


@pytest.mark.parametrize('network', CHAINS)
def test_network_api_journey(env, network):
    config=env['config'];config.multichain=True
    config.factory=env['v']['address']  # Explicitly synthetic deployment.
    observation=copy.deepcopy(env['v'])
    observation.update(collateral='0',withdrawable='0',loanState=0)
    env['db'].execute('DELETE FROM vaults')

    class SyntheticDogeOS:
        def read(self, owner, tx=None):
            assert network=='dogeos' and owner==observation['owner']
            return copy.deepcopy(observation),None

    model=FixtureModel()
    app=create_app(config,feed=env['feed'],chain=SyntheticDogeOS(),model=model)
    db=app.state.db;paper=app.state.paper
    wallet=identity(network);owner=subject(network,wallet)

    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            headers={'Origin':config.origin,'X-ADP-Client':'web','X-Vella-Network':network}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=config.origin,headers=headers) as client:
                sequence=0
                async def post(path, body):
                    nonlocal sequence
                    sequence+=1
                    response=await client.post(path,json=body,headers={'Idempotency-Key':f'journey-{network}-{sequence}'})
                    assert response.status_code==200,response.text
                    return response.json()
                async def snapshot():
                    response=await client.get('/api/account')
                    assert response.status_code==200,response.text
                    return response.json()
                async def login():
                    challenge=await post('/api/auth/nonce',dict(address=wallet,chainId=adapter(network).chain_id))
                    return await post('/api/auth/verify',dict(address=wallet,nonce=challenge['nonce'],signature=sign(network,challenge['message'])))
                async def sync(**changes):
                    observation.update(changes);observation['block']+=1
                    await post('/api/vault/sync',{})
                async def sim(action,**body):
                    account=await snapshot()
                    return await post('/api/simulated-vault/'+action,dict(confirm=True,
                        vaultId=account['vault']['address'] if account['vault'] else None,
                        loanId=account['health']['loan']['id'] if account['health'] and account['health']['loan'] else None,**body))
                public=(await client.get('/api/config')).json()
                assert public['chain']['id']==network and len(public['chains'])==7
                assert public['mode']=='PAPER' and public['lending']=='DEMO'
                assert public['liveEnabled'] is False and public['realFunding'] is False
                assert (await client.get('/api/account')).status_code==401
                assert (await login())['network']==network
                assert (await snapshot())['paper']['balance']=='0'
                if network=='dogeos':
                    await sync()  # Owner-created vault, represented by a chain fixture.
                    await sync(collateral=str(10000*10**18),withdrawable=str(10000*10**18))
                    await post('/api/quote',{'drop':'0'})
                    await sync(loanState=1,withdrawable='0',principalMicro='400000000')
                    await post('/api/borrow',{})
                    await sync(loanState=2,trading=True)
                else:
                    await sim('create',asset=adapter(network).collateral)
                    await sim('deposit',amount='10')
                    await sim('borrow',amount='400')
                    await sim('authorize',allocation='400',positionSize='100',maxLoss='1')
                assert D((await snapshot())['health']['debt'])==400
                await post('/api/trading/start',{'confirm':True})
                for market in env['feed'].markets.values():
                    for candle in market.candles:
                        db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',
                            (market.symbol,candle['ts'],candle['o'],candle['h'],candle['l'],candle['c']))
                agents=agents_for(db,model,paper)
                for _ in range(3):put(db,'ai-last-call',0);agents.tick_ai()
                signal=agents.signal(db.execute('SELECT * FROM users WHERE address=?',(owner,)).fetchone())
                assert signal and {'research','review','direction'}<=set(model.calls)
                with transaction(db):paper.open(owner,signal)
                db.execute('UPDATE paper_orders SET due=0');paper.tick()
                positions=(await snapshot())['paper']['positions'];assert len(positions)==1
                account=await snapshot()
                context=dict(vaultId=account['vault']['address'],loanId=account['health']['loan']['id'])
                await post('/api/repay',dict(context,confirm=True,amount='100'))
                assert D((await snapshot())['health']['debt'])==300
                market=env['feed'].markets['BTC/USD'];market.mark*=D('1.01');market.index=market.mark
                market.bids={market.mark-D('.01'):D(10000)};market.asks={market.mark+D('.01'):D(10000)}
                with transaction(db):paper.close(positions[0]['id'],'synthetic-journey-profit')
                await post('/api/settle',dict(context,confirm=True));paper.settle_tick()
                if network=='dogeos':
                    assert (await snapshot())['health']['loan']['state']=='AWAITING_CHAIN'
                    await sync(loanState=3,withdrawable=observation['collateral'],trading=False)
                    await sync(collateral='0',withdrawable='0')
                else:await sim('withdraw',amount='10')
                account=await snapshot()
                assert account['health']['loan']['state']=='REPAID'
                assert D(account['health']['debt'])==0 and D(account['health']['collateral'])==0
                assert account['paper']['totals']['trades']==1
                assert db.execute('SELECT count(*) FROM chain_receipts').fetchone()[0]==0
                wrong='base' if network!='base' else 'solana'
                assert (await client.get('/api/account',headers={'X-Vella-Network':wrong})).status_code==403
                for path in ('/api/live/order','/api/deposit/real'):
                    assert (await client.post(path,json={})).status_code==404
                await post('/api/logout',{})
                assert (await client.get('/api/account')).status_code==401
                await login()
                assert (await snapshot())['health']['loan']['state']=='REPAID'
        finally:
            await asyncio.get_running_loop().shutdown_default_executor()
            beat.cancel()
    try:asyncio.run(run())
    finally:db.close()
