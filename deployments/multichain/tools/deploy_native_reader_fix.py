"""Guarded one-file correction of the already-installed backend overlay."""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from tools.deploy_backend import (TARGET, BACKEND, GATEWAY, Host, digest,
    source_fingerprint, atomic_write, ledger_state, assert_idle, assert_continuity)
from tools.build_backend_release import verify

PREVIOUS = '3538d6512705ac8a0aa8abd5a2d16a08accc6b3d91f9f754e83159d4faa8b286'
RELATIVE = 'tools/native_testnet_config.py'
RELEASE = ROOT/'.runtime/releases/noah-backend-d4af3ae3b81d'


def installed():
    verify(RELEASE)
    manifest = json.loads((RELEASE/'manifest.json').read_text())
    for item in manifest['files']:
        path = TARGET/item['path']
        if path.is_symlink() or digest(path.read_bytes()) != item['sha256']:
            raise RuntimeError('Installed overlay differs: '+item['path'])
    if source_fingerprint(TARGET) != PREVIOUS:
        raise RuntimeError('Installed backend source changed; reconcile first')


def deploy(apply=False):
    installed()
    host = Host(TARGET)
    for name, factory in ((BACKEND, 'tools.multichain_handoff:create_app'),
                          (GATEWAY, 'tools.dogeos_readonly:create_app')):
        state = host.state(name)
        if (state.get('ActiveState') != 'active' or int(state.get('MainPID','0')) <= 0
                or state.get('WorkingDirectory') != str(TARGET)
                or factory not in state.get('ExecStart','')):
            raise RuntimeError('Service identity differs')
    host.health(expected=PREVIOUS, ready=True)
    # Observe actual registered contracts before the service is touched.
    from autodefi.config import Config
    from autodefi.chain import DogeOS
    from tools.native_testnet_config import configured
    config = configured(Config(network_enabled=True))
    if config.native_actions_enabled or config.attestor_key or config.reward_factory:
        raise RuntimeError('This patch only restores public native reads')
    native = DogeOS(config).state('0xf8b7120f466f39063398fdc62f5f8cbce37ad1db')
    if not native['vault'] or native['vault']['address'] != '0x533bef9b9453914b584241f53aba40f106ac3125':
        raise RuntimeError('Existing owner vault differs')
    database = TARGET/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    baseline, frontend = ledger_state(database), host.ui_state()
    assert_idle(baseline)
    path = TARGET/RELATIVE
    old, new = path.read_bytes(), (ROOT/RELATIVE).read_bytes()
    if old == new:
        raise RuntimeError('No patch to apply')
    result = dict(deployed=False, previousFingerprint=PREVIOUS, nativeReadVerified=True,
                  changedFile=RELATIVE, sha256=digest(new), realTransactionsSubmitted=0,
                  databaseReplaced=False, nativeActionsEnabled=False, liveProtectionVerified=False)
    if not apply:
        return result
    recovery = TARGET/'.runtime/deployments'/('native-reader-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    recovery.mkdir(mode=0o700, parents=True, exist_ok=False)
    atomic_write(recovery/'native_testnet_config.py.before', old, 0o600)
    lock, changed, stopped = None, False, False
    mode = path.stat().st_mode & 0o777
    try:
        stopped = True
        host.stop()
        lock = open(str(database)+'.lock', 'a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen = ledger_state(database)
        assert_idle(frozen)
        assert_continuity(baseline, frozen)
        installed()
        atomic_write(path, new, mode)
        changed = True
        after = ledger_state(database)
        assert_continuity(frozen, after)
        if after['exposureDigest'] != frozen['exposureDigest'] or host.ui_state() != frontend:
            raise RuntimeError('Unrelated state changed during patch')
        expected = source_fingerprint(TARGET)
        lock.close(); lock = None
        host.start()
        host.accept(expected)
        assert_continuity(frozen, ledger_state(database))
        if host.ui_state() != frontend:
            raise RuntimeError('Frontend changed during acceptance')
        result.update(deployed=True, backendFingerprint=expected, ledgerContinuity=True,
                      healthHttpsWebSocketVerified=True, frontendUnchanged=True)
        return result
    except BaseException:
        if changed:
            host.stop()
            if path.read_bytes() != new:
                raise RuntimeError('Concurrent source change; preserve for reconciliation')
            atomic_write(path, old, mode)
        if lock is not None:
            lock.close(); lock = None
        if stopped:
            host.start()
            host.accept(PREVIOUS)
        result['rolledBack'] = True
        raise
    finally:
        if lock is not None: lock.close()
        atomic_write(recovery/'result.json', (json.dumps(result,indent=2)+'\n').encode(), 0o600)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = deploy(args.apply)
    (ROOT/'verification/noah/native-reader-deployment.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
