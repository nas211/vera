"""Activation guards and rollback fixtures. No real process is signalled here."""
import json
import os
from pathlib import Path
import signal
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import activate_preview as preview
from tools import modular_preview as modular
from conftest import funded


def test_online_backup_and_both_initializers_preserve_existing_accounts(env, tmp_path):
    funded(env)
    before = modular.database_signature(env['db'])
    backup = tmp_path / 'backup.sqlite3'
    modular.database_backup(env['config'].db_path, backup)
    check = modular.check_database(backup, Path(__file__).resolve().parents[1] /
                                   'backend/autodefi/store.py', env['config'])
    assert check['status'] == 'PASS' and check['accounts'] == 1 and check['loans'] == 1
    assert check['schemaUnchanged'] and check['accountDataUnchanged']
    assert check['previousInitializerReopen'] == 'PASS'
    assert modular.database_signature(env['db']) == before
    assert backup.stat().st_mode & 0o777 == 0o600


def test_compatibility_rejects_a_required_migration_without_touching_live_db(env, tmp_path):
    before = modular.database_signature(env['db'])
    backup = tmp_path / 'backup.sqlite3'
    modular.database_backup(env['config'].db_path, backup)
    with sqlite3.connect(backup) as db:
        db.execute('DROP TABLE paper_activity')
    with pytest.raises(ValueError, match='alter existing schema'):
        modular.check_database(backup, Path(__file__).resolve().parents[1] /
                               'backend/autodefi/store.py', env['config'])
    assert modular.database_signature(env['db']) == before


def test_backup_refuses_missing_source_and_existing_destination(tmp_path):
    source = tmp_path / 'missing.sqlite3'
    with pytest.raises(ValueError):
        modular.database_backup(source, tmp_path / 'copy.sqlite3')
    source.touch()
    with pytest.raises(ValueError):
        modular.database_backup(source, source)


@pytest.mark.parametrize('branch', ['multichain', 'feature/multichain', ''])
def test_multichain_or_detached_branch_is_never_activated(tmp_path, monkeypatch, branch):
    command = Mock(return_value=branch.encode())
    monkeypatch.setattr(modular, 'git', command)
    with pytest.raises(ValueError, match='main branch'):
        modular.pinned_application(tmp_path)
    assert command.call_count == 1


def test_changed_application_is_not_the_authorized_release(tmp_path, monkeypatch):
    monkeypatch.setattr(modular, 'git', Mock(side_effect=[b'main', b'backend/autodefi/api.py']))
    with pytest.raises(ValueError, match='approved f45b4c0'):
        modular.pinned_application(tmp_path)


@pytest.fixture
def replacement(tmp_path, monkeypatch):
    runtime = tmp_path / '.runtime/private-preview'
    runtime.mkdir(parents=True)
    state = dict(pid=12345, identity={'start':'100', 'cwd':str(tmp_path), 'app':True}, fingerprint='old')
    (runtime / 'state.json').write_text(json.dumps(state))
    backup = runtime / 'backups/fixture'
    backup.mkdir(parents=True)
    for base, content in [('previous', 'old'), ('prepared', 'new')]:
        folder = backup / base / 'backend/autodefi'
        folder.mkdir(parents=True)
        (folder / 'api.py').write_text(content)
    live = tmp_path / 'backend/autodefi/api.py'
    live.parent.mkdir(parents=True)
    live.write_text('new')
    (backup / 'previous/site/dist').mkdir(parents=True)
    (backup / 'previous/site/dist/index.html').write_bytes(b'old html')
    (backup / 'preview.json').write_text(json.dumps({'releaseManifest':'old', 'listenPort':8802}))
    database = tmp_path / 'live.sqlite3'
    database.write_bytes(b'live data')
    report = dict(backup=str(backup.relative_to(tmp_path)), activated=False,
                  previousProcessPreserved=True, databaseCompatibility={'status':'PASS'})
    preserve = Mock(return_value=(backup, report))
    monkeypatch.setattr(modular, 'preserve', preserve)
    stop = Mock()
    monkeypatch.setattr(modular, 'stop_recorded', stop)
    monkeypatch.setattr(modular, 'owns_listener', lambda _:True)
    copy = Mock()
    monkeypatch.setattr(modular, 'database_backup', copy)
    monkeypatch.setattr(modular.ssl, 'SSLContext', Mock())
    health = json.dumps(dict(mode='PAPER', lending='DEMO', liveEnabled=False, realFunding=False)).encode()
    api = SimpleNamespace(ROOT=tmp_path, already_running=Mock(return_value=True),
                          get=Mock(side_effect=[(200, health), (200, b'old html')]),
                          verify=Mock(return_value={'servedAsset':'PASS'}),
                          launch=Mock(return_value={'status':'RUNNING', 'servedAsset':'PASS'}),
                          Config=SimpleNamespace(from_env=lambda:SimpleNamespace(db_path=database)))
    settings = dict(releaseManifest='modular', frontendDirectory='site/prepared',
                    moduleApiVersion=1, certificate='fixture', certificateKey='fixture')
    return SimpleNamespace(root=tmp_path, runtime=runtime, backup=backup, state=state,
                           api=api, settings=settings, preserve=preserve, stop=stop,
                           copy=copy, live=live, database=database)


