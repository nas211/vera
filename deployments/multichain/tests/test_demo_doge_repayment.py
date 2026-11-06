import time
from decimal import Decimal as D
import pytest
from vela.db import put, get, transaction
from autodefi.store import cashflow
from autodefi import demo_doge_repayment as repayment


def profit(db, owner, gross='1', fees='.1', funding='.02'):
    # Closed-fill fixture only; production eligibility reads engine-generated rows.
    db.execute("INSERT INTO trades(id,user,session,strategy,position_id,symbol,side,qty,entry,exit,gross,fees,pnl,pnl_micro,opened,closed,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(time.time_ns()),owner,'fixture',0,'closed','BTC/USD','long','1','100','101',gross,fees,str(D(gross)-D(fees)-D(funding)),0,time.time()-1,time.time(),'fixture'))
    put(db,'paper-cost-totals:'+owner,dict(tradingFee=fees,funding=funding))


@pytest.fixture
def configured(env):
    db,owner=env['db'],env['owner']
    cashflow(db,owner,D(20))
    repayment.configure(db,owner,dict(confirm=True,enabled=True,revision=0),7*10**18,now=100)
    return db,owner,get(db,repayment.POLICY+owner)


def test_closed_profit_deducts_all_fees_and_funding(configured):
    db,owner,p=configured;profit(db,owner)
    assert repayment.eligible(db,owner,p,7*10**18,10**20,[],now=101)==880000000000000000


@pytest.mark.parametrize('change',['funding-gap','position','pending','expired','disabled','loss'])
def test_unsettled_or_unprofitable_accounts_do_not_pay(configured,change):
    db,owner,p=configured;profit(db,owner)
    if change=='funding-gap':put(db,'paper-funding-incomplete:'+owner,True)
    if change=='position':
        db.execute("INSERT INTO positions(id,user,session,symbol,side,qty,entry,stop,target,leverage,opened,expires,entry_fee_left,paid_fees,realized_gross) VALUES ('held',?,'x','BTC/USD','long','1','100','99','102','1',0,9999999999,'0','0','0')",(owner,))
    if change=='pending':db.execute("INSERT INTO paper_orders VALUES ('pending',?,'x','key',0,0,'pending','{}',NULL,NULL)",(owner,))
    if change=='expired':p['expiresAt']=100
    if change=='disabled':p['enabled']=False
    if change=='loss':profit(db,owner,gross='-2')
    assert repayment.eligible(db,owner,p,7*10**18,10**20,[],now=101)==0


def test_no_replay_and_reserve_debt_caps(configured):
    db,owner,p=configured;profit(db,owner,gross='3',fees='0',funding='0')
    job=dict(owner=owner,policyId=p['id'],status='confirmed',paidWei=str(2*10**18),valueWei=str(2*10**18))
    assert repayment.eligible(db,owner,p,7*10**18,10**20,[job],now=101)==10**18
    assert repayment.eligible(db,owner,p,123,10**20,[job],now=101)==123
    assert repayment.eligible(db,owner,p,7*10**18,555,[job],now=101)==555
    job['paidWei']=str(3*10**18)
    assert repayment.eligible(db,owner,p,7*10**18,10**20,[job],now=101)==0


def test_reenable_excludes_history_and_cannot_reset_lifetime_cap(configured):
    db,owner,p=configured;profit(db,owner,gross='20',fees='0',funding='0')
    repayment.configure(db,owner,dict(confirm=True,enabled=False,revision=1),0,now=101)
    repayment.configure(db,owner,dict(confirm=True,enabled=True,revision=2),7*10**18,now=102)
    current=get(db,repayment.POLICY+owner)
    assert repayment.eligible(db,owner,current,7*10**18,10**20,[],now=103)==0
    profit(db,owner,gross='1',fees='0',funding='0')
    history=[dict(owner=owner,policyId=p['id'],status='confirmed',paidWei=str(10*10**18),valueWei=str(10*10**18))]
    assert repayment.eligible(db,owner,current,7*10**18,10**20,history,now=103)==0


def test_stale_setting_and_missing_debt_fail(configured):
    db,owner,p=configured
    with pytest.raises(ValueError,match='changed'):repayment.configure(db,owner,dict(confirm=True,enabled=False,revision=0),0)
    with pytest.raises(ValueError,match='active DOGE loan'):repayment.configure(db,owner,dict(confirm=True,enabled=True,revision=1),0)


def test_public_receipts_never_disclose_signed_bytes(configured):
    db,owner,p=configured
    put(db,repayment.JOB+'fixture',dict(owner=owner,policyId=p['id'],status='confirmed',hash='0x'+'aa'*32,paidWei='100',block=1,raw='private-broadcast-bytes'))
    value=repayment.public_status(db,owner,now=101)
    assert value['paidWei']=='100' and 'private-broadcast-bytes' not in str(value)


def test_api_wallet_scope_explicit_consent_and_duplicate_requests(env,monkeypatch):
    import asyncio,httpx
    from dataclasses import replace
    from types import SimpleNamespace
    from eth_account.messages import encode_defunct
    from conftest import ALICE,BOB
    from autodefi.api import create_app
    from autodefi.doge_lending import DogeLendingReader
    monkeypatch.setattr(DogeLendingReader,'snapshot',lambda self,wallet:dict(debtWei=str(7*10**18)))
    monkeypatch.setattr(repayment,'settings',lambda:dict(enabled=True,reserveAddress=ALICE.address))
    app=create_app(replace(env['config'],network_enabled=True),feed=env['feed'],chain=SimpleNamespace(w3=None),model=SimpleNamespace(ready=False,model='fixture'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}) as client:
            route='/api/demo-doge-repayment'
            assert (await client.post(route,json={})).status_code==401
            async def login(wallet):
                c=(await client.post('/api/auth/nonce',json={'address':wallet.address,'chainId':6281971})).json()
                sig=wallet.sign_message(encode_defunct(text=c['message'])).signature.hex()
                assert (await client.post('/api/auth/verify',json=dict(address=wallet.address,nonce=c['nonce'],signature=sig))).status_code==200
            await login(ALICE)
            assert (await client.post(route,json={'enabled':True,'revision':0},headers={'Idempotency-Key':'missing-consent'})).status_code==400
            body=dict(confirm=True,enabled=True,revision=0)
            first=await client.post(route,json=body,headers={'Idempotency-Key':'enable-native-demo'})
            assert first.status_code==200,first.text
            again=await client.post(route,json=body,headers={'Idempotency-Key':'enable-native-demo'})
            assert again.json()==first.json() and first.json()['revision']==1
            await login(BOB)
            r=await client.post(route,json=dict(confirm=True,enabled=False,revision=0),headers={'Idempotency-Key':'bob-disable-native'})
            assert r.status_code==200 and not r.json()['enabled']
            assert get(app.state.db,repayment.POLICY+ALICE.address.lower())['enabled']
    try:asyncio.run(run())
    finally:app.state.db.close()
