"""Secure read-only handoff and synthetic integrations; no live credentials/requests."""
import asyncio
import hashlib
import io
import json
import os
import runpy
from decimal import Decimal as D

import httpx
import pytest

from tools import multichain_handoff as handoff
from autodefi.adapters import CHAINS
from autodefi.trading import ProtocolModel, agents_for
from conftest import fixture_feed, funded
from test_api_ai import FixtureModel
from test_multichain import identity, sign
from vela.db import get, put, transaction


@pytest.fixture
def sources(tmp_path, monkeypatch):
    # Use the existing secure parser against fixture files, never real key values.
    module = runpy.run_path(str(handoff.MODEL_LOADER))
    module['load_model_file'].__globals__['ROOT'] = tmp_path
    monkeypatch.setattr(handoff.runpy, 'run_path', lambda _: module)
    model = tmp_path / '.runtime/lighter-showcase/model.env'
    model.parent.mkdir(parents=True)
    model.write_text('VELA_MODEL=fixture-model\nVELA_MODEL_KEY=fixture-key-never-a-credential\n')
    model.chmod(0o600)
    wallet = tmp_path / 'preview.env'
    wallet.write_text('VERA_PROTOCOL_ORIGIN=https://defivela.xyz\n'
                      'VERA_PROTOCOL_RPC=https://rpc.testnet.dogeos.com/\n'
                      'VERA_PROTOCOL_MYDOGE_CLIENT_ID=fixture-client\n'
                      'VERA_PROTOCOL_DB=/must/not/import.sqlite3\n'
                      'VERA_PROTOCOL_DEMO_ATTESTOR_KEY=must-not-import\n'
                      'VELA_OPERATOR_PRIVATE_KEY=must-not-import\n')
    wallet.chmod(0o600)
    monkeypatch.setattr(handoff, 'MODEL_FILE', model)
    monkeypatch.setattr(handoff, 'WALLET_FILE', wallet)
    return model, wallet


