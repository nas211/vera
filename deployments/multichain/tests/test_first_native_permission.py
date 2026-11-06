from decimal import Decimal as D
import pytest
from conftest import funded, save_vault
from vela.db import put


def unused(env):
    funded(env)
    env['v'].update(kind='legacy', loanNonce=1, permissionRevision=1, trading=False,
                    agent='0x'+'00'*20, limits=dict(allocation='0',positionSize='0',expiresAt='0',
                    maxLoss='0',leverage='0',markets='0',autoReduce=False))
    save_vault(env['db'], env['v'])


def test_first_permission_can_be_reviewed_without_enabling_execution(env):
    unused(env)
    paper, owner = env['paper'], env['owner']
    changes = env['db'].total_changes
    assert paper.authorization_budget(owner) == '2000000'
    assert paper.permission(owner)['maxLoss'] == '0'
    with pytest.raises(ValueError, match='paper trading permission'):
        paper.gate(owner)
    assert env['db'].total_changes == changes


@pytest.mark.parametrize('limit', ['0','1','250000','2000000'])
def test_first_permission_preserves_explicit_stored_budget_including_zero(env, limit):
    unused(env)
    put(env['db'], 'trading-permission:'+env['owner'], {'maxLoss':limit})
    assert env['paper'].authorization_budget(env['owner']) == limit


@pytest.mark.parametrize('change', ['revision','nonce','agent','limits','autoReduce'])
def test_only_provably_unused_legacy_permission_can_ignore_empty_limits(env, change):
    unused(env)
    v = env['v']
    if change=='revision':v['permissionRevision']=2
    elif change=='nonce':v['loanNonce']=2
    elif change=='agent':v['agent']='0x'+'12'*20
    elif change=='limits':v['limits']['allocation']='1'
    else:v['limits']['autoReduce']=True
    save_vault(env['db'], v)
    assert env['paper'].authorization_budget(env['owner']) == '0'


def test_existing_native_and_system_stricter_limits_are_kept(env):
    funded(env)
    env['v']['limits']['maxLoss']='125000'
    save_vault(env['db'],env['v'])
    assert env['paper'].authorization_budget(env['owner'])=='125000'
    env['paper'].settings.loss_budget=D('.10')
    assert env['paper'].authorization_budget(env['owner'])=='100000'


def test_rewarded_vault_uses_lifetime_budget_across_revocation(env):
    unused(env)
    env['v'].update(kind='rewarded', stopBudgetMicro='750000', permissionRevision=8)
    save_vault(env['db'], env['v'])
    assert env['paper'].authorization_budget(env['owner'])=='750000'
    put(env['db'],'trading-permission:'+env['owner'], {'maxLoss':'500000'})
    assert env['paper'].authorization_budget(env['owner'])=='500000'
    put(env['db'],'trading-permission:'+env['owner'], {'maxLoss':'0'})
    assert env['paper'].authorization_budget(env['owner'])=='0'


def test_unfunded_account_keeps_its_configured_limit(env):
    assert env['paper'].authorization_budget(env['owner'])=='2000000'
    put(env['db'],'trading-permission:'+env['owner'], {'maxLoss':'300000'})
    assert env['paper'].authorization_budget(env['owner'])=='300000'
