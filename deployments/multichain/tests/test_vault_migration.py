import copy
import json
from decimal import Decimal as D
import pytest
from autodefi.chain import DogeOS
from autodefi import vault_migration as m
from vela.db import get, put, transaction
from conftest import save_vault, funded, fill

TARGET='0x'+'22'*20
FACTORY='0x'+'33'*20


def settled(e):
    v=copy.deepcopy(e['v'])
    v.update(kind='legacy',bytecodeVerified=True,collateral='0',withdrawable='0',loanState=3,
             agent=m.ZERO,permissionRevision=2,trading=False)
    v['limits']={k:False if k=='autoReduce' else '0' for k in v['limits']}
    save_vault(e['db'],v)
    observed=dict(owner=e['owner'],chainId=6281971,block=v['block'],vault=v,
                  upgrade=dict(sourceVault=v['address'],targetFactory=FACTORY,targetVault=TARGET,eligible=True))
    body=dict(confirm=True,sourceVault=v['address'],targetFactory=FACTORY,targetVault=TARGET)
    return v,observed,body


def authorize(e,o,b):
    with transaction(e['db']):return m.authorize(e['db'],e['owner'],o,b)


def destination(v):
    return dict(v,address=TARGET,kind='rewarded',factory=FACTORY,loanState=0,loanNonce=0,
                permissionRevision=0,stopBudgetMicro='2000000',migratedFrom=copy.deepcopy(v))


def test_authorized_switch_preserves_cash_and_history_and_records_once(env):
    db=env['db'];v,o,b=settled(env)
    db.execute("UPDATE users SET balance='123.456789',initial='150'")
    before=tuple(db.execute('SELECT balance,initial FROM users').fetchone())
    intent=authorize(env,o,b)
    assert authorize(env,o,b)==intent
    assert len(db.execute('SELECT * FROM loan_events').fetchall())==1
    new=destination(v)
    DogeOS.record(db,env['owner'],new)
    DogeOS.record(db,env['owner'],new)
    assert tuple(db.execute('SELECT balance,initial FROM users').fetchone())==before
    assert db.execute('SELECT address FROM vaults').fetchone()[0]==TARGET
    assert db.execute("SELECT COUNT(*) FROM loan_events WHERE kind='REWARD_VAULT_MIGRATED'").fetchone()[0]==1
    assert get(db,'account-stop-budget:'+env['owner'])=='2000000'
    with pytest.raises(ValueError,match='migration'):DogeOS.record(db,env['owner'],v)


@pytest.mark.parametrize('change',['balance','active','agent','paused','trading','debt','permission','wrongowner','wrongtarget','unconfirmed','stale','running','funding'])
def test_migration_refuses_incomplete_or_changed_source(env,change):
    v,o,b=settled(env)
    if change=='balance':v['collateral']='1'
    if change=='active':v['loanState']=2
    if change=='agent':v['agent']=TARGET
    if change=='paused':v['paused']=True
    if change=='trading':v['trading']=True
    if change=='debt':v['badDebtMicro']='1'
    if change=='permission':v['limits']['maxLoss']='1'
    if change=='wrongowner':o['owner']=TARGET
    if change=='wrongtarget':b['targetVault']=FACTORY
    if change=='unconfirmed':b['confirm']=False
    if change=='stale':o['block']-=1
    if change=='running':put(env['db'],'paper-prefs:'+env['owner'],dict(autonomous=True))
    if change=='funding':put(env['db'],'paper-funding-incomplete:'+env['owner'],True)
    with pytest.raises(ValueError):authorize(env,o,b)
    assert not m.context(env['db'],env['owner'])


def test_unpaid_loan_is_not_removed_by_empty_chain_observation(env):
    funded(env)
    v,o,b=settled(env)
    put(env['db'],'paper-prefs:'+env['owner'],dict(autonomous=False))
    with pytest.raises(ValueError,match='Settle every loan'):authorize(env,o,b)
    assert env['lending'].loan(env['owner'])['principal']=='800'


