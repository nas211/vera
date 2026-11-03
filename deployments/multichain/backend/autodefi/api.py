import asyncio,json,time
from contextlib import asynccontextmanager
from decimal import Decimal as D
from fastapi import FastAPI,Request,WebSocket,HTTPException,Query
from typing import Literal
from fastapi.responses import JSONResponse,FileResponse
from fastapi.staticfiles import StaticFiles
from vela.lighter.feed import LighterFeed
from vela.db import transaction,get,put
from .config import Config,ROOT,CHAIN_ID
from .store import open_db,once
from .auth import Auth,address,digest
from .chain import DogeOS,certificate
from .lending import Lending
from .trading import GovernedPaper,ProtocolModel,model_module
from .runtime import Runtime
from .risk import RISK
from .repayment import Repayment,TradingCapital
from .orchestration import AgentOrchestrator
from .vaults import VaultManagement
from .adapters import adapter,CHAINS,split_owner
from .multichain_auth import MultichainAuth
from .simulated_vaults import SimulatedVaults
from .ownership import OwnedDatabase
from .http_security import RequestLimits
from .staking import DemoStaking
from .build import fingerprint
from .paper_desk import attach as attach_paper_desk

def create_app(config=None,feed=None,chain=None,model=None,frontend=None):
    config=config or Config.from_env()
    source_fingerprint=fingerprint()
    # Validate the release candidate through the same ASGI routes without
    # replacing the directory used by the running preview.
    if frontend is not None and (frontend!='candidate' or not config.multichain):raise ValueError('Isolated multichain candidate required')
    feed=feed or LighterFeed();chain=chain or DogeOS(config);model=model or ProtocolModel()
    db=OwnedDatabase(config.db_path,open_db)
    try:
        if hasattr(model,'bind_usage'):model.bind_usage(config.db_path)
    except BaseException:
        db.close()
        raise
    try:
        paper=GovernedPaper(db,config.paper(),feed,require_session=True);auth=(MultichainAuth if config.multichain else Auth)(db,config);lending=Lending(db)
        repayment=Repayment(db,paper);capital=TradingCapital(db,paper)
        agent=AgentOrchestrator(db,paper,repayment);vaults=VaultManagement(db,chain)
        runtime=Runtime(config,db,feed,paper,model,chain)
        web_root=ROOT/'site/prepared' if (ROOT/'site/prepared/index.html').is_file() else ROOT/'site/dist'
        if config.multichain: web_root=ROOT/'site/multichain'
        if frontend: web_root=ROOT/'site'/frontend
        simulated=SimulatedVaults(db,paper)
    except BaseException:
        db.close()
        raise
    @asynccontextmanager
    async def lifespan(app):
        try:
            await runtime.start()
            yield
        finally:await runtime.stop();db.close()
    app=FastAPI(title='VELLA — Autonomous DeFi Trading Protocol',lifespan=lifespan)
    app.add_middleware(RequestLimits)
    app.state.db=db;app.state.paper=paper;app.state.runtime=runtime;app.state.auth=auth
    attach_paper_desk(app,db,paper,runtime,model,config.origin)
    @app.middleware('http')
    async def security(request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('origin')!=config.origin or request.headers.get('x-adp-client')!='web':
                return JSONResponse({'error':'Same-origin request required'},status_code=403)
            length=request.headers.get('content-length','0')
            if len(length)>16 or not length.isdigit() or int(length)>16384: return JSONResponse({'error':'Request too large'},status_code=413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api') else 'no-cache'
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin'
        return response
    @app.exception_handler(ValueError)
    async def invalid(req,exc):return JSONResponse({'error':str(exc)},status_code=400)
    @app.exception_handler(PermissionError)
    async def denied(req,exc):return JSONResponse({'error':str(exc)},status_code=401 if str(exc) in ('Authentication required','Session expired') else 403)
    def network(req):
        selected=req.headers.get('x-vella-network','dogeos')
        if not config.multichain and selected!='dogeos':raise ValueError('Multichain preview required')
        return adapter(selected)
    def owner(req):
        chain=network(req)
        return auth.session(req.cookies.get('adp_session'),req.headers.get('x-wallet-address'),**({'network':chain.id} if config.multichain else {}))
    def financial_context(req,who,body):
        if not config.multichain or network(req).id=='dogeos':return
        loan=lending.loan(who)
        if body.get('loanId')!=(loan['id'] if loan else None):raise ValueError('Matching loan identifier required')
        row=db.execute('SELECT address FROM vaults WHERE owner=?',(who,)).fetchone()
        if body.get('vaultId')!=(row[0] if row else None):raise ValueError('Matching vault identifier required')
    @app.get('/api/config')
    async def public_config(req:Request):
        selected=network(req);descriptor=selected.public(config)
        return dict(moduleApiVersion=1,multichain=config.multichain,chain=descriptor,
            chains=[a.public(config) for a in CHAINS.values()] if config.multichain else [],
            brand=json.loads((ROOT/'config/brand.json').read_text()),network=json.loads((ROOT/'config/dogeos-testnet.json').read_text()) if selected.id=='dogeos' else descriptor['network'],
            risk=RISK,factory=config.factory or None,myDogeClientId=config.client_id,walletConnectProjectId=config.walletconnect_project_id,mode='PAPER',lending='DEMO',liveEnabled=False,realFunding=False,
            modelConfigured=model.ready,networkEnabled=config.network_enabled,attestorConfigured=bool(config.attestor_key),demoStakingEnabled=config.demo_staking,
            abis={n:json.loads((ROOT/'config'/f'{n}.abi.json').read_text()) for n in ('VaultFactory','DogeCollateralVault')})
    @app.get('/api/health')
    async def health():
        # The connection record only changes when the model is called; mark it stale
        # after the same 5-minute window the entry gate uses, rather than implying a probe.
        connection=get(db,'ai-connection')
        if connection:connection=dict(connection,fresh=bool(connection.get('ok')) and 0<=time.time()-connection.get('at',0)<=300)
        usage=getattr(getattr(model,'opener',None),'ledger',None)
        return dict(mode='PAPER',lending='DEMO',liveEnabled=False,realFunding=False,sourceFingerprint=source_fingerprint,modelConfigured=model.ready,modelConnection=connection,
            modelUsage=usage.summary() if usage else None,
            engineReady=bool(runtime.last_cycle) and 0<=time.time()-runtime.last_cycle<3,
            backgroundTasks=[dict(name=t.get_name(),running=not t.done()) for t in runtime.tasks],
            backgroundRestarts=runtime.restarts,
            marketData=bool(feed.markets) and all(m.fresh() for m in feed.markets.values()),runtimeError=runtime.error)
    @app.get('/api/ready')
    async def ready():
        state=await health()
        state['ready']=bool(state['engineReady'] and state['marketData'] and runtime.tasks and all(not t.done() for t in runtime.tasks)
                           and not runtime.errors.get('runtime') and not runtime.errors.get('task:position-risk-and-execution'))
        return JSONResponse(state,status_code=200 if state['ready'] else 503)
    @app.post('/api/auth/nonce')
    async def nonce(req:Request):
        b=await req.json();return auth.challenge(b.get('address'),b.get('chainId'),**({'network':network(req).id} if config.multichain else {}))
    @app.post('/api/auth/verify')
    async def verify(req:Request):
        b=await req.json();selected=network(req);token,who=auth.verify(b.get('nonce'),b.get('signature'),b.get('address'),**({'network':selected.id} if config.multichain else {}))
        response=JSONResponse(dict(address=split_owner(who)[1],network=selected.id,authenticated=True))
        response.set_cookie('adp_session',token,max_age=86400,httponly=True,secure=config.origin.startswith('https:'),samesite='strict',path='/api')
        return response
    @app.get('/api/session')
    async def session(req:Request):
        chain,wallet=split_owner(owner(req));return dict(address=wallet,network=chain,authenticated=True)
    @app.post('/api/logout')
    async def logout(req:Request):
        token=req.cookies.get('adp_session')
        try:
            who=auth.session(token)
            with transaction(db):paper.pause_entries(who);repayment.disable(who,'Wallet disconnected');auth.logout(token)
        except PermissionError:pass
        response=JSONResponse({'authenticated':False});response.delete_cookie('adp_session',path='/api');return response
    @app.get('/api/account')
    async def snapshot(req:Request):
        who=owner(req);v=db.execute('SELECT data FROM vaults WHERE owner=?',(who,)).fetchone()
        return dict(address=split_owner(who)[1],network=split_owner(who)[0],vault=json.loads(v[0]) if v else None,
            health=lending.health(who) if v and lending.quote(who) else None,paper=paper.snapshot(who),
            repayment=repayment.status(who),agent=agent.status(who),tradingPermission=paper.permission(who),
            directAllocation=get(db,'demo-allocated:'+who,'0'),
            demoSurplus=get(db,'demo-surplus:'+who,'0'),events=[dict(x) for x in db.execute('SELECT * FROM loan_events WHERE owner=? ORDER BY id DESC LIMIT 80',(who,))],
            transactions=[dict(x) for x in db.execute('SELECT * FROM chain_receipts WHERE owner=? ORDER BY block DESC LIMIT 40',(who,))])
    @app.post('/api/vault/sync')
    async def sync(req:Request):
        who=owner(req);b=await req.json()
        if network(req).simulated:raise ValueError('No blockchain synchronization for simulated vaults')
        return await vaults.sync(who,b.get('tx'))
    @app.post('/api/simulated-vault/{action}')
    async def simulated_vault(action:str,req:Request):
        who=owner(req);b=await req.json()
        if not config.multichain:raise ValueError('Multichain preview required')
        def mutate():
            financial_context(req,who,b)
            return simulated.mutate(who,action,b)
        return once(db,who,req.headers.get('idempotency-key'),{'action':'simulated-vault:'+action,'payload':b},mutate)
    @app.post('/api/repay')
    async def repay(req:Request):
        who=owner(req);b=await req.json()
        if b.get('confirm') is not True:raise ValueError('Confirm simulated repayment')
        financial_context(req,who,b)
        return once(db,who,req.headers.get('idempotency-key'),{'action':'repay','payload':b},lambda:repayment.pay(who,b.get('amount'),full=b.get('full') is True))
    @app.post('/api/repayment/policy')
    async def repayment_policy(req:Request):
        who=owner(req);b=await req.json()
        financial_context(req,who,b)
        return once(db,who,req.headers.get('idempotency-key'),{'action':'repayment-policy','payload':b},lambda:repayment.configure(who,b))
    @app.post('/api/trading/allocate')
    async def allocate(req:Request):
        who=owner(req);b=await req.json()
        return once(db,who,req.headers.get('idempotency-key'),{'action':'allocate','payload':b},lambda:capital.allocate(who,b.get('amount'),b.get('confirm')))
    @app.post('/api/trading/withdraw')
    async def withdraw_demo(req:Request):
        who=owner(req);b=await req.json()
        return once(db,who,req.headers.get('idempotency-key'),{'action':'withdraw-demo','payload':b},lambda:capital.withdraw(who,b.get('amount'),b.get('confirm')))
    @app.get('/api/staking')
    async def staking_status(req:Request):
        who=owner(req)
        if not config.demo_staking:raise HTTPException(503,'Demo staking is not enabled; funded staking is unavailable')
        return DemoStaking(db,paper).status(who)
    @app.post('/api/staking/{action}')
    async def staking_action(action:Literal['deposit','withdraw','claim'],req:Request):
        who=owner(req);body=await req.json()
        if not config.demo_staking:raise HTTPException(503,'Demo staking is not enabled; funded staking is unavailable')
        return once(db,who,req.headers.get('idempotency-key'),dict(action='demo-staking:'+action,payload=body),
                    lambda:DemoStaking(db,paper).mutate(who,action,body))
    @app.post('/api/trading/permission')
    async def permission(req:Request):
        who=owner(req);b=await req.json()
        return once(db,who,req.headers.get('idempotency-key'),{'action':'trading-permission','payload':b},lambda:paper.configure(who,b))
    @app.post('/api/quote')
    async def quote(req:Request):
        who=owner(req);b=await req.json()
        with transaction(db):return lending.quote(who,b.get('drop','0'))
    @app.post('/api/borrow')
    async def borrow(req:Request):
        who=owner(req);b=await req.json()
        if network(req).simulated:raise ValueError('Use simulated vault borrowing; no chain certificates')
        # Verify signing capability before any credit, but no key is sent to the browser.
        v=lending.vault(who);certificate(config,v,1,v['principalMicro'])
        result=once(db,who,req.headers.get('idempotency-key'),{'action':'borrow','vault':v['address'],'nonce':v['loanNonce'],'loan':v['loanId']},lambda:lending.borrow(who))
        return dict(loan=result,certificate=certificate(config,v,1,v['principalMicro']))
    @app.post('/api/borrow/cancel')
    async def cancel_borrow(req:Request):
        who=owner(req)
        if network(req).simulated:raise ValueError('No pending blockchain request')
        v=lending.vault(who)
        proof=certificate(config,v,3)
        with transaction(db):lending.cancel_unissued(who)
        return proof
    @app.post('/api/certificate')
    async def cert(req:Request):
        who=owner(req)
        if network(req).simulated:raise ValueError('Simulations have no blockchain certificate')
        v=lending.vault(who);l=lending.loan(who)
        if not l or l['nonce']!=v['loanNonce']:raise ValueError('Matching loan required')
        if v['loanState']==1 and l['state']=='ACTIVE':return certificate(config,v,1,v['principalMicro'])
        if v['loanState']==2 and l['state'] in ('AWAITING_CHAIN','DEFAULT_PENDING'):
            return certificate(config,v,2,int(D(l['paid'])*1000000),int(l['seized']),int(D(l['bad_debt'])*1000000))
        raise ValueError('Settlement is not reconciled; collateral remains locked')
    @app.post('/api/trading/start')
    async def start(req:Request):
        who=owner(req)
        if who in runtime.starting:raise HTTPException(409,'A trading start check is already pending')
        if not model.ready:raise ValueError('Dedicated AI model is not configured')
        paper.gate(who,require_running=False)
        generation=get(db,'paper-prefs:'+who,{}).get('generation',0)
        revision=paper.revision(who)
        runtime.starting.add(who)
        try:
            try:
                await runtime.run_blocking(model.ask,'review',{'purpose':'Connection preflight only; no trade authorization. Return delay.','financialPosition':dict(loanHealth=lending.health(who) if db.execute('SELECT 1 FROM vaults WHERE owner=?',(who,)).fetchone() and lending.quote(who) else None,trading=paper.snapshot(who),repayment=repayment.status(who))},model_module.ASSESSMENT)
            except Exception:raise ValueError('AI connection unavailable; trading remains stopped') from None
            # Stop, logout, expiry, or a changed permission during network I/O
            # invalidates this request. Model success cannot restore consent.
            if owner(req)!=who:raise PermissionError('Wallet/session mismatch')
            with transaction(db):
                if generation!=get(db,'paper-prefs:'+who,{}).get('generation',0) or revision!=paper.revision(who):
                    raise ValueError('Trading permission changed during start; reconnect or retry')
                put(db,'ai-connection',dict(ok=True,at=time.time(),model=model.model))
                put(db,'agent-session:'+who,digest(req.cookies['adp_session']))
                paper.start(who,model)
        finally:
            runtime.starting.discard(who)
        return {'status':'ACTIVE','mode':'PAPER'}
    @app.post('/api/trading/stop')
    async def stop(req:Request):
        who=owner(req)
        with transaction(db):paper.pause_entries(who)
        return {'status':'STOPPED','protectiveManagement':True}
    @app.post('/api/trading/positions/{position_id}/close')
    async def close_position(position_id:str,req:Request):
        who=owner(req);body=await req.json()
        if set(body)-{'fraction','confirm'} or body.get('confirm') is not True:
            raise ValueError('Confirm the simulated position reduction')
        key=req.headers.get('idempotency-key')
        payload=dict(reduceOnly=True,positionId=position_id,fraction=body.get('fraction','1'),reason='owner_reduction')
        return once(db,who,key,{'action':'close-position','position':position_id,'payload':body},
                    lambda:paper.submit(who,payload,key))
    @app.post('/api/trading/orders/{order_id}/cancel')
    async def cancel_order(order_id:str,req:Request):
        who=owner(req);key=req.headers.get('idempotency-key')
        def cancel():
            paper.cancel(who,order_id)
            return dict(id=order_id,status='cancelled',mode='PAPER')
        return once(db,who,key,dict(action='cancel-order',order=order_id),cancel)
    @app.get('/api/history/{kind}')
    async def history(kind:Literal['trades','orders','transactions'],req:Request,
                      before:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=100)):
        who=owner(req)
        table,column={'trades':('trades','user'),'orders':('paper_orders','user'),
                      'transactions':('loan_events','owner')}[kind]
        sql=f'SELECT rowid AS history_cursor,* FROM {table} WHERE {column}=?'
        values=[who]
        if before is not None:sql+=' AND rowid<?';values.append(before)
        rows=[dict(row) for row in db.execute(sql+' ORDER BY rowid DESC LIMIT ?',(*values,limit+1))]
        more=len(rows)>limit;rows=rows[:limit]
        return dict(kind=kind,items=rows,nextCursor=rows[-1]['history_cursor'] if more else None,
                    scope='All-time authenticated wallet and selected network; simulated ledger records')
    @app.post('/api/settle')
    async def settle(req:Request):
        who=owner(req);b=await req.json()
        financial_context(req,who,b)
        def action():return agent.settle(who,b.get('confirm'))
        return once(db,who,req.headers.get('idempotency-key'),{'action':'settle',**b},action)
    @app.post('/api/liquidate')
    async def liquidate(req:Request):
        who=owner(req);b=await req.json()
        financial_context(req,who,b)
        return once(db,who,req.headers.get('idempotency-key'),{'action':'liquidate',**b},lambda:lending.liquidate(who,b.get('confirm')))
    @app.get('/api/markets')
    async def markets():return dict(markets=[m.public() for m in feed.markets.values()],error=feed.error,dogeSupported=False)
    @app.websocket('/ws/markets')
    async def market_ws(ws:WebSocket):
        if ws.headers.get('origin')!=config.origin:await ws.close(code=1008);return
        await ws.accept()
        try:
            while True:
                await ws.send_json(dict(markets=[m.public() for m in feed.markets.values()],privateData=False));await asyncio.sleep(1)
        except Exception:pass
    if (web_root/'assets').is_dir():app.mount('/assets',StaticFiles(directory=web_root/'assets'),name='assets')
    @app.get('/')
    @app.get('/vault')
    async def index():
        if not (web_root/'index.html').is_file():raise HTTPException(503,'Build frontend with npm run build')
        return FileResponse(web_root/'index.html')
    return app