def test_check_preserves_database_without_sockets_or_process_changes(replacement):
    x = replacement
    result = modular.activate(x.api, x.settings, 'new', check_only=True)
    assert result['status'] == 'PRESERVED AND COMPATIBLE — NOT ACTIVATED'
    assert result['activated'] is False
    x.preserve.assert_called_once()
    x.api.get.assert_not_called()
    x.stop.assert_not_called()
    x.api.launch.assert_not_called()


def test_inaccessible_https_never_stops_existing_preview(replacement):
    x = replacement
    x.api.get.side_effect = TimeoutError()
    with pytest.raises(ValueError, match='original process was not stopped'):
        modular.activate(x.api, x.settings, 'new')
    x.stop.assert_not_called()
    x.api.launch.assert_not_called()
    assert json.loads((x.runtime / 'state.json').read_text()) == x.state
    report = json.loads((x.backup / 'activation.json').read_text())
    assert report['activated'] is False and report['failureType'] == 'TimeoutError'


def test_different_served_html_never_stops_a_process(replacement):
    x = replacement
    x.api.get.side_effect = [(200, json.dumps(dict(mode='PAPER', lending='DEMO', liveEnabled=False,
                                                realFunding=False)).encode()), (200, b'unknown')]
    with pytest.raises(ValueError, match='differs from the preserved release'):
        modular.activate(x.api, x.settings, 'new')
    x.stop.assert_not_called()
    x.api.launch.assert_not_called()


def test_different_listener_owner_never_stops_a_process(replacement, monkeypatch):
    x = replacement
    monkeypatch.setattr(modular, 'owns_listener', lambda _:False)
    with pytest.raises(ValueError, match='does not own port 8802'):
        modular.activate(x.api, x.settings, 'new')
    x.stop.assert_not_called()
    x.api.launch.assert_not_called()


def test_replacement_uses_existing_launcher_and_never_rewinds_database(replacement):
    x = replacement
    result = modular.activate(x.api, x.settings, 'new')
    assert result['activated'] is True and result['databaseRewound'] is False
    x.stop.assert_called_once_with(x.api, x.state)
    x.api.launch.assert_called_once_with(x.settings, 'new', lock_held=True)
    assert x.database.read_bytes() == b'live data'
    assert not (x.runtime / 'state.json').exists() # Real launcher writes its new verified child state.


def test_failed_new_preview_rolls_back_code_and_retains_latest_ledger(replacement):
    x = replacement
    def launch(settings, fingerprint, **kwargs):
        if fingerprint == 'new':
            assert x.live.read_text() == 'new'
            x.database.write_bytes(b'latest ledger')
            raise ValueError('fixture verification failure')
        assert x.live.read_text() == 'old'
        return {'status':'RUNNING'}
    x.api.launch.side_effect = launch
    with pytest.raises(ValueError, match='PREVIOUS PREVIEW RESTORED'):
        modular.activate(x.api, x.settings, 'new')
    assert x.database.read_bytes() == b'latest ledger'
    assert x.live.read_text() == 'new' # Ready to retry without changing the old process's loaded code.
    report = json.loads((x.backup / 'activation.json').read_text())
    assert report['rollback'] == 'PREVIOUS PREVIEW RESTORED' and report['databaseRewound'] is False
    assert x.api.launch.call_count == 2


def test_failed_rollback_is_reported_as_failed_not_activated(replacement):
    x = replacement
    x.api.launch.side_effect = ValueError('fixture failure')
    with pytest.raises(ValueError, match='FAILED'):
        modular.activate(x.api, x.settings, 'new')
    report = json.loads((x.backup / 'activation.json').read_text())
    assert report['rollback'] == 'FAILED' and report['activated'] is False
    assert x.live.read_text() == 'new'


