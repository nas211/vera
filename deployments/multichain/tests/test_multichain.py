"""Real crypto and shared services, synthetic wallets/markets; no chain claims."""
import asyncio,json,time,uuid
from decimal import Decimal as D
import httpx,pytest
from Crypto.Signature import eddsa
from eth_account.messages import encode_defunct
from autodefi.adapters import CHAINS,BASE58,adapter,subject,split_owner,pool_key
from autodefi.multichain_auth import MultichainAuth
from autodefi.simulated_vaults import SimulatedVaults
from autodefi.api import create_app
from autodefi.lending import Lending
from autodefi.repayment import Repayment
from autodefi.orchestration import AgentOrchestrator
from autodefi.store import once
from vela.db import transaction,get,put
from conftest import ALICE,BOB,fill
from test_api_ai import FixtureModel
from test_modules import profit_close

NETWORKS=[n for n in CHAINS if n!='dogeos']
SOL_KEY=eddsa.import_private_key(bytes.fromhex('04'*32))
def base58(raw):
    n=int.from_bytes(raw,'big');s=''
    while n:n,r=divmod(n,58);s=BASE58[r]+s
    return '1'*(len(raw)-len(raw.lstrip(b'\0')))+s
SOL=base58(SOL_KEY.public_key().export_key(format='raw'))

def identity(network,key=ALICE):return SOL if network=='solana' else key.address
def sign(network,message,key=ALICE):
    return eddsa.new(SOL_KEY,'rfc8032').sign(message.encode()).hex() if network=='solana' else key.sign_message(encode_defunct(text=message)).signature.hex()
def login(e,network,key=ALICE):
    auth=MultichainAuth(e['db'],e['config']);wallet=identity(network,key)
    c=auth.challenge(wallet,adapter(network).chain_id,network)
    token,owner=auth.verify(c['nonce'],sign(network,c['message'],key),wallet,network)
    return auth,token,owner
def operation(e,owner,action,**body):
    service=SimulatedVaults(e['db'],e['paper'])
    with transaction(e['db']):
        row=e['db'].execute('SELECT address FROM vaults WHERE owner=?',(owner,)).fetchone()
        return service.mutate(owner,action,dict(confirm=True,vaultId=row[0] if row else None,**body))
def financed(e,network='base'):
    _,_,owner=login(e,network)
    operation(e,owner,'create')
    operation(e,owner,'deposit',amount='10')
    operation(e,owner,'borrow',amount='400')
    operation(e,owner,'authorize',allocation='400',positionSize='200')
    with transaction(e['db']):put(e['db'],'paper-prefs:'+owner,dict(autonomous=True,generation=1))
    e['owner']=owner;e['v']=Lending(e['db']).vault(owner)
    return owner

@pytest.mark.parametrize('network',NETWORKS)
def test_borrow_pauses_previous_run_with_demo_reason_and_can_restart(env,network):
    _,_,owner=login(env,network)
    paper,db=env['paper'],env['db']
    operation(env,owner,'create');operation(env,owner,'deposit',amount='10')
    with transaction(db):
        paper.configure(owner,dict(confirm=True,allocation='400',positionSize='100',maxLoss='0.5'))
        put(db,'paper-prefs:'+owner,dict(autonomous=True,generation=1))
        paper.authorize_run(owner)
    operation(env,owner,'borrow',amount='400')
    snap=paper.snapshot(owner)
    assert snap['preferences']['autonomous'] is False and not paper.run_authorized(owner)
    assert 'demo loan' in snap['entryStop']['reason']
    assert 'Start AI Trading' in snap['entryStop']['action']
    # The explicit Start renews saved paper settings; no separate native/demo
    # vault authorization is required, and borrowing did not restart execution.
    assert paper.gate(owner,require_running=False,starting=True)['budget']==D('0.5')
    assert not paper.run_authorized(owner)
    with transaction(db):
        paper.start(owner,FixtureModel());paper.authorize_run(owner)
    assert paper.run_authorized(owner)
    assert paper.snapshot(owner)['entryStop'] is None
    assert D(paper.permission(owner)['maxLoss'])==500000

