#!/usr/bin/env python3
"""Pipe-only browser harness. Synthetic keys/model/feed, real ASGI/SQLite services.

No listener or external integration. Never import this into the application.
"""
import asyncio,json,sys,time,uuid
from pathlib import Path
from decimal import Decimal as D
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend'),str(ROOT/'tests')]
import httpx
from conftest import fixture_feed,ALICE
from test_api_ai import FixtureModel
from test_multichain import SOL,sign
from autodefi.api import create_app
from autodefi.config import Config
from autodefi.adapters import subject
from autodefi.trading import agents_for
from autodefi.repayment import Repayment
from vela.db import transaction,put

async def main():
    # Application logs must not be interpreted as pipe protocol responses.
    protocol=sys.stdout;sys.stdout=sys.stderr
    async def pulse():
        while True:await asyncio.sleep(.01)
    asyncio.create_task(pulse())
    path=ROOT/'.runtime/lighter-showcase'/('browser-'+uuid.uuid4().hex+'.sqlite3')
    config=Config(origin='https://defivela.xyz',db_path=path,multichain=True)
    config.client_id='fixture-only-no-live-wallet'
    feed=fixture_feed();model=FixtureModel();app=create_app(config,feed=feed,model=model,frontend='candidate')
    db=app.state.db;paper=app.state.paper
    from tools.dogeos_readonly import create_app as gateway
    class NativeFixture:
        factory='0xD6644a0C4233257b091D669142A7A8db3F2B7C3a'
        reference='0x533beF9B9453914b584241F53aBA40f106Ac3125'
        def __init__(self):self.vault=None;self.balance=10*10**18;self.transactions={};self.block=100
        def snapshot(self,wallet=None,transaction=None):
            return dict(status='VERIFIED',chainId=6281971,block=self.block,confirmations=3,
                wallet=wallet or ALICE.address.lower(),factory=self.factory,registered=bool(self.vault),vault=self.vault,
                walletBalanceWei=str(self.balance),walletBalanceTestDOGE=str(D(self.balance)/10**18),
                observedAt=0,readOnly=True,customerTransactionsEnabled=False,referenceVault=self.reference,
                referenceOwner=ALICE.address.lower(),explorer='https://dogeos-testnet.l2scan.co',deploymentTransactions={},
                transaction=self.transactions.get(transaction))
        def send(self,tx):
            from eth_utils import keccak
            self.block+=3
            function=lambda name:'0x'+keccak(text=name)[:4].hex()
            if tx['data']==function('createVault()'):
                self.vault=dict(address=self.reference,owner=ALICE.address.lower(),ownershipVerified=True,registrationVerified=True,
                    bytecodeVerified=True,loanState=0,paused=False,trading=False,collateralWei='0',withdrawableWei='0',status='IDLE')
            elif tx['data']==function('deposit()'):
                amount=int(tx['value'],16);self.balance-=amount
                self.vault['collateralWei']=str(int(self.vault['collateralWei'])+amount)
            elif tx['data'].startswith(function('withdraw(uint256)')):
                amount=int(tx['data'][10:],16);self.balance+=amount
                self.vault['collateralWei']=str(int(self.vault['collateralWei'])-amount)
            else:raise ValueError('Unsupported fixture transaction')
            self.vault['withdrawableWei']=self.vault['collateralWei']
            self.vault['collateralTestDOGE']=str(D(self.vault['collateralWei'])/10**18)
            self.vault['balanceTestDOGE']=self.vault['collateralTestDOGE']
            self.balance-=10**14  # Explicitly synthetic fee; no real receipt.
            h='0x'+format(self.block,'064x')
            self.transactions[h]=dict(hash=h,status='CONFIRMED',sender=tx['from'],to=tx['to'],data=tx['data'],valueWei=str(int(tx['value'],16)),block=self.block)
            return h
    native=NativeFixture()
    app=gateway(reader=native,transport=httpx.ASGITransport(app=app),wallet_transactions=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=config.origin) as client:
        while line:=await asyncio.to_thread(sys.stdin.readline):
            try:
                request=json.loads(line)
                for m in feed.markets.values():m.book_at=m.stats_at=time.time()
                if request.get('control')=='native': result={'hash':native.send(request['tx'])}
                elif request.get('control')=='identity': result=dict(evm=ALICE.address.lower(),solana=SOL)
                elif request.get('control')=='sign':result=dict(signature=sign(request['network'],request['message']))
                elif request.get('control')=='trade':
                    owner=subject(request['network'],SOL if request['network']=='solana' else ALICE.address)
                    with transaction(db):
                        for m in feed.markets.values():
                            end=int(time.time())//300*300
                            for j,c in enumerate(m.candles):
                                c['ts']=end-300*(250-j)
                                db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(m.symbol,c['ts'],c['o'],c['h'],c['l'],c['c']))
                    agents=agents_for(db,model,paper)
                    for _ in range(3):
                        for m in feed.markets.values():m.book_at=m.stats_at=time.time()
                        put(db,'ai-last-call',0);agents.tick_ai()
                    user=db.execute('SELECT * FROM users WHERE address=?',(owner,)).fetchone();signal=agents.signal(user)
                    if not signal:raise ValueError('Fixture model did not produce an eligible signal; model stages: '+str(model.calls))
                    with transaction(db):paper.open(owner,signal)
                    db.execute('UPDATE paper_orders SET due=0');paper.tick()
                    result=dict(positions=len(paper.getPosition(owner)),scope='Synthetic model/feed; real shared paper engine')
                elif request.get('control')=='profit':
                    owner=subject(request['network'],SOL if request['network']=='solana' else ALICE.address)
                    m=feed.markets['BTC/USD'];m.mark*=D('1.01');m.index=m.mark;m.bids={m.mark-D('.01'):D(10000)};m.asks={m.mark+D('.01'):D(10000)}
                    for p in paper.getPosition(owner):
                        with transaction(db):paper.close(p['id'],'synthetic-browser-profit')
                    Repayment(db,paper).tick();result=dict(simulated=True)
                else:
                    async def heartbeat():
                        while True:await asyncio.sleep(.01)
                    beat=asyncio.create_task(heartbeat())
                    try:
                        response=await client.request(request['method'],request['path'],headers=request.get('headers',{}),content=request.get('body'))
                        result=dict(status=response.status_code,body=response.text,headers={k:v for k,v in response.headers.items() if k in ('content-type','set-cookie')})
                        paper.settle_tick();Repayment(db,paper).tick()
                    finally:beat.cancel()
                print(json.dumps(result),flush=True,file=protocol)
            except Exception as error:
                print(json.dumps(dict(fixtureError=type(error).__name__+': '+str(error))),flush=True,file=protocol)
    db.close()
    for suffix in ('','-wal','-shm'):Path(str(path)+suffix).unlink(missing_ok=True)
    # Keep the pulse alive until executor completion reaches this event loop in
    # restricted runtimes where the normal selector wakeup socket is unavailable.
    await asyncio.get_running_loop().shutdown_default_executor()

asyncio.run(main())
