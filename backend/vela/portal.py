"""Wallet-authenticated demo accounts. No custody, transactions or real funding routes."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal as D
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .db import get, put, transaction
from .paper import money
from .agents import bars_for
from .reuse import model_module
from .lighter.feed import decimal

CHAIN=6281971
COOKIE="vela_demo_session"

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def iso(ts): return datetime.fromtimestamp(ts,timezone.utc).isoformat().replace('+00:00','Z')

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Challenge(Strict):
    address:str=Field(pattern=r'^0x[0-9a-fA-F]{40}$')
    chainId:int
class Verify(Strict):
    challengeId:str=Field(pattern=r'^[0-9a-f]{64}$')
    signature:str=Field(pattern=r'^0x[0-9a-fA-F]{130}$')
class Amount(Strict):
    clientId:str=Field(min_length=8,max_length=100)
    amount:str=Field(max_length=30)
    asset:str='USDC'
class Confirm(Strict):
    clientId:str=Field(min_length=8,max_length=100)
    confirm:bool=False
class Prefs(Strict):
    leverage:int=Field(ge=1,le=3)

class Portal:
    def __init__(self,runtime,origin=None):
        self.rt=runtime
        self.origin=(origin or os.getenv('VELA_PORTAL_ORIGIN','http://127.0.0.1:8790')).rstrip('/')
        url=urlsplit(self.origin)
        if not url.hostname or url.scheme not in ('http','https') or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError('Invalid portal origin')
        if url.scheme=='http' and url.hostname not in ('localhost','127.0.0.1','testserver'):
            raise ValueError('Non-loopback portal requires HTTPS')
        self.host=url.netloc;self.secure=url.scheme=='https'
        self.limits={};self.start_lock=asyncio.Lock()
        runtime.db.executescript('''
        CREATE TABLE IF NOT EXISTS portal_challenges(id TEXT PRIMARY KEY,address TEXT,chain_id INTEGER,message TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS portal_sessions(id TEXT PRIMARY KEY,user TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS portal_transfers(id TEXT PRIMARY KEY,user TEXT,client_id TEXT,kind TEXT,amount TEXT,created REAL,session TEXT,UNIQUE(user,client_id));
        ''')

    def guard(self,request):
        if request.headers.get('host')!=self.host:
            raise HTTPException(403,'Invalid application origin')
        if request.method!='GET' and (request.headers.get('origin')!=self.origin or request.headers.get('x-vela-portal')!='1'):
            raise HTTPException(403,'Refresh Vela and try again')

    def rate(self,request):
        now=time.time();key=request.client.host if request.client else 'unknown'
        self.limits={k:v for k,v in self.limits.items() if now-v[0]<60}
        start,n=self.limits.get(key,(now,0))
        if n>=30 or len(self.limits)>2000:raise HTTPException(429,'Please wait a minute before trying again')
        self.limits[key]=(start,n+1)

    def user(self,request):
        self.guard(request)
        token=request.cookies.get(COOKIE,'')
        if not re.fullmatch(r'[a-f0-9]{64}',token):raise HTTPException(401,'Connect your wallet to continue')
        row=self.rt.db.execute('SELECT * FROM portal_sessions WHERE id=? AND expires>?',(digest(token),time.time())).fetchone()
        if not row:raise HTTPException(401,'Your session expired. Reconnect your wallet')
        self.check_wallet(request,row['user'])
        return row['user']

    def check_wallet(self,request,user):
        # Cookies are shared across tabs. A stale tab must never read or mutate
        # the account selected by another tab's newer wallet login.
        address=request.headers.get('x-vela-wallet')
        if address is not None and (not re.fullmatch(r'0x[0-9a-fA-F]{40}',address) or user!='wallet:'+address.lower()):
            raise HTTPException(403,'Wallet session changed. Reconnect your wallet')

    def ensure_account(self,address):
        user='wallet:'+address.lower();db=self.rt.db
        if not db.execute('SELECT 1 FROM users WHERE address=?',(user,)).fetchone():
            if db.execute('SELECT count(*) FROM users').fetchone()[0]>=500:raise HTTPException(429,'Demo capacity reached')
            db.execute("INSERT INTO users(address,session,strategy,status,initial,balance,doge_in,started,start_tx) VALUES (?,?,1,'active','0','0','0',?,'demo')",(user,uuid.uuid4().hex,time.time()))
            put(db,'paper-prefs:'+user,dict(autonomous=False,symbol='BTC/USD',symbols=['BTC/USD','ETH/USD','SOL/USD'],leverage='2',generation=0))
        return user

    def snapshot(self,user):
        result=self.rt.adapter.snapshot(user)
        result.update(wallet=user.removeprefix('wallet:'),network='DogeOS Chikyū Testnet',chainId=CHAIN,
            demo=True,realFundingEnabled=False,strategy='AI Managed',
            allocated=str(sum((D(p['entry'])*D(p['qty'])/D(p['leverage']) for p in result['positions']),D(0))),
            model=self.model_state(),riskReady=not self.rt.error and 0<self.rt.settings.loss_budget<=2,
            transactions=[dict(r) for r in self.rt.db.execute('SELECT id,kind,amount,created FROM portal_transfers WHERE user=? ORDER BY created DESC LIMIT 100',(user,))])
        # Cash flows change the cost basis, never performance. Today's headline uses
        # closed fills only, matching the explicit scope of its count/wins/losses.
        result['todayPnlScope']='Closed fills today (UTC), including fees and allocated funding'
        return result

    def model_state(self):
        c=get(self.rt.db,'ai-connection',{}) or {}
        connected=bool(self.rt.model.ready and c.get('ok') and time.time()-c.get('at',0)<300)
        return dict(configured=bool(self.rt.model.ready),connected=connected,
            checkedAt=c.get('at'),status='Connected' if connected else 'Awaiting connection check' if self.rt.model.ready and c.get('ok') is not False else 'Disconnected')

    def pause(self,user): self.rt.adapter.pause_entries(user)

    def owned(self,table,identifier,user):
        # Use the same response for unknown and foreign identifiers.
        if not self.rt.db.execute(f'SELECT 1 FROM {table} WHERE id=? AND user=?',(identifier,user)).fetchone():
            raise HTTPException(403,'This item is not available to your wallet')

    def pending(self,user,entries_only=False):
        rows=self.rt.db.execute("SELECT payload FROM paper_orders WHERE user=? AND status='pending'",(user,)).fetchall()
        return any(not entries_only or not json.loads(row['payload']).get('reduceOnly') for row in rows)

    def expire_agents(self):
        now=time.time();db=self.rt.db
        db.execute('DELETE FROM portal_challenges WHERE expires<?',(now,))
        db.execute('DELETE FROM portal_sessions WHERE expires<?',(now,))
        for row in db.execute("SELECT address FROM users WHERE address LIKE 'wallet:%'").fetchall():
            user=row['address']
            if (get(db,'paper-prefs:'+user,{}).get('autonomous') or self.pending(user,entries_only=True)) and not db.execute('SELECT 1 FROM portal_sessions WHERE user=? AND expires>?',(user,now)).fetchone():self.pause(user)

    def duplicate(self,user,client_id,kind,amount):
        row=self.rt.db.execute('SELECT * FROM portal_transfers WHERE user=? AND client_id=?',(user,client_id)).fetchone()
        if row and (row['kind']!=kind or D(row['amount'])!=amount):raise HTTPException(409,'This request ID was already used for a different action')
        return bool(row)

    def funding(self,user,body,kind):
        if body.asset!='USDC':raise HTTPException(409,'Only simulated USDC is available')
        try:amount=decimal(body.amount,True)
        except ValueError:raise HTTPException(422,'Enter a valid demo amount') from None
        if not D('1')<=amount<=100000 or amount!=money(amount):raise HTTPException(422,'Choose 1 to 100,000 simulated USDC, up to six decimals')
        db=self.rt.db
        with transaction(db):
            if self.duplicate(user,body.clientId,kind,amount):return self.snapshot(user)
            a=self.rt.adapter.snapshot(user)
            if kind=='demo_withdrawal' and (a['preferences'].get('autonomous') or a['positions'] or self.pending(user)):
                raise HTTPException(409,'Stop trading and close positions before removing demo funds')
            delta=amount if kind=='demo_deposit' else -amount
            balance=D(a['balance'])+delta
            if balance<0 or balance>1000000:raise HTTPException(409,'Demo balance limit exceeded')
            row=db.execute('SELECT session FROM users WHERE address=?',(user,)).fetchone()
            db.execute('UPDATE users SET balance=?,initial=? WHERE address=?',(str(balance),str(D(a['initial'])+delta),user))
            db.execute('INSERT INTO portal_transfers VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,user,body.clientId,kind,str(amount),time.time(),row['session']))
            self.rt.adapter.activity(user,kind,f'{amount} simulated USDC · no wallet transfer')
        return self.snapshot(user)

    async def check_model(self):
        if not self.rt.model.ready:raise HTTPException(503,'AI is disconnected. Please try again later')
        c=get(self.rt.db,'ai-connection',{}) or {}
        if c.get('ok') and time.time()-c.get('at',0)<300:return
        now=time.time()
        calls=[t for t in get(self.rt.db,'ai-calls',[]) if now-t<86400]
        if len(calls)>=200:raise HTTPException(429,'AI demo capacity reached. Please try again later')
        put(self.rt.db,'ai-calls',calls+[now])
        def probe():
            with self.rt.model_lock:
                # The same model rate guard is used by the original agents.
                delay=2.1-(time.time()-self.rt.model.last) if hasattr(self.rt.model,'last') else 0
                if delay>0:time.sleep(delay)
                return self.rt.model.ask('review',dict(markets={},purpose='Connectivity check only; no strategy or trading permission'),model_module.ASSESSMENT)
        try:
            await asyncio.to_thread(probe)
            put(self.rt.db,'ai-connection',dict(ok=True,at=time.time(),model=getattr(self.rt.model,'model','unknown')))
        except Exception:
            put(self.rt.db,'ai-connection',dict(ok=False,at=time.time()))
            raise HTTPException(503,'AI connection unavailable. No trading was started') from None


def attach_portal(app,Order,Close):
    router=APIRouter(prefix='/api/portal')
    def service():return app.state.runtime.portal

    @app.middleware('http')
    async def private_responses(request,call_next):
        response=await call_next(request)
        if request.url.path.startswith('/api/portal/'):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response

    @router.post('/auth/challenge')
    async def challenge(body:Challenge,request:Request):
        p=service();p.guard(request);p.rate(request)
        if body.chainId!=CHAIN:raise HTTPException(409,'Switch to DogeOS Chikyū Testnet')
        now=time.time();nonce=secrets.token_hex(16);cid=secrets.token_hex(32)
        address=body.address
        from eth_utils import to_checksum_address
        address=to_checksum_address(address)
        message=f'{p.host} wants you to sign in with your Ethereum account:\n{address}\n\nSign in to Vela Demo. No transaction, deposit or trading authorization.\n\nURI: {p.origin}/trade\nVersion: 1\nChain ID: {CHAIN}\nNonce: {nonce}\nIssued At: {iso(now)}\nExpiration Time: {iso(now+300)}'
        p.rt.db.execute('DELETE FROM portal_challenges WHERE expires<?',(now,))
        p.rt.db.execute('INSERT INTO portal_challenges VALUES (?,?,?,?,?)',(cid,address.lower(),CHAIN,message,now+300))
        return dict(challengeId=cid,message=message)

    @router.post('/auth/verify')
    async def verify(body:Verify,request:Request,response:Response):
        p=service();p.guard(request);p.rate(request);db=p.rt.db
        row=db.execute('SELECT * FROM portal_challenges WHERE id=?',(body.challengeId,)).fetchone()
        db.execute('DELETE FROM portal_challenges WHERE id=?',(body.challengeId,))
        if not row or row['expires']<time.time():raise HTTPException(401,'Signature challenge expired. Connect again')
        try:signer=Account.recover_message(encode_defunct(text=row['message']),signature=body.signature).lower()
        except Exception:raise HTTPException(401,'Wallet signature could not be verified') from None
        if signer!=row['address']:raise HTTPException(401,'Wallet signature does not match')
        with transaction(db):
            user=p.ensure_account(signer)
            # A new wallet login on this browser must not leave its former
            # account opening entries after the cookie is replaced.
            old=db.execute('SELECT user FROM portal_sessions WHERE id=?',(digest(request.cookies.get(COOKIE,'')),)).fetchone()
            if old:
                p.pause(old['user'])
                db.execute('DELETE FROM portal_sessions WHERE id=?',(digest(request.cookies.get(COOKIE,'')),))
            token=secrets.token_hex(32)
            db.execute('INSERT INTO portal_sessions VALUES (?,?,?)',(digest(token),user,time.time()+86400))
        response.set_cookie(COOKIE,token,max_age=86400,httponly=True,secure=p.secure,samesite='strict',path='/api/portal')
        response.headers['Cache-Control']='no-store'
        return p.snapshot(user)

    @router.get('/account')
    async def account(request:Request,response:Response):
        p=service();response.headers['Cache-Control']='no-store';return p.snapshot(p.user(request))

    @router.post('/auth/logout')
    async def logout(request:Request,response:Response):
        p=service();p.guard(request)
        token=request.cookies.get(COOKIE,'');row=p.rt.db.execute('SELECT user FROM portal_sessions WHERE id=?',(digest(token),)).fetchone()
        if row:
            p.check_wallet(request,row['user'])
            with transaction(p.rt.db):
                p.pause(row['user'])
                p.rt.db.execute('DELETE FROM portal_sessions WHERE id=?',(digest(token),))
        response.delete_cookie(COOKIE,path='/api/portal',httponly=True,secure=p.secure,samesite='strict')
        return dict(disconnected=True)

    @router.post('/deposit')
    async def deposit(body:Amount,request:Request):
        p=service();return p.funding(p.user(request),body,'demo_deposit')

    @router.post('/withdraw')
    async def withdraw(body:Amount,request:Request):
        p=service();return p.funding(p.user(request),body,'demo_withdrawal')

    @router.post('/start')
    async def start(request:Request):
        p=service();user=p.user(request)
        prefs=get(p.rt.db,'paper-prefs:'+user,{})
        revision=prefs.get('generation',0)
        async with p.start_lock:
            await p.check_model()
            p.user(request)  # Recheck logout/expiry while model connectivity was pending.
            current=get(p.rt.db,'paper-prefs:'+user,{})
            if current.get('generation',0)!=revision:raise HTTPException(409,'Trading controls changed. Start again if intended')
            a=p.snapshot(user)
            if p.rt.error or time.time()-p.rt.last_cycle>3:raise HTTPException(503,'Risk engine is reconnecting')
            if a['available'] is None or D(a['available'])<10:raise HTTPException(409,'Deposit at least 10 demo USDC to start')
            if not a['riskReady']:raise HTTPException(503,'Risk controls are unavailable')
            if not all(m.fresh() and bars_for(p.rt.db,m.symbol,time.time()) for m in p.rt.feed.markets.values()) or len(p.rt.feed.markets)!=3:
                raise HTTPException(503,'Waiting for fresh market data')
            put(p.rt.db,'paper-prefs:'+user,dict(current,autonomous=True))
            p.rt.adapter.activity(user,'agent_started','AI Managed active · paper trading only')
        return p.snapshot(user)

    @router.post('/stop')
    async def stop(request:Request):
        p=service();user=p.user(request)
        with transaction(p.rt.db):p.pause(user)
        return p.snapshot(user)

    @router.post('/close-all')
    async def close_all(body:Confirm,request:Request):
        p=service();user=p.user(request)
        if not body.confirm:raise HTTPException(409,'Confirm closing all simulated positions')
        with transaction(p.rt.db):
            p.pause(user)
            for pos in p.rt.adapter.getPosition(user):
                try:p.rt.adapter.submit(user,dict(reduceOnly=True,positionId=pos['id'],fraction='1'),body.clientId+':'+pos['id'])
                except ValueError as exc:raise HTTPException(409,str(exc)) from None
        return p.snapshot(user)

    @router.post('/preferences')
    async def preferences(body:Prefs,request:Request):
        p=service();user=p.user(request);prefs=get(p.rt.db,'paper-prefs:'+user,{})
        put(p.rt.db,'paper-prefs:'+user,dict(prefs,leverage=str(body.leverage)))
        return p.snapshot(user)

    @router.post('/orders',status_code=202)
    async def order(body:Order,request:Request):
        p=service();user=p.user(request)
        if p.rt.error or time.time()-p.rt.last_cycle>3:raise HTTPException(503,'Risk engine is reconnecting')
        payload=body.model_dump(exclude_none=True);client_id=payload.pop('clientId')
        try:
            with transaction(p.rt.db):return p.rt.adapter.submit(user,payload,client_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @router.post('/positions/{identifier}/close',status_code=202)
    async def close(identifier:str,body:Close,request:Request):
        p=service();user=p.user(request)
        p.owned('positions',identifier,user)
        try:
            with transaction(p.rt.db):return p.rt.adapter.submit(user,dict(reduceOnly=True,positionId=identifier,fraction=body.fraction),body.clientId)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @router.post('/orders/{identifier}/cancel')
    async def cancel(identifier:str,request:Request):
        p=service();user=p.user(request)
        p.owned('paper_orders',identifier,user)
        try:p.rt.adapter.cancel(user,identifier)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        return p.snapshot(user)

    @router.post('/reset')
    async def reset(body:Confirm,request:Request):
        p=service();user=p.user(request);db=p.rt.db
        if not body.confirm:raise HTTPException(409,'Confirm resetting the demo account')
        with transaction(db):
            if p.duplicate(user,body.clientId,'demo_reset',D(0)):return p.snapshot(user)
            a=p.snapshot(user)
            if a['positions'] or p.pending(user) or a['preferences'].get('autonomous'):
                raise HTTPException(409,'Stop trading and close positions before resetting')
            # Archive before resetting displayed performance. Keep a separate audit record.
            audit={table:[dict(row) for row in db.execute(f'SELECT * FROM {table} WHERE user=?',(user,))]
                for table in ('trades','equity','paper_costs','paper_orders','paper_activity')}
            put(db,'portal-archive:'+user+':'+str(time.time()),dict(account=a,records=audit))
            for table in ('trades','equity','paper_costs','paper_orders','paper_activity'):
                db.execute(f'DELETE FROM {table} WHERE user=?',(user,))
            for key,value in (('paper-cost-totals:',{}),('paper-funding-incomplete:',False),('ai-decision:',None),('ai-approved:',None),('ai-stage:',{}),('ai-last-research:',0)):
                put(db,key+user,value)
            session=uuid.uuid4().hex
            db.execute("UPDATE users SET initial='0',balance='0',session=?,started=? WHERE address=?",(session,time.time(),user))
            p.pause(user)
            db.execute('INSERT INTO portal_transfers VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,user,body.clientId,'demo_reset','0',time.time(),session))
        return p.snapshot(user)

    app.include_router(router)