@pytest.mark.parametrize('network',CHAINS)
def test_adapter_explicit_real_and_simulated_capabilities(env,network):
    c=adapter(network).public(env['config']);cap=c['capabilities']
    assert cap['liveTrading'] is False and cap['realLending'] is False
    assert cap['crossChainSettlement'] is False and cap['genuineWalletVerified'] is False
    assert cap['simulatedVault']==(network!='dogeos')
    if network=='robinhood':assert c['chainId']==46630 and c['testnet']
    if network=='solana':assert c['family']=='solana' and c['network']['nativeCurrency']['decimals']==9

@pytest.mark.parametrize('network',NETWORKS)
def test_crypto_network_wallet_replay_and_logout(env,network):
    auth,token,owner=login(env,network)
    assert auth.session(token,identity(network),network)==owner
    wrong='base' if network!='base' else 'arbitrum'
    with pytest.raises(PermissionError):auth.session(token,network=wrong)
    with pytest.raises((ValueError,PermissionError)):auth.session(token,BOB.address,network)
    wallet=identity(network);c=auth.challenge(wallet,adapter(network).chain_id,network)
    with pytest.raises(ValueError):auth.verify(c['nonce'],'00'*64,wallet,network)
    with pytest.raises(ValueError):auth.verify(c['nonce'],sign(network,c['message']),wallet,wrong)
    auth.verify(c['nonce'],sign(network,c['message']),wallet,network)
    with pytest.raises(ValueError):auth.verify(c['nonce'],sign(network,c['message']),wallet,network)
    with pytest.raises(ValueError):auth.challenge(wallet,999,network)
    auth.logout(token)
    with pytest.raises(PermissionError):auth.session(token,network=network)

@pytest.mark.parametrize('network',NETWORKS)
def test_persistent_vault_borrow_partial_full_repay_and_unlock(env,network):
    owner=financed(env,network);db=env['db'];lending=Lending(db);repay=Repayment(db,env['paper'])
    v=lending.vault(owner);assert v['address'].startswith('sim:') and v['simulated']
    assert D(lending.health(owner)['collateral'])==10 and D(lending.health(owner)['debt'])==400
    with pytest.raises(ValueError):operation(env,owner,'withdraw',amount='1')
    before=lending.health(owner)
    with transaction(db):receipt=repay.pay(owner,'100')
    assert D(receipt['health']['healthFactor'])>D(before['healthFactor'])
    with transaction(db):receipt=repay.pay(owner,'300',full=True)
    assert receipt['loan']['state']=='REPAID' and D(receipt['health']['debt'])==0
    operation(env,owner,'withdraw',amount='10')
    assert D(Lending(db).health(owner)['collateral'])==0
    assert not db.execute('SELECT 1 FROM chain_receipts WHERE owner=?',(owner,)).fetchone()
    assert env['paper'].snapshot(owner)['pnl']=='0.000000'

@pytest.mark.parametrize('network',NETWORKS)
@pytest.mark.parametrize('side',['long','short'])
def test_shared_fee_inclusive_stop_partial_exits_and_reconciliation(env,network,side):
    financed(env,network);p=fill(env,side);paper=env['paper'];db=env['db']
    def loss(p):
        sign=D(1) if side=='long' else D(-1)
        return (D(p['entry'])-D(p['stop']))*D(p['qty'])*sign+D(p['paid_fees'])+D(p['stop'])*D(p['qty'])*D('.0004')-D(p['realized_gross'])
    assert loss(p)<=2
    with transaction(db):paper.close(p['id'],'partial_fixture',D('.5'))
    p=paper.getPosition(env['owner'])[0];assert loss(p)<=2
    db.execute('UPDATE positions SET stop=? WHERE id=?',(str(D(p['entry'])*(D('.9') if side=='long' else D('1.1'))),p['id']))
    paper.tick();p=paper.getPosition(env['owner'])[0];assert loss(p)<=2
    with pytest.raises(ValueError):operation(env,env['owner'],'authorize',allocation='400',maxLoss='3')