def test_read_only_handoff_ignores_ambient_secrets_and_preserves_files(sources, monkeypatch):
    for key in ['VELA_MODEL_KEY', 'ADP_MODEL_KEY', 'WHY_DISCOVERY_MODEL_KEY',
                'VERA_PROTOCOL_FACTORY', 'VERA_PROTOCOL_DEMO_ATTESTOR_KEY']:
        monkeypatch.setenv(key, 'ambient-must-not-import')
    before_env = dict(os.environ)
    before_files = [(p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in sources]
    database_existed = handoff.DATABASE.exists()
    config, model, report = handoff.prepare()
    assert model.ready and model.key == 'fixture-key-never-a-credential'
    assert config.origin == 'https://defivela.xyz' and config.client_id == 'fixture-client'
    assert config.multichain and config.network_enabled
    assert config.factory == config.attestor_key == ''
    assert config.paper().loss_budget <= D(2)
    assert dict(os.environ) == before_env
    assert before_files == [(p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in sources]
    assert handoff.DATABASE.exists() == database_existed
    assert report['candidateAssetsVerified'] == 18 and report['readyForControlledActivation'] is False
    assert 'fixture-key' not in json.dumps(report) and 'ambient-must-not-import' not in json.dumps(report)


def test_explicit_empty_model_mapping_does_not_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv('VELA_MODEL', 'ambient-model')
    monkeypatch.setenv('VELA_MODEL_KEY', 'ambient-secret')
    model = ProtocolModel(model_values={})
    assert not model.ready and model.key == ''


@pytest.mark.parametrize('which', [0, 1])
@pytest.mark.parametrize('problem', ['mode', 'symlink', 'missing', 'oversized'])
def test_private_configuration_rejects_unsafe_files(sources, which, problem):
    p = sources[which]
    if problem == 'mode':
        p.chmod(0o644)
    elif problem == 'symlink':
        other = p.with_name('actual.env');p.rename(other);p.symlink_to(other)
    elif problem == 'missing':
        p.unlink()
    else:
        p.write_text('x' * 16385)
    with pytest.raises(ValueError, match='mode-0600'):
        handoff.prepare()


@pytest.mark.parametrize('replacement', [
    ('https://defivela.xyz', 'http://127.0.0.1:8803'),
    ('https://rpc.testnet.dogeos.com/', 'https://mainnet.invalid/'),
    ('fixture-client', ''),
])
def test_wallet_origin_rpc_and_client_binding_fail_closed(sources, replacement):
    p = sources[1];p.write_text(p.read_text().replace(*replacement))
    with pytest.raises(ValueError):
        handoff.prepare()


def test_errors_do_not_echo_malformed_secret_input(sources, capsys, monkeypatch):
    sources[0].write_text('VELA_MODEL_KEY="fixture-sensitive-unterminated\n')
    monkeypatch.setattr(handoff.sys, 'argv', ['multichain_handoff.py', '--check'])
    assert handoff.main() == 1
    assert 'fixture-sensitive' not in capsys.readouterr().out
    sources[1].write_text('VERA_PROTOCOL_ORIGIN="fixture-sensitive-unterminated\n')
    with pytest.raises(ValueError) as exc:
        handoff.load_existing_wallet()
    assert 'fixture-sensitive' not in str(exc.value)


def test_existing_service_ledger_cannot_be_reused(sources, monkeypatch):
    monkeypatch.setattr(handoff, 'DATABASE', handoff.ROOT / '.runtime/lighter-showcase/multichain.sqlite3')
    with pytest.raises(ValueError, match='existing service ledger'):
        handoff.prepare()


def test_candidate_tampering_is_rejected(tmp_path, monkeypatch):
    directory = tmp_path / 'site/candidate';(directory / 'assets').mkdir(parents=True)
    (directory / 'index.html').write_text('fixture html')
    asset = directory / 'assets/app.js';asset.write_text('fixture js')
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = directory / 'build-manifest.json'
    manifest.write_text(json.dumps(dict(outputs=['candidate/assets/app.js'],
        sha256={'candidate/assets/app.js':digest(asset)},htmlSha256=digest(directory / 'index.html'))))
    release = tmp_path / 'release.json';release.write_text(json.dumps(dict(candidateManifestSha256=digest(manifest))))
    monkeypatch.setattr(handoff, 'ROOT', tmp_path);monkeypatch.setattr(handoff, 'RELEASE', release)
    assert handoff.verify_candidate() == 1
    asset.write_text('corrupted')
    with pytest.raises(ValueError, match='asset changed'):
        handoff.verify_candidate()


def test_candidate_factory_seven_network_sessions_and_secret_isolation(sources, monkeypatch, env):
    monkeypatch.setattr(handoff, 'DATABASE', env['config'].db_path)
    monkeypatch.setattr(handoff, 'LighterFeed', fixture_feed)
    app = handoff.create_app()
    assert app.state.runtime.config.network_enabled
    assert app.state.runtime.tasks == []  # Factory/check does not start a worker.
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat = asyncio.create_task(heartbeat())
        try:
            for network, chain in CHAINS.items():
                headers = {'Origin':handoff.ORIGIN,'X-ADP-Client':'web','X-Vella-Network':network}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=handoff.ORIGIN,headers=headers) as c:
                    assert (await c.get('/vault')).content == (handoff.ROOT / 'site/candidate/index.html').read_bytes()
                    config = (await c.get('/api/config')).json()
                    assert config['chain']['id'] == network and len(config['chains']) == 7
                    assert config['modelConfigured'] is True and config['myDogeClientId'] == 'fixture-client'
                    assert config['mode'] == 'PAPER' and config['lending'] == 'DEMO'
                    assert config['liveEnabled'] is False and config['realFunding'] is False
                    health = await c.get('/api/health')
                    assert 'fixture-key-never-a-credential' not in json.dumps(config) + health.text
                    assert (await c.get('/api/account')).status_code == 401
                    challenge = (await c.post('/api/auth/nonce',json={'address':identity(network),'chainId':chain.chain_id})).json()
                    r = await c.post('/api/auth/verify',json={'address':identity(network),'nonce':challenge['nonce'],'signature':sign(network,challenge['message'])})
                    assert r.status_code == 200 and 'Secure' in r.headers['set-cookie']
                    assert (await c.get('/api/session')).json()['network'] == network
                    assert (await c.post('/api/logout',json={})).status_code == 200
                    assert (await c.get('/api/account')).status_code == 401
        finally:
            await asyncio.get_running_loop().shutdown_default_executor();beat.cancel()
    try:asyncio.run(run())
    finally:app.state.db.close()


def test_loaded_model_responses_pass_risk_paper_execution_and_logging(sources, env):
    funded(env)
    model = handoff.load_existing_model()
    synthetic = FixtureModel()
    class SyntheticTransport:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            role = payload['text']['format']['name'].removeprefix('vela_')
            answer = synthetic.ask(role, {}, {})
            return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message',
                'content':[{'type':'output_text','text':json.dumps(answer)}]}]}).encode())
    model.opener = SyntheticTransport()
    db, paper = env['db'], env['paper']
    for market in env['feed'].markets.values():
        for c in market.candles:
            db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(market.symbol,c['ts'],c['o'],c['h'],c['l'],c['c']))
    agents = agents_for(db, model, paper)
    for _ in range(3):
        put(db, 'ai-last-call', 0);model.last=0;agents.tick_ai()
    assert synthetic.calls == ['research', 'review', 'direction']
    user = db.execute('SELECT * FROM users WHERE address=?',(env['owner'],)).fetchone()
    signal = agents.signal(user);assert signal
    assert get(db, 'ai-connection')['ok'] is True
    assert get(db, 'ai-decision:'+env['owner'])['phase'] == 'direction'
    with transaction(db):paper.open(env['owner'],signal)
    db.execute('UPDATE paper_orders SET due=0');paper.tick()
    assert len(paper.getPosition(env['owner'])) == 1
    assert paper.gate(env['owner'])['budget'] <= D(2)
    with transaction(db):paper.pause_entries(env['owner'])
    assert agents.signal(user) is None
