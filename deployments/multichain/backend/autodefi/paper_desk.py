"""Existing independent paper desk, attached to the current single ledger owner."""
import asyncio
import time
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field
from vela.agents import bars_for, features
from vela.db import get, put, transaction
from vela.lighter.engine import identity
from .store import account, once
from .repayment import TradingCapital
from .trading import agents_for


class Order(BaseModel):
    model_config = ConfigDict(extra='forbid')
    clientId: str = Field(min_length=8, max_length=120)
    symbol: Literal['BTC/USD', 'ETH/USD', 'SOL/USD']
    side: Literal['long', 'short']
    type: Literal['market', 'limit'] = 'market'
    notional: str = Field(default='100', max_length=40)
    leverage: Literal['1', '2', '3'] = '2'
    stopBps: str = Field(default='50', max_length=40)
    targetBps: str = Field(default='75', max_length=40)
    limitPrice: str | None = Field(default=None, max_length=40)
    riskBudget: str = Field(default='2', max_length=40)


class Close(BaseModel):
    model_config = ConfigDict(extra='forbid')
    clientId: str = Field(min_length=8, max_length=120)
    fraction: str = Field(default='1', max_length=40)


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: Literal['BTC/USD', 'ETH/USD', 'SOL/USD']
    leverage: Literal['1', '2', '3'] = '2'
    autonomous: bool = False


def attach(app, db, paper, runtime, model, origin):
    router = APIRouter(prefix='/api/paper')

    def identities(token):
        try:
            owner = identity(token)
        except ValueError:
            raise HTTPException(401, 'Paper session required') from None
        # Not a wallet-session digest: these capabilities cannot authenticate a
        # wallet API cookie. Only the existing runtime reads this binding.
        return owner, 'desk:'+owner.removeprefix('paper:')

    def auth(token):
        owner, binding = identities(token)
        row = db.execute('SELECT address,expires FROM auth_sessions WHERE hash=?', (binding,)).fetchone()
        if not row or row['address'] != owner or row['expires'] <= time.time():
            raise HTTPException(401, 'Open the paper desk to establish its session')
        return owner

    def markets():
        return dict(mode='PAPER', liveEnabled=False, realFunding=False, privateData=False,
                    connected=paper.feed.connected, reconnects=paper.feed.reconnects,
                    error=runtime.error or paper.feed.error, serverTime=time.time(),
                    engineAt=runtime.last_cycle, modelReady=bool(model.ready),
                    modelConnection=get(db, 'ai-connection'),
                    markets=[m.public() for m in paper.feed.markets.values()],
                    config=dict(paper.settings.public(), initialBalance='10000', maxOrderNotional='200'))

    @router.get('/markets')
    async def public_markets():return markets()

    @router.get('/health')
    async def health():
        return dict(mode='PAPER', liveEnabled=False, engineReady=0 < time.time()-runtime.last_cycle < 3,
                    marketData=bool(paper.feed.markets) and all(m.fresh() for m in paper.feed.markets.values()),
                    error=runtime.error)

    @router.post('/session')
    async def session(x_vela_paper: str | None = Header(default=None)):
        owner, binding = identities(x_vela_paper)
        with transaction(db):
            existing = db.execute('SELECT 1 FROM users WHERE address=?', (owner,)).fetchone()
            if not existing:
                if db.execute("SELECT COUNT(*) FROM users WHERE address LIKE 'paper:%'").fetchone()[0] >= 500:
                    raise HTTPException(429, 'Paper demonstration account capacity reached')
                account(db, owner)
                TradingCapital(db, paper).allocate(owner, '10000', True)
                paper.configure(owner, dict(confirm=True, allocation='10000', positionSize='200',
                    maxLoss=str(min(Decimal(2), paper.settings.loss_budget)), leverage='3'))
                put(db, 'paper-prefs:'+owner, dict(autonomous=False, symbol='BTC/USD', leverage='2', generation=0))
            expires = time.time()+86400
            db.execute('INSERT OR REPLACE INTO auth_sessions(hash,address,expires) VALUES (?,?,?)', (binding, owner, expires))
            put(db, 'agent-session:'+owner, binding)
            permission = get(db, 'trading-permission:'+owner)
            if permission and int(permission['expiresAt']) <= time.time():
                permission.update(expiresAt=str(int(expires)), revision=permission.get('revision', 0)+1)
                put(db, 'trading-permission:'+owner, permission)
        return paper.snapshot(owner)

    @router.get('/account')
    async def snapshot(x_vela_paper: str | None = Header(default=None)):
        return paper.snapshot(auth(x_vela_paper))

    @router.post('/orders', status_code=202)
    async def order(body: Order, x_vela_paper: str | None = Header(default=None)):
        owner = auth(x_vela_paper)
        payload = body.model_dump(exclude_none=True);key = payload.pop('clientId')
        return once(db, owner, key, dict(action='desk-order', payload=payload), lambda:paper.submit(owner, payload, key))

    @router.post('/orders/{identifier}/cancel')
    async def cancel(identifier: str, x_vela_paper: str | None = Header(default=None)):
        owner = auth(x_vela_paper)
        def perform():
            paper.cancel(owner, identifier)
            return paper.snapshot(owner)
        return once(db, owner, 'desk-cancel:'+identifier, dict(action='desk-cancel', order=identifier), perform)

    @router.post('/positions/{identifier}/close', status_code=202)
    async def close(identifier: str, body: Close, x_vela_paper: str | None = Header(default=None)):
        owner = auth(x_vela_paper)
        payload = dict(reduceOnly=True, positionId=identifier, fraction=body.fraction)
        return once(db, owner, body.clientId, dict(action='desk-close', payload=payload),
                    lambda:paper.submit(owner, payload, body.clientId))

    @router.post('/agent')
    async def agent(body: Preferences, x_vela_paper: str | None = Header(default=None)):
        owner = auth(x_vela_paper)
        with transaction(db):
            paper.pause_entries(owner)
            prefs = get(db, 'paper-prefs:'+owner)
            put(db, 'paper-prefs:'+owner, dict(prefs, symbol=body.symbol, leverage=body.leverage))
            permission = get(db, 'trading-permission:'+owner)
            permission.update(leverage=body.leverage, revision=permission.get('revision',0)+1)
            put(db, 'trading-permission:'+owner, permission)
            if body.autonomous:paper.start(owner, model)
        return paper.snapshot(owner)

    @router.get('/analysis/{symbol}')
    async def analysis(symbol: Literal['BTC', 'ETH', 'SOL']):
        full = symbol+'/USD';bars = bars_for(db, full, time.time())
        candidates = [s for s in agents_for(db, model, paper).signal_candidates(time.time()) if s['symbol'] == full]
        return dict(symbol=full, modelReady=bool(model.ready), closedCandles=len(bars),
                    features=features(bars) if bars else None, candidates=candidates,
                    status='Model review required' if model.ready else 'HOLD · model unavailable')

    @router.websocket('/stream')
    async def stream(socket: WebSocket):
        if socket.headers.get('origin') != origin:
            await socket.close(code=1008);return
        await socket.accept()
        try:
            while True:
                await socket.send_json(markets());await asyncio.sleep(1)
        except Exception:pass

    app.include_router(router)
