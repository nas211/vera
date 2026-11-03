#!/usr/bin/env python3
"""Pipe-only browser harness. Synthetic keys/model/feed, real ASGI/SQLite services.

No listener or external integration. Never import this into the application.
"""
import asyncio,json,sys,time,uuid
from pathlib import Path
from decimal import Decimal as D
ROOT=Path('/root/VellaMultichain')
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
    path=ROOT/'.runtime/lighter-showcase'/('browser-'+uuid.uuid4().hex+'.sqlite3')
    config=Config(origin='https://defivela.xyz',db_path=path,multichain=True)
    config.client_id=httpx.get('http://127.0.0.1:8804/api/config').json()['myDogeClientId']
    feed=fixture_feed();model=FixtureModel();app=create_app(config,feed=feed,model=model,frontend='candidate')
    db=app.state.db;paper=app.state.paper
    from tools.dogeos_readonly import create_app as gateway
    app=gateway(transport=httpx.ASGITransport(app=app))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=config.origin) as client:
        while line:=await asyncio.to_thread(sys.stdin.readline):
            try:
                request=json.loads(line)
                for m in feed.markets.values():m.book_at=m.stats_at=time.time()
                if request.get('control')=='identity': result=dict(evm=ALICE.address.lower(),solana=SOL)
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

asyncio.run(main())
