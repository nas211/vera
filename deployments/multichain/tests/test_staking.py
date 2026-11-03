from decimal import Decimal as D
import pytest
from autodefi.staking import DemoStaking,POOL
from autodefi.store import once,account
from vela.db import put,get,transaction
from conftest import funded,BOB


def demo(env):
    db,owner=env['db'],env['owner']
    with transaction(db):
        account(db,owner)
        db.execute("UPDATE users SET balance='1000',initial='1000' WHERE address=?",(owner,))
        put(db,'paper-prefs:'+owner,dict(autonomous=False,generation=0))
    clock=[1000000000.0]
    staking=DemoStaking(db,env['paper'],clock=lambda:clock[0])
    def action(name,amount=None,key=None):
        body={'confirm':True,**({'amount':amount} if amount is not None else {})}
        return once(db,owner,key or 'staking-'+name,dict(action=name,body=body),lambda:staking.mutate(owner,name,body))
    return staking,clock,action


def test_staking_deposit_claim_withdraw_persistence_and_cashflow_isolation(env):
    staking,clock,action=demo(env)
    first=action('deposit','500')
    assert action('deposit','500')==first
    assert env['paper'].snapshot(env['owner'])['balance']=='500'
    clock[0]+=31536000
    assert staking.status(env['owner'])['claimable']=='25.000000'
    result=action('claim')
    assert result['amount']=='25.000000' and result['realFunding'] is False
    assert D(env['paper'].snapshot(env['owner'])['pnl'])==0
    action('withdraw','500')
    assert D(env['paper'].snapshot(env['owner'])['balance'])==1025
    restarted=DemoStaking(env['db'],env['paper'],clock=lambda:clock[0])
    assert restarted.status(env['owner'])['principal']=='0'
    assert restarted.status(env['owner'])['claimed']=='25.000000'
    assert env['db'].execute("SELECT count(*) FROM loan_events WHERE kind LIKE 'DEMO_STAKING_%'").fetchone()[0]==3


def test_rewards_are_pool_bounded_and_pause_cannot_trap_principal(env):
    staking,clock,action=demo(env)
    action('deposit','1000')
    put(env['db'],POOL,dict(available='1',allocated='0',depositsPaused=True))
    clock[0]+=31536000
    assert action('claim')['amount']=='1.000000'
    assert get(env['db'],POOL)['available']=='0'
    with pytest.raises(ValueError,match='paused'):action('deposit','1','paused-deposit')
    assert action('withdraw','1000')['staking']['principal']=='0'


def test_staking_blocks_loan_capital_and_rolls_back_failed_cashflow(env,monkeypatch):
    staking,clock,action=demo(env)
    funded(env)
    with pytest.raises(ValueError,match='debt'):action('deposit','100')
    assert get(env['db'],'demo-staking:'+env['owner']) is None
    env['db'].execute('DELETE FROM loans')
    put(env['db'],'paper-prefs:'+env['owner'],{'autonomous':False})
    def fail(*args):raise RuntimeError('fixture cashflow failure')
    monkeypatch.setattr('autodefi.staking.cashflow',fail)
    with pytest.raises(RuntimeError):action('deposit','100')
    assert get(env['db'],POOL) is None
    assert get(env['db'],'demo-staking:'+env['owner']) is None


def test_staking_accounts_cannot_withdraw_other_users_principal(env):
    staking,clock,action=demo(env);action('deposit','500')
    owner=BOB.address.lower()
    with transaction(env['db']):account(env['db'],owner)
    with pytest.raises(ValueError,match='Insufficient'):
        once(env['db'],owner,'foreign-withdraw',{},lambda:staking.mutate(owner,'withdraw',dict(confirm=True,amount='500')))
    assert staking.status(env['owner'])['principal']=='500'


def test_clock_rollback_cannot_reaccrue_the_same_reward_period(env):
    staking,clock,action=demo(env);action('deposit','500')
    clock[0]+=31536000
    action('claim')
    end=clock[0]
    clock[0]-=1000
    action('withdraw','1')
    clock[0]=end
    assert staking.status(env['owner'])['claimable']=='0.000000'