@pytest.mark.parametrize('network',NETWORKS)
def test_profit_sweep_schedule_agent_settlement_and_revocation(env,network,monkeypatch):
    owner=financed(env,network);db=env['db'];repay=Repayment(db,env['paper']);lending=Lending(db)
    with transaction(db):policy=repay.configure(owner,dict(confirm=True,sweepPercent='50',frequency='daily',maximum='10',minimum='0'))
    profit_close(env);repay.tick();paid=D(lending.loan(owner)['paid']);assert paid>0
    repay.tick();assert D(lending.loan(owner)['paid'])==paid
    monkeypatch.setattr(time,'time',lambda:policy['nextDue'])
    repay.tick();assert D(lending.loan(owner)['paid'])==paid+10
    repay.tick();assert D(lending.loan(owner)['paid'])==paid+10
    with transaction(db):AgentOrchestrator(db,env['paper'],repay).settle(owner,True)
    env['paper'].settle_tick()
    assert lending.loan(owner)['state']=='REPAID'
    assert not get(db,'repayment-policy:'+owner)['enabled']
    operation(env,owner,'revoke')
    with pytest.raises(ValueError):env['paper'].gate(owner,require_running=False)

def test_cross_network_collateral_cash_debt_and_operations_isolation(env):
    alice=financed(env,'base');base=Lending(env['db']).health(alice)
    _,_,arb=login(env,'arbitrum');_,_,bob=login(env,'base',BOB)
    operation(env,arb,'create');operation(env,arb,'deposit',amount='3')
    assert env['paper'].snapshot(arb)['balance']=='0' and env['paper'].snapshot(bob)['balance']=='0'
    assert Lending(env['db']).loan(arb) is None and Lending(env['db']).loan(bob) is None
    assert get(env['db'],pool_key(arb))['cash']!=get(env['db'],pool_key(alice))['cash']
    assert Lending(env['db']).health(alice)==base
    with pytest.raises(ValueError):
        with transaction(env['db']):SimulatedVaults(env['db'],env['paper']).mutate(arb,'withdraw',dict(confirm=True,vaultId=env['v']['address'],amount='1'))

def test_stale_quote_health_deterioration_losses_and_revocation(env):
    owner=financed(env,'solana');db=env['db'];lending=Lending(db)
    q=lending.quote(owner);put(db,'quote:'+owner,dict(q,at=0))
    with pytest.raises(ValueError):env['paper'].gate(owner)
    with transaction(db):lending.quote(owner,'.50')
    # $500 collateral value / $400 debt gives reduced, unblocked health.
    put(db,'quote:'+owner,dict(lending.quote(owner),price='50'))
    assert env['paper'].gate(owner)['position']==100
    # A deeper explicitly recorded illustrative price deterioration blocks entries.
    put(db,'quote:'+owner,dict(lending.quote(owner),price='40'))
    env['paper'].risk_governor();assert not get(db,'paper-prefs:'+owner)['autonomous']
    db.execute('UPDATE users SET balance=? WHERE address=?',('50',owner))
    with transaction(db):env['paper'].settle(owner,True)
    env['paper'].settle_tick()
    assert lending.loan(owner)['state']=='SHORTFALL' and D(lending.debt(lending.loan(owner))[0])==350
    assert lending.vault(owner)['withdrawable']=='0'
    operation(env,owner,'revoke');assert not lending.vault(owner)['trading']

@pytest.mark.parametrize('network',NETWORKS)
def test_permission_before_vault_authorization_and_stricter_limit(env,network):
    _,_,owner=login(env,network)
    operation(env,owner,'create');operation(env,owner,'deposit',amount='10')
    operation(env,owner,'borrow',amount='400')
    with transaction(env['db']):
        env['paper'].configure(owner,dict(confirm=True,allocation='400',positionSize='100',maxLoss='1'))
    assert env['paper'].permission(owner)['allocation']=='400000000'
    assert not env['paper'].run_authorized(owner)
    assert env['paper'].gate(owner,require_running=False)['budget']==1
    operation(env,owner,'authorize',allocation='400',maxLoss='2')
    assert D(env['paper'].permission(owner)['maxLoss'])==1000000
    assert env['paper'].gate(owner,require_running=False)['budget']==1

