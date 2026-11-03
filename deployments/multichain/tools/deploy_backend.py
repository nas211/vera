"""Guarded deployment to the existing VPS backend; no frontend or ledger replacement."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.backup_state import backup_database
from tools.build_backend_release import verify

TARGET = Path('/root/VellaMultichain')
BACKEND = 'vela-multichain-candidate.service'
GATEWAY = 'vela-dogeos-readonly.service'
TIMERS = ('vela-backend-backup.timer', 'vela-backend-health.timer')
TABLES = ('users', 'trades', 'loan_events', 'operations', 'chain_receipts')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_fingerprint(root):
    value = hashlib.sha256()
    for path in sorted((root/'backend/autodefi').glob('*.py')):
        value.update(path.name.encode()+b'\0'+hashlib.sha256(path.read_bytes()).digest())
    return value.hexdigest()


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError('Refuse redirected deployment path')
    fd, temporary = tempfile.mkstemp(prefix='.vela-deploy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def archive_files(path):
    # Call verify() before this function. Never use tar.extractall on live paths.
    with tarfile.open(path, 'r:gz') as archive:
        return {member.name: (archive.extractfile(member).read(), member.mode & 0o777)
                for member in archive if member.isfile()}


def ledger_state(path):
    info = path.stat()
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        counts = {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in TABLES}
        identities = {digest((str(row[0])+':'+str(row[1])).encode()) for row in db.execute('SELECT address,session FROM users')}
        positions = db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM paper_orders WHERE status='pending'").fetchone()[0]
        return dict(inode=(info.st_dev, info.st_ino), counts=counts, identities=identities,
                    positions=positions, pending=pending)
    finally:
        db.close()


def assert_idle(state):
    if state['positions'] or state['pending']:
        raise RuntimeError('Wait for an idle paper ledger; deployment never force-closes positions or cancels orders')


def assert_continuity(before, after):
    if (before['inode'] != after['inode'] or not before['identities'].issubset(after['identities']) or
            any(after['counts'][name] < count for name, count in before['counts'].items())):
        raise RuntimeError('Ledger continuity check failed; retain current ledger for reconciliation')


class Host:
    def __init__(self, root, repair_model_error=False):
        self.root = root
        self.repair_model_error = repair_model_error
        self.config_changes = []
        self.enabled = []

    def systemctl(self, *args, check=True, timeout=150):
        result = subprocess.run(['systemctl', *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError('Vela service manager command failed: '+args[0])
        return result

    def state(self, name):
        result = self.systemctl('show', name, '--no-pager', '-p', 'ActiveState', '-p', 'MainPID',
                                '-p', 'WorkingDirectory', '-p', 'ExecStart', '-p', 'Result')
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def fetch(self, url):
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read(65536))

    def health(self, expected=None, ready=False, repair_model_error=False):
        urls = ['http://127.0.0.1:8804/api/'+('ready' if ready else 'health'),
                'http://127.0.0.1:8805/api/health', 'https://defivela.xyz/api/health']
        for url in urls:
            state = self.fetch(url)
            runtime_error = state.get('runtimeError')
            # A known failed model loop must be repairable. This exception is
            # explicit, preflight-only and cannot relax post-deploy acceptance.
            if (repair_model_error and not expected and not ready and
                    runtime_error == 'Model step unavailable; no unapproved entries'):
                runtime_error = ''
            if (state.get('mode') != 'PAPER' or state.get('lending') != 'DEMO' or
                    state.get('liveEnabled') is not False or state.get('realFunding') is not False or
                    state.get('marketData') is not True or runtime_error):
                raise RuntimeError('Existing paper-only health/market acceptance failed')
            if expected and (state.get('sourceFingerprint') != expected or state.get('engineReady') is not True):
                raise RuntimeError('Published endpoint is not serving the expected healthy backend source')

    def preflight(self, manifest):
        for name, factory in [(BACKEND, 'tools.multichain_handoff:create_app'), (GATEWAY, 'tools.dogeos_readonly:create_app')]:
            state = self.state(name)
            if (state.get('ActiveState') != 'active' or int(state.get('MainPID', '0')) <= 0 or
                    state.get('WorkingDirectory') != str(self.root) or factory not in state.get('ExecStart', '')):
                raise RuntimeError('Existing service identity/state differs; reconcile before deployment')
        head = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        if head != manifest['baseCommit']:
            raise RuntimeError('Target repository advanced; reconcile the release first')
        if subprocess.run(['git', '-C', str(self.root), 'diff', '--quiet', 'HEAD', '--',
                           'backend', 'components', 'config', 'tools', 'ops']).returncode:
            raise RuntimeError('Preserve unpublished target backend changes; reconcile first')
        self.health(repair_model_error=self.repair_model_error)

    def ui_state(self):
        # Observe existing Nginx roots; do not alter routing or static files.
        import re
        nginx = Path('/etc/nginx/sites-available/defivela.xyz').read_bytes()
        roots = set(re.findall(rb'root\s+(/var/www/defivela-ui/[^\s;]+);', nginx))
        if not roots:
            raise RuntimeError('Current frontend root is not recognized')
        result = {'nginx': digest(nginx)}
        for raw in roots:
            root = Path(raw.decode())
            for path in sorted(root.rglob('*')):
                if path.is_file():result[str(path)] = digest(path.read_bytes())
        return result

    def stop(self):
        self.systemctl('stop', GATEWAY)
        self.systemctl('stop', BACKEND)
        for name in (BACKEND, GATEWAY):
            state = self.state(name)
            if (state.get('ActiveState') != 'inactive' or state.get('MainPID') != '0' or
                    state.get('Result') != 'success'):
                raise RuntimeError('Existing Vela owner has not stopped cleanly')

    def start(self):
        self.systemctl('start', BACKEND)
        self.systemctl('start', GATEWAY)

    def accept(self, expected):
        deadline = time.monotonic()+120
        while True:
            try:
                self.health(expected=expected, ready=True)
                break
            except Exception:
                if time.monotonic() >= deadline:raise
                time.sleep(2)
        async def stream():
            import websockets
            async with websockets.connect('wss://defivela.xyz/ws/markets', origin='https://defivela.xyz', open_timeout=15) as ws:
                data = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if data.get('privateData') is not False or not data.get('markets'):
                    raise RuntimeError('Public WebSocket acceptance failed')
        asyncio.run(stream())

    def install_ops(self, recovery):
        (self.root/'.runtime/backups').mkdir(mode=0o700, parents=True, exist_ok=True)
        mapping = {'vela-multichain-candidate.service.d/60-vela-backend.conf':
                   'vela-backend-reliability.override.conf.pending'}
        for suffix in ('backup.service', 'backup.timer', 'health.service', 'health.timer'):
            mapping['vela-backend-'+suffix] = 'vela-backend-'+suffix+'.pending'
        for relative, source in mapping.items():
            path = Path('/etc/systemd/system')/relative
            new = (self.root/'ops'/source).read_bytes()
            if path.is_symlink():raise RuntimeError('Refuse redirected service configuration')
            old = path.read_bytes() if path.exists() else None
            if old is not None and old != new:
                raise RuntimeError('Preserve existing operational configuration; reconcile first')
            self.config_changes.append((path, old, new))
            if old is not None:(recovery/('config-'+str(len(self.config_changes)))).write_bytes(old)
            atomic_write(path, new)
        self.systemctl('daemon-reload')
        for name in (BACKEND, GATEWAY, *TIMERS):
            enabled = self.systemctl('is-enabled', '--quiet', name, check=False).returncode == 0
            self.enabled.append((name, enabled))
            self.systemctl('enable', name)
        self.systemctl('start', 'vela-backend-backup.service')
        self.systemctl('start', 'vela-backend-health.service')
        for name in TIMERS:self.systemctl('start', name)

    def rollback_ops(self):
        for name, previous in reversed(self.enabled):
            if name in TIMERS:self.systemctl('stop', name, check=False)
            if not previous:self.systemctl('disable', name, check=False)
        for path, previous, deployed in reversed(self.config_changes):
            if path.exists() and path.read_bytes() != deployed:
                raise RuntimeError('Operational configuration changed during deployment; preserve it')
            if previous is None:path.unlink(missing_ok=True)
            else:atomic_write(path, previous)
        if self.config_changes:self.systemctl('daemon-reload')


def deploy(release, root, recovery, host=None, apply=False, backup_fn=backup_database):
    release, root, recovery = Path(release).resolve(), Path(root).resolve(), Path(recovery)
    verify(release, root)
    manifest = json.loads((release/'manifest.json').read_text())
    host = host or Host(root)
    host.preflight(manifest)  # Service/HTTP access must succeed before any mutation.
    database = root/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    baseline = ledger_state(database)
    assert_idle(baseline)
    frontend = host.ui_state()
    if not apply:
        return dict(preflightPassed=True, deployed=False, mode='PAPER', liveProtectionVerified=False)
    recovery.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup = backup_fn(database, recovery/'ledger.sqlite3')
    atomic_write(recovery/'backup.json', (json.dumps(backup, indent=2)+'\n').encode(), 0o600)
    files = archive_files(release/'backend-overlay.tar.gz')
    previous = archive_files(release/'backend-rollback.tar.gz')
    changed = []
    lock = None
    stop_attempted = False
    result = dict(releaseCommit=manifest['releaseCommit'], deployed=False, mode='PAPER',
                  liveProtectionVerified=False, realTransactionsSubmitted=0, databaseReplaced=False)
    try:
        stop_attempted = True
        host.stop()
        lock = open(str(database)+'.lock', 'a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stopped = ledger_state(database)
        assert_idle(stopped)
        assert_continuity(baseline, stopped)
        verify(release, root)  # Recheck after shutdown; never overwrite a racing edit.
        for name, (content, mode) in files.items():
            atomic_write(root/name, content, mode)
            changed.append(name)
        assert_continuity(stopped, ledger_state(database))
        if host.ui_state() != frontend:raise RuntimeError('Frontend changed during deployment')
        expected = source_fingerprint(root)
        lock.close();lock = None
        host.start()
        host.accept(expected)
        assert_continuity(stopped, ledger_state(database))
        host.install_ops(recovery)
        if host.ui_state() != frontend:raise RuntimeError('Frontend changed during acceptance')
        result.update(deployed=True, backendFingerprint=expected, ledgerContinuity=True,
                      frontendUnchanged=True, healthHttpsWebSocketVerified=True,
                      backupsAndHealthTimersInstalled=True, genuineWalletApprovalVerified=False)
        atomic_write(recovery/'result.json', (json.dumps(result, indent=2)+'\n').encode(), 0o600)
        return result
    except BaseException as error:
        result.update(errorType=type(error).__name__, rolledBack=False)
        try:
            if changed:
                host.stop()
                host.rollback_ops()
                if lock is None:
                    lock = open(str(database)+'.lock', 'a')
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for name in reversed(changed):
                    path = root/name
                    if not path.is_file() or digest(path.read_bytes()) != digest(files[name][0]):
                        raise RuntimeError('Changed release file requires manual reconciliation')
                    if name in previous:atomic_write(path, *previous[name])
                    else:path.unlink()
            if lock is not None:lock.close();lock = None
            if stop_attempted:host.start()
            result['rolledBack'] = True
        except BaseException as recovery_error:
            result['rollbackErrorType'] = type(recovery_error).__name__
        atomic_write(recovery/'result.json', (json.dumps(result, indent=2)+'\n').encode(), 0o600)
        raise RuntimeError('Deployment failed; inspect the private recovery result. Current ledger retained.') from error
    finally:
        if lock is not None:lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--repair-model-error', action='store_true',
                        help='Allow only the known model-loop error before repair; require healthy acceptance afterward')
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0:raise SystemExit('Existing root-owned Vela service access is required')
    recovery = TARGET/'.runtime/deployments'/time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    try:
        print(json.dumps(deploy(args.release, TARGET, recovery,
                               host=Host(TARGET, repair_model_error=args.repair_model_error), apply=args.apply)))
    except Exception as error:
        print(json.dumps(dict(deployed=False, error=str(error), errorType=type(error).__name__,
                              recovery=str(recovery), liveProtectionVerified=False)))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
