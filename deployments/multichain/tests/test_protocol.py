import time,json,threading
from decimal import Decimal as D
import pytest
from eth_account.messages import encode_defunct
from eth_account import Account
from autodefi.auth import Auth
from autodefi.chain import certificate
from autodefi.store import once,open_db,cashflow
from autodefi.lending import Lending
from autodefi.config import Config
from vela.db import transaction,get,put
from vela.lighter.engine import LighterLiveAdapter
from conftest import ALICE,BOB,ATTESTOR,funded,fill,order,save_vault

def test_zero_account_and_demo_credit_idempotency(env):
    e=env;db=e['db'];owner=e['owner']
    assert e['paper'].snapshot(owner)['balance']=='0'
    a=once(db,owner,'borrow-one',{'action':'borrow'},lambda:e['lending'].borrow(owner))
    b=once(db,owner,'borrow-one',{'action':'borrow'},lambda:e['lending'].borrow(owner))
    c=once(db,owner,'borrow-two',{'action':'borrow'},lambda:e['lending'].borrow(owner))
    assert a==b==c and e['paper'].snapshot(owner)['balance']=='800'
    assert get(db,'pool')['cash']=='999200'
    with pytest.raises(ValueError,match='conflict'):once(db,owner,'borrow-one',{'action':'other'},lambda:None)

def test_ltv_and_health_scenarios(env):
    funded(env);h=env['lending'].health(env['owner'])
    assert D(h['collateralValue'])==2000
    assert D(h['healthFactor'])==pytest.approx(D('1.625'),abs=D('.000001'))
    assert h['withdrawableDoge']=='0'
    with transaction(env['db']):env['lending'].quote(env['owner'],'.50')
    assert D(env['lending'].health(env['owner'])['healthFactor'])<1
    with pytest.raises(ValueError,match='health'):env['paper'].gate(env['owner'])

def test_excess_ltv_rolls_back(env):
    env['v']['principalMicro']='900000000';save_vault(env['db'],env['v'])
    with pytest.raises(ValueError,match='LTV'):
        once(env['db'],env['owner'],'too-much',{},lambda:env['lending'].borrow(env['owner']))
    assert env['lending'].loan(env['owner']) is None and env['paper'].snapshot(env['owner'])['balance']=='0'

def test_interest_frequency_does_not_change_debt(env):
    funded(env);db=env['db'];owner=env['owner'];l=env['lending'].loan(owner);start=l['last']
    loan=Lending(db,clock=lambda:start+31536000)
    assert loan.debt(l)[0]==D(840)
    for t in range(1,11):
        loan.clock=lambda t=t:start+3153600*t
        with transaction(db):loan.accrue(loan.loan(owner))
    assert loan.debt(loan.loan(owner))[0]==D(840)

@pytest.mark.parametrize('side',['long','short'])
def test_fee_inclusive_stop_partial_exit_and_reconciliation(env,side):
    funded(env);p=fill(env,side);db=env['db'];paper=env['paper']
    def budget(p):
        sign=D(1) if side=='long' else D(-1)
        return (D(p['entry'])-D(p['stop']))*D(p['qty'])*sign+D(p['paid_fees'])+D(p['stop'])*D(p['qty'])*D('.0004')-D(p['realized_gross'])
    assert budget(p)<=2
    with transaction(db):paper.close(p['id'],'partial_fixture',D('.5'))
    p=paper.getPosition(env['owner'])[0];assert budget(p)<=2
    # Corrupt a historic stop outward and verify existing-position risk reconciliation.
    db.execute('UPDATE positions SET stop=? WHERE id=?',(str(D(p['entry'])*(D('.9') if side=='long' else D('1.1'))),p['id']))
    paper.tick();p=paper.getPosition(env['owner'])[0];assert budget(p)<=2

def test_stricter_permission_and_forbidden_market(env):
    funded(env);env['v']['limits']['maxLoss']='500000';save_vault(env['db'],env['v'])
    with pytest.raises(ValueError,match='budget'):env['paper'].submit(env['owner'],order(),'limit-test')
    env['v']['limits']['markets']='2';save_vault(env['db'],env['v'])
    with pytest.raises(ValueError,match='Market'):env['paper'].submit(env['owner'],order(),'limit-test-2')

def test_stop_cancels_entries_not_positions(env):
    funded(env);p=fill(env)
    with transaction(env['db']):env['paper'].pause_entries(env['owner'])
    assert env['paper'].getPosition(env['owner'])[0]['id']==p['id']
    with pytest.raises(ValueError,match='stopped'):env['paper'].submit(env['owner'],order(),'after-stop')

def test_revocation_between_submit_and_fill(env):
    funded(env)
    with transaction(env['db']):r=env['paper'].submit(env['owner'],order(),'pending-revoke')
    env['v']['agent']='0x'+'0'*40;save_vault(env['db'],env['v'])
    env['db'].execute('UPDATE paper_orders SET due=0');env['paper'].tick()
    assert not env['paper'].getPosition(env['owner'])
    assert env['db'].execute('SELECT status FROM paper_orders WHERE id=?',(r['id'],)).fetchone()[0]=='rejected'

