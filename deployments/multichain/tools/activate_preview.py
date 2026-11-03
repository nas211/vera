#!/usr/bin/env python3
"""Start only the VELLA loopback TLS preview. Never change Nginx, DNS or systemd."""
import argparse
from contextlib import closing
import errno
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from autodefi.config import Config,MODEL_VARIABLES,model_env
from autodefi.trading import ProtocolModel

ALLOWED = {'ORIGIN', 'EXECUTION', 'LENDING', 'NETWORK_ENABLED', 'RPC', 'DB', 'FACTORY',
           'MYDOGE_CLIENT_ID', 'WALLETCONNECT_PROJECT_ID', 'DEMO_ATTESTOR_KEY',
           'MODEL_ENDPOINT', 'MODEL', 'MODEL_KEY'}


def load_environment(path):
    """JSON-quoted or plain values, no shell evaluation and no credential output."""
    if not path.exists():
        raise ValueError('Missing dedicated .runtime/preview.env; do not source old Vela configuration')
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Preview environment must be an owned regular file with mode 600')
    updates = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError('Invalid preview environment assignment')
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        name = key.removeprefix('VERA_PROTOCOL_') if key.startswith('VERA_PROTOCOL_') else key.removeprefix('ADP_') if key.startswith('ADP_') else key.removeprefix('VELA_') if key in {'VELA_'+n for n in MODEL_VARIABLES} else ''
        if name not in ALLOWED or key in updates:
            raise ValueError('Unsupported or duplicate preview variable: ' + key)
        try:
            if value.startswith('"'):
                value = json.loads(value)
            if not isinstance(value, str) or '\n' in value or '\r' in value:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Invalid preview value for ' + key) from None
        if key in os.environ and os.environ[key] != value:
            raise ValueError('Conflicting preview variable: ' + key)
        updates[key] = value
    for name in MODEL_VARIABLES:model_env(name,values={**os.environ,**updates})
    os.environ.update(updates)


def prepared(modular=False):
    settings = json.loads((ROOT / 'ops/preview.json').read_text())
    if modular:
        # An explicit private-preview operation; leave the public/legacy configuration intact.
        settings = dict(settings, releaseManifest='snapshot/modular-release.json',
                        frontendDirectory='site/prepared', moduleApiVersion=1)
    if settings['listenHost'] != '127.0.0.1' or settings['listenPort'] != 8802 or settings['origin'] != 'https://defivela.xyz':
        raise ValueError('Preview must use the approved origin and isolated loopback port 8802')
    envfile = ROOT / settings['environmentFile']
    if not envfile.resolve().is_relative_to(ROOT / '.runtime'):
        raise ValueError('Independent preview environment required')
    load_environment(envfile)
    config = Config.from_env()
    if config.origin != settings['origin']:
        raise ValueError('Preview origin must be https://defivela.xyz')
    model = ProtocolModel()
    release = json.loads((ROOT / settings['releaseManifest']).read_text())
    for filename, expected in release['files'].items():
        path = (ROOT / filename).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Release file differs from verified build: ' + filename)
    for name in ('certificate', 'certificateKey'):
        if not Path(settings[name]).is_file():
            raise ValueError('Existing TLS ' + name + ' is unavailable; do not create or replace certificates')
    # The fingerprint contains no recoverable credential values.
    binding = {k: v for k, v in os.environ.items() if k.startswith(('VERA_PROTOCOL_', 'ADP_')) or k in {'VELA_'+n for n in MODEL_VARIABLES}}
    fingerprint = hashlib.sha256(json.dumps([settings, release, binding], sort_keys=True).encode()).hexdigest()
    readiness = {'project': 'VELLA', 'origin': config.origin, 'mode': 'PAPER', 'lending': 'DEMO',
                 'sdkCandidateConfigured': bool(config.client_id), 'sdkRegistrationVerified': False,
                 'factoryConfigured': bool(config.factory), 'modelConfigured': model.ready,
                 'attestorConfigured': bool(config.attestor_key), 'networkReadsEnabled': config.network_enabled,
                 'genuineWalletApproval': 'NOT VERIFIED', 'publicRoutingChanged': False}
    return settings, fingerprint, readiness


