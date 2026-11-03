"""Guarded replacement for the existing VELLA preview, invoked by activate_preview.

No application, service, public route, contract or financial operation is created.
The database is backed up with SQLite's online backup API, never copied as a live
file and never rewound on rollback. All compatibility exercises use a copy.
"""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import sqlite3
import ssl
import subprocess
import tempfile
import time
import uuid

TARGET = 'f45b4c0'
PREVIOUS = 'c1c79d4'


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError('Cannot validate the pinned VELLA release in Git')
    return result.stdout


def pinned_application(root):
    if git(root, 'branch', '--show-current').strip() != b'main':
        raise ValueError('Activate only the DogeOS main branch; no multichain branch is authorized')
    paths = ['backend', 'config', 'contracts', 'site/src', 'site/prepared',
             'site/package.json', 'site/package-lock.json']
    if git(root, 'diff', TARGET, '--name-only', '--', *paths).strip():
        raise ValueError('Application differs from the approved f45b4c0 build')
    expected = set(git(root, 'ls-tree', '-r', '--name-only', TARGET, '--', 'backend').decode().splitlines())
    actual = {str(p.relative_to(root)) for p in (root / 'backend').rglob('*.py')}
    if actual != {p for p in expected if p.endswith('.py')}:
        raise ValueError('Unexpected backend source; refusing an unreviewed release')
    return git(root, 'rev-parse', TARGET).decode().strip()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as out:
        json.dump(value, out, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def database_backup(source, destination):
    """Read a coherent WAL snapshot. No connection ever opens the live DB writable."""
    if not source.is_file() or source.is_symlink() or destination.exists():
        raise ValueError('Expected an existing independent database and a new backup path')
    deadline = time.monotonic() + 45
    def progress(*_):
        if time.monotonic() > deadline:
            raise ValueError('Database backup exceeded its bounded time limit; preview was not stopped')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as original:
        with closing(sqlite3.connect(destination)) as backup:
            original.execute('BEGIN')
            original.execute('SELECT count(*) FROM sqlite_master').fetchone()
            original.backup(backup, pages=256, progress=progress, sleep=.05)
            original.rollback()
            if backup.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Database backup integrity check failed')
    destination.chmod(0o600)


def database_signature(db):
    schema = list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
    digest = hashlib.sha256()
    counts = {}
    for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = 0
        digest.update(name.encode())
        for row in db.execute('SELECT * FROM ' + quoted + ' ORDER BY rowid'):
            digest.update(repr(tuple(row)).encode())
            counts[name] += 1
    return dict(schema=[tuple(row) for row in schema], data=digest.hexdigest(), counts=counts)


def check_database(source_copy, previous_store, config):
    # Keep the paper engine's existing directory isolation guard intact. This is
    # a disposable copy inside VELLA's own database directory, never the live DB.
    with tempfile.TemporaryDirectory(prefix='activation-compat-', dir=config.db_path.parent) as folder:
        copy = Path(folder) / 'compatibility.sqlite3'
        database_backup(source_copy, copy)
        return _check_database(copy, previous_store, config)


def _check_database(copy, previous_store, config):
    """Exercise both initializers and modular account readers on an isolated copy."""
    from autodefi.store import open_db
    from autodefi.trading import GovernedPaper
    from autodefi.repayment import Repayment
    from vela.lighter.feed import LighterFeed
    from vela.lighter.engine import LighterPaperAdapter
    with closing(sqlite3.connect(copy)) as db:
        before = database_signature(db)
    with closing(open_db(copy)) as db:
        paper = GovernedPaper(db, replace(config.paper(), db_path=copy), LighterFeed())
        repayment = Repayment(db, paper)
        for row in db.execute('SELECT address FROM users').fetchall():
            paper.snapshot(row[0])
            repayment.status(row[0])
        if database_signature(db) != before:
            raise ValueError('Modular initialization would alter existing schema or account data')
    spec = importlib.util.spec_from_file_location('autodefi._rollback_store', previous_store)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    with closing(old.open_db(copy)) as db:
        LighterPaperAdapter(db, replace(config.paper(), db_path=copy), LighterFeed())
        if database_signature(db) != before:
            raise ValueError('Previous release cannot reopen the compatible database unchanged')
        if [tuple(row) for row in db.execute('PRAGMA integrity_check')] != [('ok',)]:
            raise ValueError('Compatibility-copy integrity check failed')
    return dict(status='PASS', migrationRequired=False, schemaUnchanged=True,
                accountDataUnchanged=True, previousInitializerReopen='PASS',
                tables=len(before['counts']), accounts=before['counts'].get('users', 0),
                loans=before['counts'].get('loans', 0), scope='Copied database; no runtime loop or network calls')


def preserve(api, settings, state):
    root = api.ROOT
    commit = pinned_application(root)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    backup = root / '.runtime/private-preview/backups' / stamp
    backup.mkdir(parents=True, mode=0o700)
    previous = backup / 'previous'
    previous.mkdir(mode=0o700)
    old_manifest = json.loads((root / 'snapshot/vella-release.json').read_text())
    for name in git(root, 'ls-tree', '-r', '--name-only', PREVIOUS, '--', 'backend').decode().splitlines():
        content = git(root, 'show', PREVIOUS + ':' + name)
        if name in old_manifest['files'] and hashlib.sha256(content).hexdigest() != old_manifest['files'][name]:
            raise ValueError('Previous backend does not match its recorded release manifest')
        target = previous / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for name, expected in old_manifest['files'].items():
        if name.startswith('backend/') and not (previous / name).is_file():
            raise ValueError('Previous backend archive is incomplete')
        if name.startswith('site/dist/') and name != 'site/dist/build-manifest.json':
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise ValueError('Preserved preview asset differs from its release manifest: ' + name)
    shutil.copytree(root / 'site/dist', previous / 'site/dist')
    shutil.copytree(root / 'backend', backup / 'prepared/backend',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(root / 'site/prepared', backup / 'prepared/site/prepared')
    shutil.copytree(root / 'config', backup / 'config')
    shutil.copy2(root / 'ops/preview.json', backup / 'preview.json')
    shutil.copy2(root / settings['environmentFile'], backup / 'preview.env')
    (backup / 'preview.env').chmod(0o600)
    shutil.copy2(root / 'snapshot/vella-release.json', backup / 'previous-release.json')
    shutil.copy2(root / 'snapshot/modular-release.json', backup / 'modular-release.json')
    write_json(backup / 'previous-state.json', state)
    config = api.Config.from_env()
    database_backup(config.db_path, backup / 'database.sqlite3')
    database_backup(backup / 'database.sqlite3', backup / 'compatibility.sqlite3')
    compatibility = check_database(backup / 'compatibility.sqlite3',
                                   previous / 'backend/autodefi/store.py', config)
    report = dict(applicationCommit=commit, previousSourceCommit=PREVIOUS,
                  backup=str(backup.relative_to(root)), databaseCompatibility=compatibility,
                  previousProcessPreserved=True, activated=False, liveProtectionVerified=False,
                  publicRoutingChanged=False, oldVelaModified=False)
    write_json(backup / 'preservation.json', report)
    return backup, report


def owns_listener(pid):
    """Linux socket inode check: never terminate an unrelated PID or listener."""
    descriptors = set()
    for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
        try:
            link = os.readlink(fd)
        except FileNotFoundError:
            continue
        if link.startswith('socket:['):
            descriptors.add(link[8:-1])
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[1] == '0100007F:2262' and fields[3] == '0A' and fields[9] in descriptors:
            return True
    return False


def stop_recorded(api, state):
    # pidfd binds the signal to this process, even if the integer PID is reused.
    with closing(os.fdopen(os.pidfd_open(state['pid']), 'rb', buffering=0)) as handle:
        if not api.already_running(state, state['fingerprint']) or not owns_listener(state['pid']):
            raise ValueError('Recorded process no longer owns the private preview; nothing was stopped')
        signal.pidfd_send_signal(handle.fileno(), signal.SIGTERM)
        if select.select([handle], [], [], 20)[0]:
            return
        # Replacement was explicitly requested; finish stopping only this pidfd.
        signal.pidfd_send_signal(handle.fileno(), signal.SIGKILL)
        if not select.select([handle], [], [], 5)[0]:
            raise ValueError('Recorded preview has not exited; no second runtime was started')


def restore_backend(root, archived):
    for source in archived.rglob('*.py'):
        target = root / 'backend' / source.relative_to(archived)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_suffix('.activate-tmp')
        staged.write_bytes(source.read_bytes())
        os.replace(staged, target)
        # Do not let same-second source swaps reuse stale Python bytecode.
        for cached in (target.parent / '__pycache__').glob(target.stem + '.*.pyc'):
            cached.unlink()


def activate(api, settings, fingerprint, *, check_only=False):
    root = api.ROOT
    runtime = root / '.runtime/private-preview'
    with (runtime / 'activation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((runtime / 'state.json').read_text())
        if not api.already_running(state, state['fingerprint']):
            raise ValueError('Recorded preview is not running; refusing to create a replacement application')
        if state['fingerprint'] == fingerprint:
            if check_only:
                return dict(status='RECORDED MODULAR RELEASE — SERVING NOT CHECKED')
            return dict(status='ALREADY RUNNING', **api.verify(settings))
        backup, report = preserve(api, settings, state)
        if check_only:
            return dict(report, status='PRESERVED AND COMPATIBLE — NOT ACTIVATED')
        # A failed control-plane check must leave the running process intact.
        # Do not blindly stop it when this terminal cannot verify private HTTPS.
        try:
            status, body = api.get(settings, '/api/health')
            health = json.loads(body)
        except Exception as error:
            report.update(status='BLOCKED BEFORE RESTART', failureType=type(error).__name__)
            write_json(backup / 'activation.json', report)
            write_json(runtime / 'modular-activation.json', report)
            raise ValueError('Private preview HTTPS is unavailable (' + type(error).__name__ +
                             '); original process was not stopped. Backup: ' + report['backup']) from None
        if (status != 200 or health.get('mode') != 'PAPER' or health.get('lending') != 'DEMO'
                or health.get('liveEnabled') is not False or health.get('realFunding') is not False):
            raise ValueError('Existing private preview safety check failed; nothing was stopped')
        status, body = api.get(settings, '/')
        if status != 200 or body != (backup / 'previous/site/dist/index.html').read_bytes():
            raise ValueError('Serving preview differs from the preserved release; nothing was stopped')
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(settings['certificate'], settings['certificateKey'])
        if not owns_listener(state['pid']):
            raise ValueError('Recorded process does not own port 8802; nothing was stopped')
        stop_recorded(api, state)
        (runtime / 'state.json').unlink()
        report['previousProcessPreserved'] = False
        try:
            # Capture the final old-runtime writes before starting the new process.
            database_backup(api.Config.from_env().db_path, backup / 'database-at-stop.sqlite3')
            result = api.launch(settings, fingerprint, lock_held=True)
        except BaseException as error:
            rollback = 'FAILED'
            try:
                restore_backend(root, backup / 'previous/backend')
                old_settings = json.loads((backup / 'preview.json').read_text())
                api.launch(old_settings, state['fingerprint'], lock_held=True)
                rollback = 'PREVIOUS PREVIEW RESTORED'
            except BaseException:
                pass
            finally:
                # As before activation, the old process holds the old modules in
                # memory while the exact modular source remains prepared on disk.
                restore_backend(root, backup / 'prepared/backend')
            write_json(backup / 'activation.json', dict(report, status='FAILED', rollback=rollback,
                       failureType=type(error).__name__, databaseRewound=False))
            raise ValueError('Modular activation failed; ' + rollback + '; inspect ' +
                             str(backup.relative_to(root) / 'activation.json')) from None
        report.update(result, activated=True, previousProcessPreserved=False, databaseRewound=False)
        write_json(backup / 'activation.json', report)
        write_json(runtime / 'modular-activation.json', report)
        return report