def test_profit_settlement_preserves_pnl_and_requires_chain(env):
    funded(env);p=fill(env);m=env['feed'].markets['BTC/USD']
    m.mark=D(60300);m.bids={D(60299):D(10000)};m.asks={D(60301):D(10000)}
    with transaction(env['db']):env['paper'].settle(env['owner'],True)
    env['db'].execute('UPDATE paper_orders SET due=0');env['paper'].tick();env['paper'].settle_tick()
    l=env['lending'].loan(env['owner']);assert l['state']=='AWAITING_CHAIN'
    snap=env['paper'].snapshot(env['owner']);assert D(snap['balance'])==0 and D(snap['pnl'])>0
    assert D(get(env['db'],'demo-surplus:'+env['owner']))>0
    assert env['lending'].health(env['owner'])['withdrawableDoge']=='0'
    with pytest.raises(ValueError):env['paper'].settle(env['owner'],True)

def test_loss_shortfall_and_simulated_liquidation(env):
    funded(env);fill(env)
    with transaction(env['db']):env['paper'].settle(env['owner'],True)
    env['db'].execute('UPDATE paper_orders SET due=0');env['paper'].tick();env['paper'].settle_tick()
    assert env['lending'].loan(env['owner'])['state']=='SHORTFALL'
    with transaction(env['db']):env['lending'].liquidate(env['owner'],True)
    l=env['lending'].loan(env['owner']);assert l['state']=='AWAITING_CHAIN'
    assert 0<D(l['seized'])<D(env['v']['collateral'])
    assert D(get(env['db'],'pool')['buyerCash'])<1000000

def test_combined_losses_default_not_erased(env):
    funded(env);db=env['db']
    db.execute("UPDATE users SET balance='0' WHERE address=?",(env['owner'],))
    with transaction(db):
        env['lending'].quote(env['owner'],'.50')
        # Three years' debt plus depleted collateral: explicit simulated insolvency fixture.
        db.execute('UPDATE loans SET interest=\'500\'')
        env['lending'].repay_available(env['owner'])
        env['lending'].liquidate(env['owner'],True)
    l=env['lending'].loan(env['owner']);assert l['state']=='DEFAULT_PENDING' and D(l['bad_debt'])>0
    assert D(l['principal'])>0 and D(l['seized'])==D(env['v']['collateral'])

def test_stale_market_settlement_waits(env):
    funded(env);fill(env);env['feed'].markets['BTC/USD'].book_at=0
    with transaction(env['db']):env['paper'].settle(env['owner'],True)
    env['paper'].settle_tick()
    assert env['lending'].loan(env['owner'])['state']=='CLOSING' and env['paper'].getPosition(env['owner'])

def test_stale_quote_and_vault_fail_closed(env):
    funded(env)
    env['db'].execute('UPDATE vaults SET checked=0')
    with pytest.raises(ValueError,match='Refresh'):env['paper'].gate(env['owner'])
    save_vault(env['db'],env['v']);q=env['lending'].quote(env['owner']);q['at']=0;put(env['db'],'quote:'+env['owner'],q)
    with pytest.raises(ValueError,match='quote'):env['paper'].gate(env['owner'])

def test_auth_crypto_replay_expiry_mismatch(env):
    a=Auth(env['db'],env['config']);c=a.challenge(ALICE.address,6281971)
    sig=ALICE.sign_message(encode_defunct(text=c['message'])).signature.hex()
    token,owner=a.verify(c['nonce'],sig,ALICE.address)
    assert a.session(token)==env['owner']
    with pytest.raises(PermissionError):a.session(token,BOB.address)
    with pytest.raises(ValueError):a.verify(c['nonce'],sig,ALICE.address)
    c=a.challenge(ALICE.address,6281971)
    with pytest.raises(ValueError):a.verify(c['nonce'],BOB.sign_message(encode_defunct(text=c['message'])).signature.hex(),ALICE.address)
    env['db'].execute('UPDATE auth_nonces SET expires=0')
    with pytest.raises(ValueError):a.verify(c['nonce'],sig,ALICE.address)
    a.logout(token)
    with pytest.raises(PermissionError):a.session(token)

