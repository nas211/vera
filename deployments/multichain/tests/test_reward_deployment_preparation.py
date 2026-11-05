"""Offline deployment inputs only: public roles, exact constructor bytes, no signer/RPC."""
import json
import shlex
import pytest
from eth_abi import decode
from tools import prepare_contracts as contracts

DEPLOYER='0x'+'11'*20
ATTESTOR='0x'+'22'*20
TREASURY='0x'+'33'*20
MANAGER='0x'+'44'*20
OWNER='0x'+'55'*20


def parameters(**changes):
    return dict(dict(rewarded=True,deployer=DEPLOYER,attestor=ATTESTOR,treasury=TREASURY,
                     reward_manager=MANAGER,apr_bps=500,owner=OWNER,nonce=7),**changes)


def test_unconfigured_reward_manifest_cannot_invent_deployment_or_enable_actions():
    r=contracts.prepare(rewarded=True)
    assert r==contracts.prepare(rewarded=True)
    assert set(r['contracts'])=={'RewardedVaultFactory','RewardedCollateralVault','NativeRewardPool'}
    assert r['unsignedTransaction'] is None and r['broadcast'] is False
    assert r['customerTransactionsEnabled'] is False
    assert r['backendRegistration']=={'VERA_PROTOCOL_REWARD_FACTORY':None,'VERA_PROTOCOL_REWARD_FACTORY_CODEHASH':None,
                                      'VERA_PROTOCOL_REWARD_VAULT_CODEHASH':None,'VERA_PROTOCOL_REWARD_POOL_CODEHASH':None,'VERA_PROTOCOL_NATIVE_ACTIONS_ENABLED':'0'}
    assert r['rewardTerms']['manager'] is None and r['rewardTerms']['initialAprBps'] is None
    assert all(x['deployedAddress'] is None and x['predictedAddress'] is None for x in r['contracts'].values())


def test_factory_and_vault_constructors_encode_exact_public_roles_and_rate():
    r=contracts.prepare(**parameters())
    f=r['contracts']['RewardedVaultFactory'];v=r['contracts']['RewardedCollateralVault'];tx=r['unsignedTransaction']
    assert decode(['address','address','address','uint256'],bytes.fromhex(f['constructorArguments'][2:]))==(ATTESTOR,TREASURY,MANAGER,500)
    assert decode(['address','address','address','address'],bytes.fromhex(v['constructorArguments'][2:]))==(OWNER,ATTESTOR,TREASURY,r['contracts']['NativeRewardPool']['predictedAddress'].lower())
    assert decode(['address','uint256'],bytes.fromhex(r['contracts']['NativeRewardPool']['constructorArguments'][2:]))==(MANAGER,500)
    assert tx['data'].endswith(f['constructorArguments'][2:]) and tx['value']=='0x0'
    assert tx['from'].lower()==DEPLOYER and tx['nonce']==7 and tx['chainId']==6281971
    assert 'gas' not in tx and 'gasPrice' not in tx and 'signature' not in tx
    for name,row in r['contracts'].items():
        argv=shlex.split(row['verificationCommand'])
        assert argv[2]==row['predictedAddress'] and f'src/{name}.sol:{name}' in argv
        assert '--broadcast' not in argv and row['deployedAddress'] is None
        assert argv[argv.index('--constructor-args')+1]==row['constructorArguments']


def test_wallet_preparation_without_live_nonce_never_predicts_an_address():
    r=contracts.prepare(**parameters(nonce=None))
    assert 'nonce' not in r['unsignedTransaction']
    assert all(x['predictedAddress'] is None and x['verificationCommand'] is None for x in r['contracts'].values())
    assert r['backendRegistration']['VERA_PROTOCOL_REWARD_FACTORY'] is None


def test_owner_manager_rate_and_nonce_changes_bind_to_distinct_constructor_predictions():
    original=contracts.prepare(**parameters())
    for update in [dict(owner=DEPLOYER),dict(reward_manager=OWNER),dict(apr_bps=501)]:
        other=contracts.prepare(**parameters(**update))
        assert other['contracts']['RewardedVaultFactory']['predictedAddress']==original['contracts']['RewardedVaultFactory']['predictedAddress']
        if 'owner' in update:assert other['contracts']['RewardedCollateralVault']['predictedAddress']!=original['contracts']['RewardedCollateralVault']['predictedAddress']
        else:assert other['unsignedTransaction']['data']!=original['unsignedTransaction']['data']
    other=contracts.prepare(**parameters(nonce=8))
    assert all(other['contracts'][n]['predictedAddress']!=original['contracts'][n]['predictedAddress'] for n in original['contracts'])


@pytest.mark.parametrize('changes',[dict(reward_manager=None),dict(reward_manager='0x'+'00'*20),dict(reward_manager='not-an-address'),
                                   dict(apr_bps=-1),dict(apr_bps=10001),dict(apr_bps=True),dict(apr_bps=0.5),dict(apr_bps='500'),
                                   dict(nonce=-1),dict(nonce=True),dict(nonce=1.5),dict(owner='0x'+'00'*20)])
def test_invalid_terms_or_roles_cannot_prepare_a_send(changes):
    with pytest.raises(ValueError):contracts.prepare(**parameters(**changes))


def test_reward_arguments_cannot_silently_target_legacy_contracts():
    with pytest.raises(ValueError,match='Reward settings'):contracts.prepare(**parameters(rewarded=False))
    with pytest.raises(ValueError):contracts.prepare(rewarded=True,nonce=0)


@pytest.mark.parametrize('mutation',['source','abi'])
def test_candidate_artifact_must_match_current_sources_and_checked_in_abi(tmp_path,monkeypatch,mutation):
    original_root=contracts.ROOT
    for name in ('RewardedVaultFactory','RewardedCollateralVault','NativeRewardPool'):
        relative=f'contracts/out/{name}.sol/{name}.json'
        data=json.loads((original_root/relative).read_text())
        files=[relative,f'config/{name}.abi.json']+['contracts/'+source for source in data['metadata']['sources']]
        for relative in files:
            destination=tmp_path/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes((original_root/relative).read_bytes())
    if mutation=='source':
        source=tmp_path/'contracts/src/RewardedCollateralVault.sol'
        source.write_text(source.read_text()+'\n// changed after compilation\n')
    else:(tmp_path/'config/RewardedCollateralVault.abi.json').write_text('[]')
    monkeypatch.setattr(contracts,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='current source|ABI differs'):contracts.prepare(**parameters())