def process_identity(pid):
    try:
        proc = Path('/proc') / str(pid)
        fields = (proc / 'stat').read_text().rsplit(') ', 1)[1].split()
        argv = (proc / 'cmdline').read_bytes().split(b'\0')
        return {'start': fields[19], 'cwd': str((proc / 'cwd').resolve()),
                'app': b'autodefi.api:create_app' in argv and b'uvicorn' in argv}
    except (FileNotFoundError, ProcessLookupError):
        return None


def already_running(state, fingerprint):
    identity = process_identity(state['pid'])
    if identity is None:
        return False
    if identity != state['identity'] or identity['cwd'] != str(ROOT) or not identity['app']:
        raise ValueError('Recorded PID belongs to a different process; nothing was stopped')
    if fingerprint != state['fingerprint']:
        raise ValueError('Running preview uses a different release/configuration; no automatic replacement')
    return True


class LocalTLS(http.client.HTTPSConnection):
    def __init__(self, settings):
        super().__init__('defivela.xyz', timeout=4, context=ssl.create_default_context())
        self.settings = settings

    def connect(self):
        raw = socket.create_connection((self.settings['listenHost'], self.settings['listenPort']), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname='defivela.xyz')
        except BaseException:
            raw.close()
            raise


def get(settings, path):
    with closing(LocalTLS(settings)) as connection:
        connection.request('GET', path, headers={'Host': 'defivela.xyz'})
        response = connection.getresponse()
        return response.status, response.read(8_000_000)


def verify(settings):
    directory = settings.get('frontendDirectory', 'site/dist')
    for path in ('/', '/vault'):
        status, body = get(settings, path)
        if status != 200 or b'<title>VELLA</title>' not in body:
            raise ValueError('Preview frontend route failed: ' + path)
        if settings.get('moduleApiVersion') and body != (ROOT / directory / 'index.html').read_bytes():
            raise ValueError('Preview HTML does not match the prepared modular release')
    build = json.loads((ROOT / directory / 'build-manifest.json').read_text())
    entry = build['entry']
    status, body = get(settings, '/' + entry.split('/', 1)[1])
    if status != 200 or hashlib.sha256(body).digest() != hashlib.sha256((ROOT / 'site' / entry).read_bytes()).digest():
        raise ValueError('Preview is not serving the verified frontend asset')
    status, body = get(settings, '/api/health')
    health = json.loads(body)
    if status != 200 or health.get('mode') != 'PAPER' or health.get('lending') != 'DEMO' or health.get('liveEnabled') is not False or health.get('realFunding') is not False:
        raise ValueError('Preview safety health check failed')
    if get(settings, '/api/account')[0] != 401:
        raise ValueError('Private account API is exposed')
    if settings.get('moduleApiVersion'):
        status, body = get(settings, '/api/config')
        config = json.loads(body)
        if (status != 200 or config.get('moduleApiVersion') != 1
                or config.get('network', {}).get('chainId') != 6281971
                or config.get('network', {}).get('testnet') is not True
                or config.get('mode') != 'PAPER' or config.get('lending') != 'DEMO'
                or config.get('liveEnabled') is not False or config.get('realFunding') is not False):
            raise ValueError('Modular API, DogeOS network or simulation enforcement failed')
        for asset in build['outputs']:
            status, body = get(settings, '/' + asset.split('/', 1)[1])
            if status != 200 or body != (ROOT / 'site' / asset).read_bytes():
                raise ValueError('Prepared modular asset is not being served: ' + asset)
    # Use the loopback socket while retaining certificate/hostname verification.
    from websockets.sync.client import connect
    with socket.create_connection((settings['listenHost'], settings['listenPort']), timeout=4) as raw:
        with connect('wss://defivela.xyz/ws/markets', sock=raw, ssl=ssl.create_default_context(),
                     server_hostname='defivela.xyz', origin=settings['origin'], proxy=None,
                     open_timeout=4, close_timeout=2) as ws:
            payload = json.loads(ws.recv(timeout=4))
            if payload.get('privateData') is not False or not isinstance(payload.get('markets'), list):
                raise ValueError('Preview public WebSocket check failed')
    return {'httpsRoutes': 'PASS', 'servedAsset': 'PASS', 'privateApi': 'PASS', 'websocket': 'PASS',
            'liveLighter': 'PASS' if health.get('marketData') else 'NOT VERIFIED',
            'genuineWalletApproval': 'NOT VERIFIED', 'publicRoutingChanged': False}


