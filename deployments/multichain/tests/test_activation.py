"""Launcher/manifest unit tests. Process, TLS and wallet boundaries are fixtures."""
import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from eth_abi import decode

from tools import prepare_contracts as contracts
from tools import activate_preview as preview


def test_contract_manifest_is_reproducible_and_never_invents_deployment():
    first=contracts.prepare()
    assert first==contracts.prepare()
    assert first['chainId']==6281971 and first['broadcast'] is False and first['approvalRequired']
    assert first['unsignedTransaction'] is None
    assert all(c['deployedAddress'] is None and c['predictedAddress'] is None for c in first['contracts'].values())
    assert first['nativeErc20']=='0x530000000000000000000000000000000000d09e'


def test_public_deployment_inputs_bind_addresses_nonce_and_verification():
    deployer='0x'+'11'*20;attestor='0x'+'22'*20;treasury='0x'+'33'*20;owner='0x'+'44'*20
    result=contracts.prepare(deployer,7,attestor,treasury,owner)
    tx=result['unsignedTransaction']
    assert tx['chainId']==6281971 and tx['nonce']==7 and tx['value']=='0x0'
    assert decode(['address','address'],bytes.fromhex(tx['data'][-128:]))==(attestor,treasury)
    changed=contracts.prepare(deployer,8,attestor,treasury,owner)
    for name in ('VaultFactory','DogeCollateralVault'):
        c=result['contracts'][name]
        assert c['predictedAddress']!=changed['contracts'][name]['predictedAddress']
        argv=shlex.split(c['verificationCommand'])
        assert argv[1]=='verify-contract' and '--broadcast' not in argv
        assert argv[2]==c['predictedAddress']
        assert argv[argv.index('--verifier')+1]=='blockscout'
        assert argv[argv.index('--verifier-url')+1]=='https://dogeos-testnet.l2scan.co/api/'
        assert c['deployedAddress'] is None
    other_owner=contracts.prepare(deployer,7,attestor,treasury,'0x'+'55'*20)
    assert other_owner['contracts']['VaultFactory']['predictedAddress']==result['contracts']['VaultFactory']['predictedAddress']
    assert other_owner['contracts']['DogeCollateralVault']['predictedAddress']!=result['contracts']['DogeCollateralVault']['predictedAddress']


@pytest.mark.parametrize('args',[
    {'deployer':'0x'+'11'*20},
    {'deployer':'0x'+'00'*20,'nonce':0,'attestor':'0x'+'22'*20,'treasury':'0x'+'33'*20},
    {'deployer':'0x'+'11'*20,'nonce':-1,'attestor':'0x'+'22'*20,'treasury':'0x'+'33'*20},
])
def test_manifest_rejects_incomplete_or_invalid_public_inputs(args):
    with pytest.raises(ValueError):contracts.prepare(**args)


def test_preview_env_is_dedicated_private_and_not_shell_evaluated(tmp_path,monkeypatch):
    p=tmp_path/'preview.env'
    p.write_text('VERA_PROTOCOL_ORIGIN="https://defivela.xyz"\nVERA_PROTOCOL_MODEL_KEY="$(must-never-execute)"\n')
    p.chmod(0o600)
    for key in ('VERA_PROTOCOL_ORIGIN','VERA_PROTOCOL_MODEL_KEY'):
        monkeypatch.setenv(key,'')
        monkeypatch.delenv(key)
    preview.load_environment(p)
    assert os.environ['VERA_PROTOCOL_ORIGIN']=='https://defivela.xyz'
    assert os.environ['VERA_PROTOCOL_MODEL_KEY']=='$(must-never-execute)'
    p.chmod(0o644)
    with pytest.raises(ValueError,match='mode 600'):preview.load_environment(p)
    p.chmod(0o600);p.write_text('VELA_EXECUTION_MODE=LIVE')
    with pytest.raises(ValueError,match='Unsupported'):preview.load_environment(p)


def test_preview_env_does_not_replace_a_different_injected_secret(tmp_path,monkeypatch):
    p=tmp_path/'preview.env';p.write_text('VERA_PROTOCOL_MODEL_KEY="fixture-file-secret"');p.chmod(0o600)
    monkeypatch.setenv('VERA_PROTOCOL_MODEL_KEY','fixture-injected-secret')
    with pytest.raises(ValueError) as error:preview.load_environment(p)
    assert 'fixture' not in str(error.value)
    assert os.environ['VERA_PROTOCOL_MODEL_KEY']=='fixture-injected-secret'


