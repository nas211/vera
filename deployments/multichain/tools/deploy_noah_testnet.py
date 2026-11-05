"""Install the Vela app and connect the existing public entry routes."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_noah_testnet import verify, PREFIX

SITE = Path('/etc/nginx/sites-available/defivela.xyz')
BEGIN = '    # BEGIN VELA APP\n'
END = '    # END VELA APP\n'
OLD_BEGIN = '    # BEGIN VELA NOAH TESTNET\n'
OLD_END = '    # END VELA NOAH TESTNET\n'
ANCHOR = '    location / {\n        proxy_pass http://127.0.0.1:8805;'
ENTRY_ROUTES = {'vault':'/vault', 'borrow':'/borrow', 'trading':'/trading', 'repay':'/borrow', 'overview':'/'}
REDIRECTS = '\nlocation = /noah { return 302 /app/$is_args$args; }\nlocation ^~ /noah/ { return 302 /app/$is_args$args; }\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(path):
    with urllib.request.urlopen('https://defivela.xyz'+path, timeout=20) as response:
        return response.read(), dict(response.headers)


def block(fragment, begin=BEGIN, end=END):
    return begin+'\n'.join('    '+line if line else '' for line in fragment.splitlines())+'\n'+end+'\n'


def render(current, fragment, legacy_fragment=None, previous_fragment=None):
    if previous_fragment is not None:
        old = block(previous_fragment+REDIRECTS)
        if current.count(old) != 1 or current.count(BEGIN) != 1 or current.count(END) != 1:
            raise RuntimeError('Installed app routing differs from the previous release')
        return current.replace(old, block(fragment+REDIRECTS), 1)
    if OLD_BEGIN in current or OLD_END in current:
        old = block(legacy_fragment or '', OLD_BEGIN, OLD_END)
        if current.count(old) != 1:
            raise RuntimeError('Existing staged routing differs; reconcile first')
        current = current.replace(old, '', 1)
    if BEGIN in current or END in current or '/noah' in current or 'location = /app' in current:
        raise RuntimeError('Existing app routing requires reconciliation')
    if current.count(ANCHOR) != 1 or current.count('listen 443 ssl;') != 1:
        raise RuntimeError('Unexpected existing HTTPS routing')
    for entry, route in ENTRY_ROUTES.items():
        pattern = r'    location = /'+entry+r' \{\n.*?\n    \}\n'
        replacement = '    location = /'+entry+' { return 302 "/app/$is_args$args#'+route+'"; }\n'
        current, count = re.subn(pattern, lambda _:replacement, current, flags=re.DOTALL)
        if count != 1:
            raise RuntimeError('Existing entry route differs: '+entry)
    addition = block(fragment+REDIRECTS)
    return current.replace(ANCHOR, addition+ANCHOR, 1)


def atomic(path, data, mode):
    if path.is_symlink():
        raise RuntimeError('Refuse redirected destination')
    fd, temporary = tempfile.mkstemp(prefix='.noah-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(*command):
    subprocess.run(command, capture_output=True, check=True, timeout=30)


def deploy(release, fingerprint, apply=False, previous_release=None):
    verify(release)
    package = json.loads((release/'package.json').read_text())
    identity = package['release']['releaseId']
    directory = Path('/var/www/defivela-app')/identity
    if SITE.is_symlink() or directory.is_symlink() or directory.parent.is_symlink():
        raise RuntimeError('Redirected deployment destination')
    if directory.exists():
        staged = {str(p.relative_to(directory)):sha(p.read_bytes()) for p in directory.rglob('*') if p.is_file()}
        if any(p.is_symlink() for p in directory.rglob('*')) or staged != package['files']:
            raise RuntimeError('Preserve different existing staged files')
    before = SITE.read_bytes()
    legacy = ROOT/'.runtime/releases/noah-testnet-c93ed7b6b417-hosting-v2'
    legacy_manifest = json.loads((legacy/'package.json').read_text())
    legacy_fragment = (legacy/'nginx-testnet.conf.pending').read_bytes()
    if sha(legacy_fragment) != legacy_manifest['nginxSha256']:
        raise RuntimeError('Historical hosting fragment changed')
    previous_fragment = None
    if previous_release is not None:
        verify(previous_release)
        previous = json.loads((previous_release/'package.json').read_text())
        previous_fragment = (previous_release/'nginx-testnet.conf.pending').read_text()
        for name, expected in previous['files'].items():
            if sha(fetch(PREFIX+('' if name=='vela.html' else name))[0]) != expected:
                raise RuntimeError('Installed app differs from the previous package: '+name)
    proposed = render(before.decode(), (release/'nginx-testnet.conf.pending').read_text(), legacy_fragment.decode(), previous_fragment).encode()
    baseline = {'/':sha(fetch('/')[0])}
    health = json.loads(fetch('/api/health')[0])
    if (health.get('sourceFingerprint') != fingerprint or health.get('mode') != 'PAPER'
            or health.get('lending') != 'DEMO' or health.get('liveEnabled') is not False
            or health.get('realFunding') is not False or not health.get('marketData')
            or health.get('runtimeError')):
        raise RuntimeError('Backend release health differs')
    run('nginx', '-t')
    result = dict(releaseId=identity, preflightPassed=True, websiteDeployed=False,
                  backendFingerprint=fingerprint, liveProtectionVerified=False,
                  livePayoutsVerified=False, walletSignedByThisCommand=False, contractsDeployedByThisCommand=False)
    if not apply:
        return result
    directory.mkdir(parents=True, mode=0o755, exist_ok=True)
    os.chmod(directory.parent, 0o755)
    for name, expected in package['files'].items():
        data = (release/'website'/name).read_bytes()
        if sha(data) != expected:
            raise RuntimeError('Artifact changed during installation')
        target = directory/name
        target.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        atomic(target, data, 0o644)
    mode = SITE.stat().st_mode & 0o777
    changed = False
    try:
        if SITE.read_bytes() != before:
            raise RuntimeError('Routing changed during preparation')
        atomic(SITE, proposed, mode)
        changed = True
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        # Reload starts new workers asynchronously; old workers may briefly
        # answer a fresh request with the previous routing configuration.
        deadline = time.monotonic()+10
        while True:
            try:
                if sha(fetch(PREFIX)[0]) == package['files']['vela.html']:
                    break
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            if time.monotonic() >= deadline:
                raise RuntimeError('New routing did not become available')
            time.sleep(.25)
        for name, expected in package['files'].items():
            body, headers = fetch(PREFIX+('' if name=='vela.html' else name))
            if sha(body) != expected:
                raise RuntimeError('Published artifact differs: '+name)
            if headers.get('X-Content-Type-Options') != 'nosniff':
                raise RuntimeError('Missing static security header')
        if {route:sha(fetch(route)[0]) for route in baseline} != baseline:
            raise RuntimeError('Existing homepage changed')
        for entry in ENTRY_ROUTES:
            if sha(fetch('/'+entry)[0]) != package['files']['vela.html']:
                raise RuntimeError('Old entry does not reach the new app: '+entry)
        result.update(websiteDeployed=True, url='https://defivela.xyz/app/',
                      servedFilesVerified=len(package['files']), homepageUnchanged=True,
                      connectedEntryRoutes=ENTRY_ROUTES,
                      nginxBeforeSha256=sha(before), nginxAfterSha256=sha(proposed),
                      nativeActivationPending=json.loads(fetch('/api/config')[0]).get('nativeVaultActionsEnabled') is not True,
                      browserAuditVerified=False)
        return result
    except BaseException:
        if changed:
            if SITE.read_bytes() != proposed:
                raise RuntimeError('Concurrent routing change; preserve for reconciliation')
            atomic(SITE, before, mode)
            run('nginx', '-t')
            run('systemctl', 'reload', 'nginx')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--backend-fingerprint', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--previous-release', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = deploy(args.release.resolve(), args.backend_fingerprint, args.apply,
                    args.previous_release.resolve() if args.previous_release else None)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