def test_same_release_verifies_serving_without_restarting(replacement):
    x = replacement
    result = modular.activate(x.api, x.settings, 'old')
    assert result['status'] == 'ALREADY RUNNING'
    x.api.verify.assert_called_once_with(x.settings)
    x.stop.assert_not_called()
    x.preserve.assert_not_called()


def test_pidfd_identity_recheck_prevents_signalling_a_reused_pid(tmp_path, monkeypatch):
    path = tmp_path / 'pidfd-fixture'
    path.touch()
    monkeypatch.setattr(modular.os, 'pidfd_open', lambda _:os.open(path, os.O_RDONLY))
    send = Mock()
    monkeypatch.setattr(modular.signal, 'pidfd_send_signal', send)
    api = SimpleNamespace(already_running=Mock(return_value=False))
    with pytest.raises(ValueError, match='nothing was stopped'):
        modular.stop_recorded(api, {'pid':123, 'fingerprint':'old'})
    send.assert_not_called()


@pytest.mark.parametrize('graceful', [True, False])
def test_stop_targets_only_verified_pidfd_and_waits_for_exit(tmp_path, monkeypatch, graceful):
    path = tmp_path / 'pidfd-fixture'
    path.touch()
    monkeypatch.setattr(modular.os, 'pidfd_open', lambda _:os.open(path, os.O_RDONLY))
    send = Mock()
    monkeypatch.setattr(modular.signal, 'pidfd_send_signal', send)
    monkeypatch.setattr(modular, 'owns_listener', lambda _:True)
    monkeypatch.setattr(modular.select, 'select', Mock(side_effect=
                        [([1], [], [])] if graceful else [([], [], []), ([1], [], [])]))
    api = SimpleNamespace(already_running=Mock(return_value=True))
    modular.stop_recorded(api, {'pid':123, 'fingerprint':'old'})
    signals = [call.args[1] for call in send.call_args_list]
    assert signals == ([signal.SIGTERM] if graceful else [signal.SIGTERM, signal.SIGKILL])
    assert len({call.args[0] for call in send.call_args_list}) == 1


def test_modular_verifier_rejects_old_html_even_when_title_matches(tmp_path, monkeypatch):
    folder = tmp_path / 'site/prepared'
    folder.mkdir(parents=True)
    (folder / 'index.html').write_bytes(b'<title>VELLA</title>new')
    monkeypatch.setattr(preview, 'ROOT', tmp_path)
    monkeypatch.setattr(preview, 'get', Mock(return_value=(200, b'<title>VELLA</title>old')))
    with pytest.raises(ValueError, match='HTML does not match'):
        preview.verify(dict(moduleApiVersion=1, frontendDirectory='site/prepared'))


@pytest.mark.parametrize('issue', ['module', 'chain', 'mainnet', 'live', 'asset'])
def test_modular_verifier_requires_correct_api_network_and_every_asset(tmp_path, monkeypatch, issue):
    folder = tmp_path / 'site/prepared'
    (folder / 'assets').mkdir(parents=True)
    html = b'<title>VELLA</title>modular'
    (folder / 'index.html').write_bytes(html)
    for asset in ('fixture.js', 'chunk.js'):
        (folder / 'assets' / asset).write_bytes(b'verified asset')
    (folder / 'build-manifest.json').write_text(json.dumps(dict(entry='prepared/assets/fixture.js',
        outputs=['prepared/assets/fixture.js', 'prepared/assets/chunk.js'])))
    health = dict(mode='PAPER', lending='DEMO', liveEnabled=False, realFunding=False)
    config = dict(health, moduleApiVersion=0 if issue=='module' else 1,
                  network=dict(chainId=1 if issue=='chain' else 6281971, testnet=issue!='mainnet'))
    if issue == 'live':
        config['liveEnabled'] = True
    def get(_, path):
        if path in ('/', '/vault'):
            return 200, html
        if path.startswith('/assets/'):
            return 200, b'corrupt' if issue=='asset' and path.endswith('chunk.js') else b'verified asset'
        if path == '/api/health':
            return 200, json.dumps(health).encode()
        if path == '/api/config':
            return 200, json.dumps(config).encode()
        return 401, b'{}'
    monkeypatch.setattr(preview, 'ROOT', tmp_path)
    monkeypatch.setattr(preview, 'get', get)
    with pytest.raises(ValueError, match='Modular API|asset is not being served'):
        preview.verify(dict(moduleApiVersion=1, frontendDirectory='site/prepared'))