@pytest.mark.parametrize('problem',['no_intent','wrong_target','old_deposit','revision','source_owner','block'])
def test_dual_registry_cannot_overwrite_ledger_without_proof(env,problem):
    v,o,b=settled(env)
    if problem!='no_intent':authorize(env,o,b)
    new=destination(v)
    if problem=='wrong_target':new['address']=FACTORY
    if problem=='old_deposit':new['migratedFrom']['collateral']='1'
    if problem=='revision':new['migratedFrom']['permissionRevision']+=1
    if problem=='source_owner':new['migratedFrom']['owner']=TARGET
    if problem=='block':new['migratedFrom']['block']-=1
    with pytest.raises(ValueError,match='migration'):DogeOS.record(env['db'],env['owner'],new)
    assert env['lending'].vault(env['owner'])['address']==v['address']


@pytest.mark.parametrize('cap',['0','250000','2000000'])
def test_migration_cap_survives_review_configuration_and_gate(env,cap):
    v,o,b=settled(env)
    put(env['db'],'account-stop-budget:'+env['owner'],cap)
    authorize(env,o,b);DogeOS.record(env['db'],env['owner'],destination(v))
    p=env['paper'];owner=env['owner']
    env['db'].execute("UPDATE users SET balance='500',initial='500'")
    configured=p.configure(owner,dict(confirm=True,allocation='100',positionSize='100',maxLoss='2',leverage='1'))
    assert D(configured['maxLoss'])==D(cap)
    assert D(p.authorization_budget(owner))==D(cap)
    assert D(p.permission(owner)['maxLoss'])==D(cap)
    assert p.gate(owner,require_running=False)['budget']==D(cap)/1000000


def test_unknown_legacy_revoked_limits_remain_zero_not_reset_to_two_dollars(env):
    v,o,b=settled(env);v['permissionRevision']=5;save_vault(env['db'],v)
    assert authorize(env,o,b)['stopBudgetMicro']=='0'


@pytest.mark.parametrize('side',['long','short'])
def test_persistent_cap_tightens_existing_position(env,side):
    funded(env);p=fill(env,side);paper=env['paper'];owner=env['owner']
    put(env['db'],'account-stop-budget:'+owner,'250000')
    paper.protect(p)
    assert D(paper.meta(p)['riskBudget'])==D('.25')
    # Existing position protection must retain the cap even if active permission disappears.
    env['db'].execute('DELETE FROM loans');env['db'].execute('DELETE FROM vaults')
    paper.protect(p)
    assert D(paper.meta(p)['riskBudget'])==D('.25')


def test_authenticated_api_migration_survives_reload_without_reissuing_credit(env):
    import asyncio
    import httpx
    from eth_account.messages import encode_defunct
    from autodefi.api import create_app
    from conftest import ALICE
    from test_api_ai import FixtureModel
    from types import SimpleNamespace
    v,o,b=settled(env);o['transaction']=None
    observations=[]
    def state(owner,tx=None,**kw):
        observations.append(kw)
        return copy.deepcopy(o)
    env['config'].network_enabled=True;env['config'].native_actions_enabled=True
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel(),chain=SimpleNamespace(state=state))
    async def run():
        headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin,headers=headers) as client:
            assert (await client.post('/api/vault/migration/authorize',json=b)).status_code==401
            challenge=(await client.post('/api/auth/nonce',json=dict(address=ALICE.address,chainId=6281971))).json()
            signature=ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
            assert (await client.post('/api/auth/verify',json=dict(address=ALICE.address,nonce=challenge['nonce'],signature=signature))).status_code==200
            bad=await client.post('/api/vault/migration/authorize',json={**b,'confirm':False})
            assert bad.status_code==400
            approved=await client.post('/api/vault/migration/authorize',json=b)
            assert approved.status_code==200,approved.text
            assert (await client.post('/api/vault/migration/authorize',json=b)).json()==approved.json()
            assert observations[-1]['migration']['sourceVault']==v['address']
            o['vault']=destination(v);o['upgrade']=None
            result=await client.post('/api/vault/sync',json={'includeState':True})
            assert result.status_code==200,result.text
            assert result.json()['vault']['address']==TARGET
            account=(await client.get('/api/account')).json()
            assert account['paper']['balance']=='0'
            assert account['health']['loan'] is None
            assert account['vault']['address']==TARGET
            assert (await client.post('/api/vault/migration/authorize',json=b)).status_code==400
            env['config'].native_actions_enabled=False
            assert (await client.post('/api/vault/migration/authorize',json=b)).status_code==400
    try:asyncio.run(run())
    finally:app.state.db.close()
