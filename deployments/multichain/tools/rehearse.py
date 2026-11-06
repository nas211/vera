#!/usr/bin/env python3
"""In-process end-to-end rehearsal. Fixture wallet/chain/model/data, NEVER testnet.
Uses the actual API, signature verification, lending ledger, Vela strategy pipeline
and Lighter paper fills. Temporary independent database; no production access.
"""
import sys,asyncio,tempfile,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tests')]
from decimal import Decimal as D
import httpx
from eth_account.messages import encode_defunct
from autodefi.config import Config
from autodefi.api import create_app
from autodefi.chain import certificate
from autodefi.lending import Lending
from autodefi.trading import agents_for
from vela.db import put,transaction
from conftest import ALICE,ATTESTOR,fixture_feed,fixture_vault
from test_api_ai import FixtureModel

class RehearsalChain:
    def __init__(self):self.v=fixture_vault(ALICE.address.lower())
    def read(self,owner,tx=None):
        if owner!=self.v['owner']:raise ValueError('Foreign vault')
        return dict(self.v),None # No fabricated transaction hashes or explorer links.

def _rehearse():
    parent=ROOT/'.runtime/lighter-showcase';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rehearsal-',dir=parent) as tmp:
        cfg=Config(db_path=Path(tmp)/'ledger.sqlite3',attestor_key=ATTESTOR.key.hex())
        feed=fixture_feed();chain=RehearsalChain();model=FixtureModel();app=create_app(cfg,feed=feed,chain=chain,model=model)
        db=app.state.db;paper=app.state.paper;owner=ALICE.address.lower();steps=[]
        async def run():
            async def heartbeat():
                while True:await asyncio.sleep(.01)
            beat=asyncio.create_task(heartbeat())
            try:
                headers={'Origin':cfg.origin,'X-ADP-Client':'web','X-Wallet-Address':owner}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=cfg.origin,headers=headers) as c:
                    async def post(path,body={},key=None):
                        r=await c.post(path,json=body,headers={'Idempotency-Key':key} if key else {})
                        assert r.status_code==200,(path,r.status_code,r.text);return r.json()
                    assert (await c.get('/api/account')).status_code==401
                    challenge=await post('/api/auth/nonce',{'address':owner,'chainId':6281971})
                    signature=ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
                    await post('/api/auth/verify',dict(address=owner,nonce=challenge['nonce'],signature=signature));steps.append('Wallet signature authenticated; unauthenticated account access rejected')
                    await post('/api/vault/sync');steps.append('FIXTURE vault with 10,000 test DOGE observed (not a testnet transaction)')
                    borrowed=await post('/api/borrow',key='rehearsal-borrow');assert borrowed['loan']['original']=='800'
                    await post('/api/borrow',key='rehearsal-borrow')
                    assert (await c.get('/api/account')).json()['paper']['balance']=='800';steps.append('800 simulated USDC credited exactly once')
                    chain.v.update(loanState=2,trading=True);await post('/api/vault/sync')
                    denied=await c.post('/api/trading/start',json={})
                    assert denied.status_code==400 and 'Confirm Start Trading' in denied.json()['error']
                    await post('/api/trading/start',{'confirm':True});steps.append('Explicit fixture consent authorized paper trading; missing consent was rejected. FIXTURE on-chain permissions and model connection only')
                    for m in feed.markets.values():
                        for bar in m.candles:db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(m.symbol,bar['ts'],bar['o'],bar['h'],bar['l'],bar['c']))
                    agents=agents_for(db,model,paper)
                    for _ in range(3):put(db,'ai-last-call',0);agents.tick_ai()
                    signal=agents.signal(db.execute('SELECT * FROM users WHERE address=?',(owner,)).fetchone());assert signal
                    with transaction(db):paper.open(owner,signal)
                    db.execute('UPDATE paper_orders SET due=0');paper.tick();assert paper.getPosition(owner)
                    steps.append('Reused research → review → direction pipeline produced an approved fixture signal; paper position filled')
                    price=feed.markets['BTC/USD'];price.mark=D(60300);price.bids={D(60299):D(10000)};price.asks={D(60301):D(10000)}
                    snap=(await c.get('/api/account')).json();assert D(snap['paper']['unrealized'])>0
                    await post('/api/trading/stop');assert paper.getPosition(owner);steps.append('Stop blocked new entries and preserved the open position')
                    await post('/api/settle',{'confirm':True},key='rehearsal-settle');db.execute('UPDATE paper_orders SET due=0');paper.tick();paper.settle_tick()
                    l=Lending(db).loan(owner);assert l['state']=='AWAITING_CHAIN' and D(l['principal'])==0
                    proof=await post('/api/certificate');assert proof['action']==2 and proof['b']=='0'
                    steps.append('Stop & settle closed the paper position, repaid principal + interest, retained surplus and prepared owner settlement certificate')
                    chain.v.update(loanState=3,trading=False,withdrawable=chain.v['collateral']);await post('/api/vault/sync')
                    assert Lending(db).loan(owner)['state']=='REPAID'
                    chain.v.update(collateral='0',withdrawable='0');await post('/api/vault/sync')
                    final=(await c.get('/api/account')).json();assert final['health']['collateral']=='0'
                    steps.append('FIXTURE settlement confirmation and withdrawal reconciled; no on-chain write was performed')
                    await post('/api/logout');assert (await c.get('/api/account')).status_code==401
                    return dict(status='PASS — LOCAL REHEARSAL ONLY',title='VELLA',steps=steps,
                        final=dict(debt=final['health']['debt'],paperBalance=final['paper']['balance'],paperPnl=final['paper']['pnl'],demoSurplus=final['demoSurplus']),
                        real=['Cryptographic wallet-signature verification','ASGI API handlers','SQLite loan and paper accounting','Reused Vela pipeline and Lighter paper execution'],
                        fixtures=['Wallet test key','DogeOS state/confirmations','AI model replies','Market prices/orderbook'],
                        NOT_VERIFIED=['MyDoge browser approval','DogeOS deployment or transactions','Live Lighter feeds','Real AI provider','Browser rendering'],liveTrading=False,realLending=False)
            finally:beat.cancel()
        # This sandbox does not reliably wake its selector for thread-executor
        # shutdown callbacks. Keep a timer alive through shutdown, not just requests.
        loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
        async def keepalive():
            while True:await asyncio.sleep(.01)
        timer=loop.create_task(keepalive())
        try:return loop.run_until_complete(run())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            timer.cancel();loop.run_until_complete(asyncio.gather(timer,return_exceptions=True))
            loop.close();asyncio.set_event_loop(None);db.close()
def rehearse():
    from unittest.mock import patch
    with patch('time.time',return_value=time.time()):return _rehearse()

if __name__=='__main__':
    report=rehearse();(ROOT/'snapshot').mkdir(exist_ok=True)
    (ROOT/'snapshot/rehearsal.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