def test_simulated_liquidation_records_default_without_chain_receipt(env):
    owner=financed(env,'base');db=env['db'];lending=Lending(db)
    db.execute('UPDATE users SET balance=? WHERE address=?',('0',owner))
    with transaction(db):
        env['paper'].settle(owner,True)
    env['paper'].settle_tick()
    put(db,'quote:'+owner,dict(lending.quote(owner),price='1'))
    with transaction(db):loan=lending.liquidate(owner,True)
    assert loan['state']=='DEFAULTED' and D(loan['bad_debt'])>390
    assert D(lending.health(owner)['debt'])>390
    assert lending.vault(owner)['collateral']==lending.vault(owner)['withdrawable']=='0'
    with pytest.raises(ValueError):operation(env,owner,'borrow',amount='1')
    assert not db.execute('SELECT 1 FROM chain_receipts WHERE owner=?',(owner,)).fetchone()

def test_api_scope_idempotency_and_forbidden_real_paths(env):
    env['config'].multichain=True
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as c:
                headers={'Origin':env['config'].origin,'X-ADP-Client':'web','X-Vella-Network':'base'}
                async def post(path,body,key='fixture-operation'):
                    return await c.post(path,headers={**headers,'Idempotency-Key':key},json=body)
                challenge=(await post('/api/auth/nonce',dict(address=ALICE.address,chainId=8453))).json()
                assert (await post('/api/auth/verify',dict(address=ALICE.address,nonce=challenge['nonce'],signature=sign('base',challenge['message'])))).status_code==200
                assert (await c.get('/api/account',headers={**headers,'X-Vella-Network':'arbitrum'})).status_code==403
                assert (await c.get('/api/account',headers={**headers,'X-Wallet-Address':BOB.address})).status_code==403
                created=await post('/api/simulated-vault/create',dict(confirm=True,asset='WETH'),'create-vault')
                assert created.status_code==200,created.text
                assert (await post('/api/simulated-vault/create',dict(confirm=True,asset='WETH'),'create-vault')).json()==created.json()
                v=created.json()['vault'];body=dict(confirm=True,amount='10',vaultId=v['address'])
                one=await post('/api/simulated-vault/deposit',body,'deposit-once');two=await post('/api/simulated-vault/deposit',body,'deposit-once')
                assert one.status_code==200 and one.json()==two.json()
                borrowed=await post('/api/simulated-vault/borrow',dict(confirm=True,amount='400',vaultId=v['address']),'borrow-once')
                assert borrowed.status_code==200,borrowed.text
                assert (await post('/api/simulated-vault/borrow',dict(confirm=True,amount='400',vaultId=v['address']),'borrow-once')).json()==borrowed.json()
                loan=borrowed.json()['vault']['loanId'];repay=dict(confirm=True,amount='100',vaultId=v['address'],loanId=loan)
                one=await post('/api/repay',repay,'repay-once');two=await post('/api/repay',repay,'repay-once')
                assert one.status_code==200 and one.json()==two.json()
                assert (await post('/api/repay',dict(repay,loanId='other-loan'),'wrong-loan')).status_code==400
                assert (await post('/api/repay',dict(repay,amount='101'),'repay-once')).status_code==400
                for path in ('/api/borrow','/api/certificate','/api/vault/sync'):
                    assert (await post(path,{})).status_code==400
                for path in ('/api/live/order','/api/deposit/real'):
                    assert (await post(path,{})).status_code==404
                account=(await c.get('/api/account',headers=headers)).json()
                assert account['health']['debt']=='300.000000' and account['health']['collateral']=='10'
        finally:beat.cancel()
    asyncio.run(run());app.state.db.close()
