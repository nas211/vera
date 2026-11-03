import fcntl
import asyncio
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import httpx

from autodefi.build import fingerprint
from tools.build_backend_release import build
from tools.deploy_backend import Host, deploy, ledger_state, source_fingerprint
from tools.operate_backend import check, backup
from test_backend_release import command, commit


def ledger(root):
    path = root/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE users(address TEXT,session TEXT);
          INSERT INTO users VALUES ('fixture-owner','fixture-session');
          CREATE TABLE trades(value TEXT);
          CREATE TABLE loan_events(value TEXT);
          CREATE TABLE operations(value TEXT);
          CREATE TABLE chain_receipts(value TEXT);
          CREATE TABLE positions(value TEXT);
          CREATE TABLE paper_orders(status TEXT);
        ''')
    return path


class FakeHost:
    def __init__(self, root, fail=None):
        self.root, self.fail, self.events = root, fail, []
    def preflight(self, manifest):
        self.events.append('preflight')
        if self.fail == 'permission':raise RuntimeError('fixture service permission denied')
    def ui_state(self):return {'fixture-ui': 'unchanged'}
    def stop(self):self.events.append('stop')
    def start(self):self.events.append('start')
    def accept(self, expected):
        self.events.append('accept')
        assert expected == source_fingerprint(self.root)
        if self.fail == 'acceptance':
            # A post-start ledger record must survive code rollback.
            with sqlite3.connect(self.root/'.runtime/lighter-showcase/multichain-candidate.sqlite3') as db:
                db.execute("INSERT INTO trades VALUES ('post-start event')")
            raise RuntimeError('fixture acceptance failed')
    def install_ops(self, recovery):self.events.append('install_ops')
    def rollback_ops(self):self.events.append('rollback_ops')


@pytest.fixture
def release(tmp_path):
    source, target = tmp_path/'source', tmp_path/'target'
    for path in (source, target):(path/'backend/autodefi').mkdir(parents=True)
    command(source, 'init', '-q')
    for path in (source, target):(path/'backend/autodefi/app.py').write_text('old\n')
    base = commit(source)
    (source/'backend/autodefi/app.py').write_text('new\n')
    (source/'backend/autodefi/added.py').write_text('addition\n')
    commit(source)
    folder = tmp_path/'release'
    build(source, folder, base)
    path = ledger(target)
    return folder, target, tmp_path/'recovery', path


def test_denied_preflight_cannot_change_source_or_stop_service(release):
    folder, target, recovery, _ = release
    host = FakeHost(target, 'permission')
    with pytest.raises(RuntimeError, match='permission'):
        deploy(folder, target, recovery, host=host, apply=True)
    assert not recovery.exists()
    assert host.events == ['preflight']
    assert (target/'backend/autodefi/app.py').read_text() == 'old\n'


def test_successful_deployment_preserves_ledger_and_verifies_backup(release):
    folder, target, recovery, path = release
    before = ledger_state(path)
    host = FakeHost(target)
    result = deploy(folder, target, recovery, host=host, apply=True)
    assert result['deployed'] and result['ledgerContinuity'] and result['frontendUnchanged']
    assert result['liveProtectionVerified'] is False
    assert (target/'backend/autodefi/app.py').read_text() == 'new\n'
    assert ledger_state(path) == before
    assert json.loads((recovery/'backup.json').read_text())['restoreVerified']
    assert host.events == ['preflight', 'stop', 'start', 'accept', 'install_ops']


def test_failed_acceptance_rolls_back_code_without_rewinding_ledger(release):
    folder, target, recovery, path = release
    host = FakeHost(target, 'acceptance')
    with pytest.raises(RuntimeError, match='Current ledger retained'):
        deploy(folder, target, recovery, host=host, apply=True)
    assert (target/'backend/autodefi/app.py').read_text() == 'old\n'
    assert not (target/'backend/autodefi/added.py').exists()
    assert ledger_state(path)['counts']['trades'] == 1
    assert json.loads((recovery/'result.json').read_text())['rolledBack']
    assert host.events[-3:] == ['stop', 'rollback_ops', 'start']


@pytest.mark.parametrize('table,value', [('positions', 'open'), ('paper_orders', 'pending')])
def test_open_exposure_blocks_deployment_without_mutations(release, table, value):
    folder, target, recovery, path = release
    with sqlite3.connect(path) as db:db.execute('INSERT INTO '+table+' VALUES (?)', (value,))
    host = FakeHost(target)
    with pytest.raises(RuntimeError, match='idle paper ledger'):
        deploy(folder, target, recovery, host=host, apply=True)
    assert not recovery.exists()
    assert host.events == ['preflight']


def test_second_ledger_owner_prevents_overlay_after_service_stop(release):
    folder, target, recovery, path = release
    host = FakeHost(target)
    with open(str(path)+'.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='Current ledger retained'):
            deploy(folder, target, recovery, host=host, apply=True)
        assert (target/'backend/autodefi/app.py').read_text() == 'old\n'
        assert json.loads((recovery/'result.json').read_text())['rolledBack']
        assert host.events == ['preflight', 'stop', 'start']


def test_preflight_only_never_creates_backup_or_changes_service(release):
    folder, target, recovery, _ = release
    host = FakeHost(target)
    assert deploy(folder, target, recovery, host=host)['preflightPassed']
    assert host.events == ['preflight'] and not recovery.exists()


def test_fingerprint_binds_code_without_reading_secrets(tmp_path):
    path = tmp_path/'api.py';path.write_text('old')
    before = fingerprint(tmp_path)
    (tmp_path/'.env').write_text('fixture-private-placeholder')
    assert fingerprint(tmp_path) == before
    path.write_text('new')
    assert fingerprint(tmp_path) != before


def test_operations_health_detects_stale_backup_and_unhealthy_market(release, monkeypatch):
    _, root, _, _ = release
    monkeypatch.setattr('tools.operate_backend.shutil.disk_usage', lambda _: SimpleNamespace(free=10*1024**3))
    state = dict(mode='PAPER', realFunding=False, liveEnabled=False, marketData=True, engineReady=True, runtimeError='')
    fetch = lambda _: state
    assert not check(root, fetch=fetch)['healthy']
    result = backup(root)
    assert result['restoreVerified'] and result['integrity'] == 'ok'
    assert check(root, fetch=fetch)['healthy']
    state['runtimeError'] = 'Model step unavailable; no unapproved entries'
    assert not check(root, fetch=fetch)['healthy']
    state['runtimeError'] = ''
    assert check(root, fetch=fetch)['healthy']
    assert not check(root, now=10**12, fetch=fetch)['healthy']
    state['marketData'] = False
    assert not check(root, fetch=fetch)['healthy']


def test_model_repair_exception_is_explicit_preflight_only_and_narrow(tmp_path):
    state = dict(mode='PAPER', lending='DEMO', liveEnabled=False, realFunding=False,
                 marketData=True, engineReady=True, sourceFingerprint='fixture',
                 runtimeError='Model step unavailable; no unapproved entries')
    host = Host(tmp_path, repair_model_error=True)
    host.fetch = lambda _: state
    with pytest.raises(RuntimeError, match='acceptance failed'):host.health()
    host.health(repair_model_error=True)
    with pytest.raises(RuntimeError, match='acceptance failed'):
        host.health(expected='fixture', repair_model_error=True)
    with pytest.raises(RuntimeError, match='acceptance failed'):
        host.health(ready=True, repair_model_error=True)
    for field, value in [('runtimeError', 'Runtime step failed: OperationalError'),
                         ('runtimeError', 'Model step unavailable; no unapproved entries; chain failure'),
                         ('mode', 'LIVE'), ('realFunding', True), ('marketData', False)]:
        old = state[field];state[field] = value
        with pytest.raises(RuntimeError, match='acceptance failed'):host.health(repair_model_error=True)
        state[field] = old


def test_gateway_rejects_huge_length_without_forwarding_or_integer_error():
    from tools.dogeos_readonly import create_app, ORIGIN
    from test_dogeos_readonly import FixtureReader
    def request(_):pytest.fail('Malformed request must not reach the backend')
    app = create_app(FixtureReader(), transport=httpx.MockTransport(request))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            response = await client.post('/api/trading/allocate', content=b'{}',
                headers={'Origin': ORIGIN, 'X-ADP-Client': 'web', 'Content-Length': '9'*5000})
            assert response.status_code == 413
    asyncio.run(scenario())
