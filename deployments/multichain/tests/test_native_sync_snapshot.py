import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from autodefi.vaults import VaultManagement
from autodefi.lending import Lending

def test_one_snapshot_sync_returns_the_exact_recorded_vault(env):
    state=dict(vault=copy.deepcopy(env['v']),transaction=None,rewardPool={'reserveWei':'1'})
    chain=SimpleNamespace(state=Mock(return_value=state),read=Mock(side_effect=AssertionError('duplicate read')))
    result=asyncio.run(VaultManagement(env['db'],chain).sync(env['owner'],include_state=True))
    assert result is state and chain.state.call_count==1
    assert Lending(env['db']).vault(env['owner'])==state['vault']

def test_new_owner_observation_does_not_create_a_vault_record(env):
    owner='0x'+'99'*20
    state=dict(vault=None,transaction=None,rewardPool={'reserveWei':'1'})
    chain=SimpleNamespace(state=Mock(return_value=state))
    assert asyncio.run(VaultManagement(env['db'],chain).sync(owner,include_state=True))==state
    assert env['db'].execute('SELECT COUNT(*) FROM vaults WHERE owner=?',(owner,)).fetchone()[0]==0

@pytest.mark.parametrize('status',[None,'PENDING','REVERTED'])
def test_snapshot_sync_cannot_record_an_unconfirmed_transaction(env,status):
    before=Lending(env['db']).vault(env['owner'])
    state=dict(vault={**env['v'],'collateral':'999'},transaction=None if status is None else {'status':status})
    chain=SimpleNamespace(state=Mock(return_value=state))
    with pytest.raises(ValueError,match='Confirmed successful'):
        asyncio.run(VaultManagement(env['db'],chain).sync(env['owner'],'0x'+'ab'*32,True))
    assert Lending(env['db']).vault(env['owner'])==before

def test_old_vault_only_sync_response_remains_compatible(env):
    chain=SimpleNamespace(read=Mock(return_value=(env['v'],None)))
    assert asyncio.run(VaultManagement(env['db'],chain).sync(env['owner']))==env['v']