def test_nonce_deadline_is_exclusive_and_production_message_is_origin_bound(env):
    from dataclasses import replace
    from datetime import datetime
    a=Auth(env['db'],replace(env['config'],origin='https://defivela.xyz'))
    c=a.challenge(ALICE.address,6281971)
    lines=c['message'].splitlines()
    assert lines[0]=='defivela.xyz wants you to sign in with your Ethereum account:'
    assert lines[1]==ALICE.address
    assert 'URI: https://defivela.xyz' in lines and 'Chain ID: 6281971' in lines
    issued=datetime.fromisoformat(next(x.removeprefix('Issued At: ') for x in lines if x.startswith('Issued At: ')))
    expiry=datetime.fromisoformat(next(x.removeprefix('Expiration Time: ') for x in lines if x.startswith('Expiration Time: ')))
    assert (expiry-issued).total_seconds()==300
    sig=ALICE.sign_message(encode_defunct(text=c['message'])).signature.hex()
    deadline=env['db'].execute('SELECT expires FROM auth_nonces WHERE id=?',(c['nonce'],)).fetchone()[0]
    a.clock=lambda:deadline
    with pytest.raises(ValueError,match='expired'):a.verify(c['nonce'],sig,ALICE.address)

def test_concurrent_duplicate_credit(env):
    errors=[]
    def attempt():
        db=open_db(env['config'].db_path)
        try:once(db,env['owner'],'same-credit',{},lambda:Lending(db).borrow(env['owner']))
        except Exception as e:errors.append(e)
        finally:db.close()
    threads=[threading.Thread(target=attempt) for _ in range(4)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert not errors and env['paper'].snapshot(env['owner'])['balance']=='800'

def test_live_and_prefunding_disabled(env):
    with pytest.raises(NotImplementedError):LighterLiveAdapter()
    with pytest.raises(ValueError):env['paper'].account('1'*64)
    with pytest.raises(ValueError):Config(db_path='/root/Vera/production.sqlite')

def test_cancelled_request_cannot_be_credited(env):
    with transaction(env['db']):env['lending'].cancel_unissued(env['owner'])
    with pytest.raises(ValueError,match='cancelled'):
        once(env['db'],env['owner'],'cancelled-borrow',{},lambda:env['lending'].borrow(env['owner']))
    assert env['paper'].snapshot(env['owner'])['balance']=='0'

def test_issued_request_cannot_be_cancelled(env):
    with transaction(env['db']):env['lending'].borrow(env['owner'])
    with pytest.raises(ValueError,match='Credited'):
        with transaction(env['db']):env['lending'].cancel_unissued(env['owner'])

def test_two_wallet_ledgers_are_isolated(env):
    from autodefi.store import account
    from conftest import fixture_vault
    funded(env);owner=BOB.address.lower();v=fixture_vault(owner,nonce=2,amount=400);v['address']='0x'+'cd'*20
    with transaction(env['db']):
        account(env['db'],owner);save_vault(env['db'],v);env['lending'].quote(owner,'0');env['lending'].borrow(owner)
    assert env['paper'].snapshot(owner)['balance']=='400'
    assert env['paper'].snapshot(env['owner'])['balance']=='800'
    assert env['lending'].loan(owner)['original']=='400'
    with transaction(env['db']):env['lending'].repay_available(owner)
    assert env['paper'].snapshot(env['owner'])['balance']=='800'

def test_negative_execution_equity_is_not_erased(env):
    funded(env);db=env['db'];owner=env['owner']
    # Explicit gap-loss fixture: realized losses can exceed a stop trigger budget.
    db.execute("UPDATE users SET balance='-1500' WHERE address=?",(owner,))
    with transaction(db):
        env['lending'].repay_available(owner)
        env['lending'].liquidate(owner,True)
    l=env['lending'].loan(owner)
    assert l['state']=='DEFAULT_PENDING' and D(l['bad_debt'])>=400
    assert D(l['principal'])>0 # No fabricated loan repayment.

def test_reduced_health_limits_size_and_leverage(env):
    funded(env)
    with transaction(env['db']):env['lending'].quote(env['owner'],'.25')
    caps=env['paper'].gate(env['owner']);assert caps['position']==100 and caps['leverage']==1
    with pytest.raises(ValueError,match='position or leverage'):
        env['paper'].submit(env['owner'],{**order(),'notional':'150'},'health-limit')

def test_additional_collateral_after_default_quote_can_be_reconciled(env):
    funded(env);db=env['db'];owner=env['owner']
    db.execute("UPDATE users SET balance='-1500' WHERE address=?",(owner,))
    with transaction(db):env['lending'].repay_available(owner);env['lending'].liquidate(owner,True)
    assert env['lending'].loan(owner)['state']=='DEFAULT_PENDING'
    env['v']['collateral']=str(20000*10**18);save_vault(db,env['v'])
    with transaction(db):env['lending'].liquidate(owner,True)
    l=env['lending'].loan(owner)
    assert l['state']=='AWAITING_CHAIN' and D(l['bad_debt'])==0

def test_unacknowledged_credit_cannot_enter_settlement(env):
    with transaction(env['db']):env['lending'].borrow(env['owner'])
    with pytest.raises(ValueError,match='Confirm the issued loan'):
        with transaction(env['db']):env['paper'].settle(env['owner'],True)
    assert env['lending'].loan(env['owner'])['state']=='ACTIVE'
    assert env['paper'].snapshot(env['owner'])['balance']=='800'