def launch(settings, fingerprint, *, lock_held=False):
    runtime = ROOT / '.runtime/private-preview'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    statefile = runtime / 'state.json'
    with (runtime / 'activation.lock').open('a') as lock:
        if not lock_held:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if statefile.exists():
            state = json.loads(statefile.read_text())
            if already_running(state, fingerprint):
                return dict(status='ALREADY RUNNING', **verify(settings))
        # Reserve the socket once. Pass that exact descriptor to the child to avoid a port race.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((settings['listenHost'], settings['listenPort']))
            listener.listen(128)
            argv = [str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'autodefi.api:create_app', '--factory',
                    '--fd', str(listener.fileno()), '--workers', '1', '--no-access-log', '--no-proxy-headers',
                    '--ssl-certfile', settings['certificate'], '--ssl-keyfile', settings['certificateKey']]
            environment = dict(os.environ, PYTHONPATH=str(ROOT / 'backend'))
            logfd = os.open(runtime / 'server.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(logfd, 'ab') as log:
                child = subprocess.Popen(argv, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, start_new_session=True, pass_fds=(listener.fileno(),))
            try:
                deadline = time.monotonic() + 30
                while child.poll() is None and time.monotonic() < deadline:
                    try:
                        status, _ = get(settings, '/api/health')
                        if status == 200:
                            break
                    except OSError as exc:
                        if exc.errno in (errno.EPERM, errno.EACCES) or isinstance(exc, ssl.SSLCertVerificationError):
                            raise
                    except http.client.HTTPException:
                        pass
                    time.sleep(.5)
                if child.poll() is not None:
                    raise ValueError('Preview process exited; inspect only its .runtime/private-preview/server.log')
                result = verify(settings)
                identity = process_identity(child.pid)
                if not identity or not identity['app'] or identity['cwd'] != str(ROOT):
                    raise ValueError('Could not confirm preview process ownership')
                state = dict(pid=child.pid, identity=identity, fingerprint=fingerprint)
                temp = statefile.with_suffix('.tmp')
                with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as out:
                    json.dump(state, out)
                os.replace(temp, statefile)
                return dict(status='RUNNING', **result)
            except BaseException:
                # Only the child created by this invocation is eligible for cleanup.
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=4)
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate local prerequisites without sockets or process activation')
    parser.add_argument('--activate-modular', action='store_true',
                        help='Preserve, check and replace only the recorded private preview with the f45b4c0 modular build')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        settings, fingerprint, readiness = prepared(modular=args.activate_modular)
        if args.activate_modular:
            sys.path.insert(0, str(ROOT))
            from tools.modular_preview import activate
            # Pass this module instance also when invoked as a script, for one shared launcher.
            result = activate(sys.modules[__name__], settings, fingerprint, check_only=args.check)
        else:
            result = {'status': 'PREPARED — NOT RUNNING'} if args.check else launch(settings, fingerprint)
        print(json.dumps(dict(readiness, **result, listener='127.0.0.1:8802 (TLS)',
                              browserAccess='Requires a private tunnel/origin mapping for https://defivela.xyz; public DNS still serves old Vela'), indent=2))
        return 0
    except ValueError as exc:
        # Validation errors contain only configuration names and public file paths.
        print('BLOCKED: ' + str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print('BLOCKED: host operation rejected with ' + errno.errorcode.get(exc.errno, 'OS_ERROR')
              + '; no public routing or unrelated service was changed.', file=sys.stderr)
        return 1
    except Exception as exc:
        # Never serialize arbitrary protocol exceptions or credential-bearing responses.
        print('BLOCKED: preview verification failed (' + type(exc).__name__
              + '); no public routing or unrelated service was changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