@pytest.fixture
def launcher(tmp_path,monkeypatch):
    monkeypatch.setattr(preview,'ROOT',tmp_path)
    monkeypatch.setattr(preview.time,'sleep',lambda _:None)
    sock=Mock();sock.fileno.return_value=9
    context=Mock();context.__enter__=Mock(return_value=sock);context.__exit__=Mock(return_value=False)
    factory=Mock(return_value=context);monkeypatch.setattr(preview.socket,'socket',factory)
    child=Mock(pid=12345);child.poll.return_value=None
    popen=Mock(return_value=child);monkeypatch.setattr(preview.subprocess,'Popen',popen)
    identity={'start':'100','cwd':str(tmp_path),'app':True}
    monkeypatch.setattr(preview,'process_identity',lambda _:identity)
    monkeypatch.setattr(preview,'get',lambda *_:(200,b'{}'))
    verify=Mock(return_value={'httpsRoutes':'PASS','websocket':'PASS'})
    monkeypatch.setattr(preview,'verify',verify)
    settings={'listenHost':'127.0.0.1','listenPort':8802,'certificate':'fixture-cert','certificateKey':'fixture-key'}
    return SimpleNamespace(root=tmp_path,settings=settings,child=child,popen=popen,sock=sock,factory=factory,verify=verify)


def test_preview_activation_is_loopback_only_and_idempotent(launcher):
    x=launcher
    assert preview.launch(x.settings,'release-one')['status']=='RUNNING'
    x.sock.bind.assert_called_once_with(('127.0.0.1',8802))
    argv=x.popen.call_args.args[0]
    assert argv[0]==str(x.root/'.venv/bin/python') and '--ssl-keyfile' in argv
    assert x.popen.call_args.kwargs['pass_fds']==(9,)
    assert preview.launch(x.settings,'release-one')['status']=='ALREADY RUNNING'
    assert x.popen.call_count==1 and x.factory.call_count==1
    x.child.terminate.assert_not_called()
    with pytest.raises(ValueError,match='different release'):preview.launch(x.settings,'release-two')
    assert x.popen.call_count==1


def test_failed_preview_verification_cleans_up_only_its_own_child(launcher):
    x=launcher;x.verify.side_effect=ValueError('Fixture unhealthy route')
    with pytest.raises(ValueError):preview.launch(x.settings,'fixture')
    x.child.terminate.assert_called_once()
    assert not (x.root/'.runtime/private-preview/state.json').exists()
    assert x.popen.call_count==1


def test_occupied_port_never_stops_another_process(launcher):
    x=launcher;x.sock.bind.side_effect=OSError(98,'Address already in use')
    with pytest.raises(OSError):preview.launch(x.settings,'fixture')
    x.popen.assert_not_called();x.child.terminate.assert_not_called();x.child.kill.assert_not_called()


def test_reused_pid_is_never_treated_as_the_preview(launcher,monkeypatch):
    x=launcher;preview.launch(x.settings,'fixture')
    monkeypatch.setattr(preview,'process_identity',lambda _:{'start':'101','cwd':'/unrelated','app':False})
    with pytest.raises(ValueError,match='different process'):preview.launch(x.settings,'fixture')
    x.child.terminate.assert_not_called();x.child.kill.assert_not_called()


def test_https_reader_closes_connection_and_keeps_hostname(monkeypatch):
    connection=Mock();connection.getresponse.return_value=SimpleNamespace(status=200,read=lambda _:b'public')
    factory=Mock(return_value=connection);monkeypatch.setattr(preview,'LocalTLS',factory)
    assert preview.get({},'/api/health')==(200,b'public')
    connection.request.assert_called_once_with('GET','/api/health',headers={'Host':'defivela.xyz'})
    connection.close.assert_called_once()


@pytest.mark.parametrize('problem',['live','funding','private_api'])
def test_preview_http_verifier_rejects_unsafe_or_unauthenticated_accounts(tmp_path,monkeypatch,problem):
    monkeypatch.setattr(preview,'ROOT',tmp_path)
    (tmp_path/'site/dist/assets').mkdir(parents=True)
    (tmp_path/'site/dist/assets/fixture.js').write_bytes(b'fixture')
    (tmp_path/'site/dist/build-manifest.json').write_text(json.dumps({'entry':'dist/assets/fixture.js'}))
    health=dict(mode='PAPER',lending='DEMO',liveEnabled=problem=='live',realFunding=problem=='funding')
    def get(_,path):
        if path in ('/','/vault'):return 200,b'<title>VELLA</title>'
        if path.startswith('/assets/'):return 200,b'fixture'
        if path=='/api/health':return 200,json.dumps(health).encode()
        return (200 if problem=='private_api' else 401),b'{}'
    monkeypatch.setattr(preview,'get',get)
    with pytest.raises(ValueError,match='safety|exposed'):preview.verify({})


def test_preview_does_not_retry_a_permission_denial(launcher,monkeypatch):
    x=launcher;request=Mock(side_effect=PermissionError(1,'Operation not permitted'))
    monkeypatch.setattr(preview,'get',request)
    with pytest.raises(PermissionError):preview.launch(x.settings,'fixture')
    request.assert_called_once();x.child.terminate.assert_called_once()
