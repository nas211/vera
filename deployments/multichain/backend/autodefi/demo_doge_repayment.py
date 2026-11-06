"""Opt-in testnet rewards: net paper profit -> simulated 1:1 DOGE -> real test loan.

The dedicated project reserve pays DOGE. No USDC transfer, exchange execution,
collateral withdrawal, or lending-pool withdrawal is implied or performed.
"""
import json
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from vela.db import get, put, transaction
from .adapters import split_owner
from .config import ROOT, CHAIN_ID

POLICY = 'demo-doge-policy:'
JOB = 'demo-doge-job:'
HEALTH = 'demo-doge-relay-health'
WEI = Decimal(10**18)
MAX_PAYMENT = 10**18
MAX_WALLET = 10 * 10**18
MAX_TOTAL = 100 * 10**18
MIN_PROFIT = 10**12


def settings():
    p = ROOT / 'config/demo-doge-repayment.json'
    if not p.exists():
        return dict(enabled=False, reserveAddress=None)
    d = json.loads(p.read_text())
    from web3 import Web3
    if d.get('chainId') != CHAIN_ID or not Web3.is_address(d.get('reserveAddress')) or d.get('demoDogePerUsd') != '1':
        raise ValueError('Invalid testnet repayment configuration')
    return d


def net_profit(db, owner):
    gross = sum((Decimal(r[0]) for r in db.execute('SELECT gross FROM trades WHERE user=?', (owner,))), Decimal(0))
    costs = get(db, 'paper-cost-totals:' + owner)
    if costs is None:
        costs = {}
        for kind, amount in db.execute('SELECT kind,amount FROM paper_costs WHERE user=?', (owner,)):
            costs[kind] = str(Decimal(costs.get(kind, '0')) + Decimal(amount))
    return gross - Decimal(costs.get('tradingFee', '0')) - Decimal(costs.get('funding', '0'))


def jobs(db):
    return [json.loads(r[0]) for r in db.execute('SELECT value FROM kv WHERE key LIKE ?', (JOB + '%',))]


def charge(job):
    return int(job.get('paidWei', 0)) if job['status'] == 'confirmed' else int(job['valueWei']) if job['status'] in ('prepared', 'sent') else 0


def eligible(db, owner, policy, debt_wei, reserve_wei, history, now=None):
    """Pure amount decision. Fees/funding/losses and prior payments reduce entitlement."""
    now = time.time() if now is None else now
    if not policy.get('enabled') or now >= policy['expiresAt']:
        return 0
    if get(db, 'paper-funding-incomplete:' + owner, False):
        return 0
    # Settle only a fully closed account; pending orders and exposed margin cannot pay.
    if db.execute('SELECT 1 FROM positions WHERE user=?', (owner,)).fetchone() or db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'", (owner,)).fetchone():
        return 0
    own = [j for j in history if j['owner'] == owner]
    spent = sum(charge(j) for j in own if j['policyId'] == policy['id'])
    earned = int(((net_profit(db, owner) - Decimal(policy['baseline'])) * WEI).to_integral_value(rounding=ROUND_DOWN))
    available = max(0, earned - spent)
    if available < MIN_PROFIT:
        return 0
    cash = db.execute('SELECT balance FROM users WHERE address=?', (owner,)).fetchone()
    cash_wei = int(Decimal(cash[0])*WEI) if cash else 0
    return max(0, min(available, cash_wei, int(debt_wei), int(reserve_wei), MAX_PAYMENT,
                      MAX_WALLET - sum(charge(j) for j in own), MAX_TOTAL - sum(charge(j) for j in history)))


def public_status(db, owner, now=None):
    now = time.time() if now is None else now
    d = settings()
    h = get(db, HEALTH, {})
    p = get(db, POLICY + owner, {})
    history = [j for j in jobs(db) if j['owner'] == owner]
    current = [j for j in history if j['status'] in ('prepared', 'sent')]
    return dict(mode='TESTNET_SIMULATED_CONVERSION', demoDogePerUsd='1',
                configured=d.get('enabled', False), reserveAddress=d.get('reserveAddress'),
                ready=bool(d.get('enabled') and h.get('ready') and 0 <= now-h.get('at', 0) <= 90),
                reason=h.get('reason', 'Project repayment reserve is being connected.'),
                enabled=bool(p.get('enabled') and now < p.get('expiresAt', 0)),
                revision=p.get('revision', 0), pending=bool(current),
                paidWei=str(sum(int(j.get('paidWei', 0)) for j in history if j['status'] == 'confirmed')),
                maximumPerWalletDoge='10',
                receipts=[dict(hash=j['hash'], paidWei=j['paidWei'], block=j['block'])
                          for j in history if j['status'] == 'confirmed'][-20:])


def configure(db, owner, body, debt_wei, now=None):
    now = time.time() if now is None else now
    network, _ = split_owner(owner)
    if network != 'dogeos' or body.get('confirm') is not True or type(body.get('enabled')) is not bool:
        raise ValueError('Confirm testnet DOGE profit repayment')
    old = get(db, POLICY + owner, {})
    if body.get('revision') != old.get('revision', 0):
        raise ValueError('Repayment setting changed; refresh and retry')
    if body['enabled']:
        if int(debt_wei) <= 0:
            raise ValueError('An active DOGE loan is required')
        if any(j['owner'] == owner and j['status'] in ('prepared', 'sent') for j in jobs(db)):
            raise ValueError('A repayment is confirming; wait before enabling again')
        if old.get('enabled') and now < old.get('expiresAt', 0):
            return public_status(db, owner, now)
        old = dict(id=uuid.uuid4().hex, baseline=str(net_profit(db, owner)), enabled=True,
                   expiresAt=now+86400, revision=old.get('revision', 0)+1)
    else:
        old = dict(old, enabled=False, revision=old.get('revision', 0)+1)
    put(db, POLICY + owner, old)
    return public_status(db, owner, now)


def attach(app, db, config, chain, owner):
    import asyncio
    from fastapi import Request
    from .doge_lending import DogeLendingReader, configuration
    from .store import once

    @app.get('/api/demo-doge-repayment/status')
    async def reserve_status():
        d = settings()
        h = get(db, HEALTH, {})
        return dict(chainId=CHAIN_ID, reserveAddress=d.get('reserveAddress'),
                    manager=configuration()['manager'], demoDogePerUsd='1',
                    ready=bool(d.get('enabled') and h.get('ready') and 0 <= time.time()-h.get('at', 0) <= 90),
                    reserveWei=h.get('reserveWei', '0'), reason=h.get('reason', 'Reserve is starting.'))

    @app.post('/api/demo-doge-repayment')
    async def set_policy(req: Request):
        who = owner(req)
        body = await req.json()
        debt = 0
        if body.get('enabled') is True:
            if not settings().get('enabled') or not config.network_enabled:
                raise ValueError('Project repayment reserve is not configured')
            network, wallet = split_owner(who)
            if network != 'dogeos':
                raise ValueError('DogeOS wallet required')
            snapshot = await asyncio.to_thread(DogeLendingReader(chain.w3, configuration()).snapshot, wallet)
            debt = int(snapshot['debtWei'])
        return once(db, who, req.headers.get('idempotency-key'),
                    {'action': 'demo-doge-repayment', 'payload': body},
                    lambda: configure(db, who, body, debt))
