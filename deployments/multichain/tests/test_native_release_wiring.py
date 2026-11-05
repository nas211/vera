"""Native release wiring: explicit settings, fresh capability gates and auth forwarding."""
import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from autodefi.config import Config, ROOT
from conftest import ATTESTOR
from tools.native_testnet_config import configured
from tools.dogeos_readonly import create_app, ORIGIN, BLOCKED
from test_dogeos_readonly import FixtureReader


def native_env(monkeypatch):
    values = {'NATIVE_ACTIONS_ENABLED': '1', 'REWARD_FACTORY': '0x'+'22'*20,
              'REWARD_FACTORY_CODEHASH': '0x'+'33'*32, 'REWARD_VAULT_CODEHASH': '0x'+'44'*32,
              'REWARD_POOL_CODEHASH': '0x'+'55'*32, 'DEMO_ATTESTOR_ADDRESS': ATTESTOR.address,
              'DEMO_ATTESTOR_KEY': ATTESTOR.key.hex()}
    for name, value in values.items():
        monkeypatch.setenv('VERA_PROTOCOL_'+name, value)


def test_native_settings_opt_in_preserves_database_and_risk(monkeypatch):
    native_env(monkeypatch)
    monkeypatch.setenv('VERA_PROTOCOL_DB', '/never/import.sqlite3')
    original = Config(origin=ORIGIN, network_enabled=True)
    result = configured(original)
    legacy = json.loads((ROOT/'config/dogeos-deployment.json').read_text())['contracts']['VaultFactory']['address']
    assert result.factory == legacy and result.reward_factory != legacy
    assert result.native_actions_enabled and result.attestor_key == ATTESTOR.key.hex()
    assert result.db_path == original.db_path and result.origin == original.origin
    assert result.paper().loss_budget <= Decimal(2)
    assert original.attestor_key == '' and original.native_actions_enabled is False


def test_disabled_native_settings_never_import_ambient_keys(monkeypatch):
    monkeypatch.setenv('VERA_PROTOCOL_NATIVE_ACTIONS_ENABLED', '0')
    monkeypatch.setenv('VERA_PROTOCOL_DEMO_ATTESTOR_KEY', 'do-not-import')
    config = Config(network_enabled=True)
    result = configured(config)
    assert result.factory == json.loads((ROOT/'config/dogeos-deployment.json').read_text())['contracts']['VaultFactory']['address']
    assert not result.attestor_key and not result.native_actions_enabled and not result.reward_factory


@pytest.mark.parametrize('name,value', [
    ('NATIVE_ACTIONS_ENABLED', 'yes'), ('EXECUTION', 'LIVE'), ('LENDING', 'REAL'),
    ('FACTORY', '0x'+'77'*20), ('REWARD_FACTORY', '0x'+'00'*20),
    ('REWARD_FACTORY_CODEHASH', ''), ('REWARD_VAULT_CODEHASH', '0x'+'00'*32),
    ('REWARD_POOL_CODEHASH', 'not-a-hash'), ('DEMO_ATTESTOR_ADDRESS', '0x'+'88'*20),
    ('DEMO_ATTESTOR_KEY', 'fixture-invalid-secret'),
])
def test_native_settings_reject_incomplete_or_foreign_configuration(monkeypatch, name, value):
    native_env(monkeypatch);monkeypatch.setenv('VERA_PROTOCOL_'+name, value)
    with pytest.raises(ValueError) as exc:
        configured(Config(network_enabled=True))
    assert 'fixture-invalid-secret' not in str(exc.value)


@pytest.mark.parametrize('network,rpc', [(False, Config.rpc), (True, 'https://mainnet.invalid/')])
def test_native_settings_reject_disabled_or_wrong_reader(monkeypatch, network, rpc):
    native_env(monkeypatch)
    with pytest.raises(ValueError):configured(Config(network_enabled=network, rpc=rpc))


def ready_config():
    return dict(mode='PAPER', lending='DEMO', liveEnabled=False, realFunding=False,
                networkEnabled=True, nativeVaultActionsEnabled=True, attestorConfigured=True,
                chain=dict(id='dogeos', chainId=6281971, capabilities={}), chains=[],
                nativeRewardDeployment=dict(factory='0x'+'22'*20, factoryCodeHash='0x'+'33'*32,
                                            vaultCodeHash='0x'+'44'*32, poolCodeHash='0x'+'55'*32))


@pytest.mark.parametrize('problem', ['none','gateway_off','backend_off','mainnet','live','funding','hash','signer','unavailable'])
def test_gateway_native_mutations_require_both_gates_and_fresh_configuration(problem):
    sent=[];config=ready_config()
    if problem=='backend_off':config['nativeVaultActionsEnabled']=False
    if problem=='mainnet':config['chain']['chainId']=1
    if problem=='live':config['liveEnabled']=True
    if problem=='funding':config['realFunding']=True
    if problem=='hash':config['nativeRewardDeployment']['poolCodeHash']=None
    if problem=='signer':config['attestorConfigured']=False
    def handler(req):
        sent.append(req)
        if req.url.path=='/api/config':
            return httpx.Response(503 if problem=='unavailable' else 200,json=copy.deepcopy(config))
        # The existing backend remains the authentication and authorization owner.
        if req.headers.get('cookie')!='adp_session=fixture':return httpx.Response(401,json={'error':'Authentication required'})
        return httpx.Response(200,json={'authoritativeLedger':True})
    app=create_app(FixtureReader(),transport=httpx.MockTransport(handler),native_workflow=problem!='gateway_off')
    async def run():
        h={'Origin':ORIGIN,'X-ADP-Client':'web','X-Vella-Network':'dogeos','X-Wallet-Address':'0x'+'11'*20,'Idempotency-Key':'same-reviewed-action'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=ORIGIN,headers=h) as c:
            if problem!='unavailable':
                public=(await c.get('/api/config')).json()
                assert public['nativeVaultActionsEnabled'] is (problem=='none')
                assert public['factory'] is None  # The old writer remains unconfigured.
            sent.clear()
            for route in BLOCKED:
                response=await c.post(route,json={'confirm':True})
                if problem=='none':
                    assert response.status_code==401
                    response=await c.post(route,json={'confirm':True},headers={'cookie':'adp_session=fixture'})
                    assert response.status_code==200
                    forwarded=sent[-1]
                    assert forwarded.headers['idempotency-key']=='same-reviewed-action'
                    assert forwarded.headers['x-wallet-address']==h['X-Wallet-Address']
                    assert forwarded.headers['origin']==ORIGIN
                else:
                    assert response.status_code in (403,503)
                    assert all(r.url.path=='/api/config' for r in sent)
            assert (await c.post('/api/simulated-vault/create',json={})).status_code==403
            assert (await c.post('/api/borrow',json={},headers={'Origin':'https://foreign.invalid'})).status_code==403
            if problem=='none':
                config['nativeVaultActionsEnabled']=False
                n=len(sent)
                assert (await c.post('/api/borrow',json={},headers={'cookie':'adp_session=fixture'})).status_code==503
                assert len(sent)==n+1 and sent[-1].url.path=='/api/config'
    asyncio.run(run())
