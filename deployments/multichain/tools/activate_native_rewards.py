"""Bind verified native contracts and the existing dedicated attestor in place."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from tools.deploy_backend import (TARGET,BACKEND,GATEWAY,Host,atomic_write,
    source_fingerprint,ledger_state,assert_idle,assert_continuity)
from tools.dogeos_readonly import native_ready
from tools.verify_reward_deployment import verify

EXPECTED='13805f40549563903c87faab1332a3f2b490c13d7fd6796fa9234d9a7f6f20d2'
ATTESTOR=TARGET/'.runtime/dogeos-private/attestor.env'
ENVFILE=TARGET/'.runtime/native-rewards.env'
BACKEND_DROP=Path('/etc/systemd/system')/(BACKEND+'.d/70-vela-native-rewards.conf')
GATEWAY_DROP=Path('/etc/systemd/system')/(GATEWAY+'.d/70-vela-native-rewards.conf')


def attest(address):
    # Reuse the target's secure loader in its own process. Only the public
    # derived address leaves that process; no key is copied into an artifact.
    code='''from pathlib import Path
import os
from tools.activate_preview import load_environment
from eth_account import Account
p=Path('.runtime/dogeos-private/attestor.env')
load_environment(p)
keys={line.split('=',1)[0].strip() for line in p.read_text().splitlines() if line.strip() and not line.strip().startswith('#')}
allowed={'ADP_DEMO_ATTESTOR_KEY','VERA_PROTOCOL_DEMO_ATTESTOR_KEY'}
if not keys or not keys<=allowed: raise RuntimeError('Unexpected dedicated attestor settings')
values={os.environ[k] for k in keys}
if len(values)!=1: raise RuntimeError('Conflicting attestor references')
print(Account.from_key(values.pop()).address)
'''
    r=subprocess.run([str(TARGET/'.venv/bin/python'),'-c',code],cwd=TARGET,capture_output=True,text=True,timeout=20)
    if r.returncode or r.stdout.strip().lower()!=address.lower():
        raise RuntimeError('Dedicated attestor verification failed; no secret values disclosed')


def activate(apply=False):
    chain=verify()
    (ROOT/'verification/noah/reward-deployment-verified.json').write_text(json.dumps(chain,indent=2)+'\n')
    if source_fingerprint(TARGET)!=EXPECTED: raise RuntimeError('Backend source changed; reconcile first')
    host=Host(TARGET)
    for name,factory in ((BACKEND,'tools.multichain_handoff:create_app'),(GATEWAY,'tools.dogeos_readonly:create_app')):
        s=host.state(name)
        if s.get('ActiveState')!='active' or s.get('WorkingDirectory')!=str(TARGET) or factory not in s.get('ExecStart',''):
            raise RuntimeError('Existing service identity differs')
    host.health(expected=EXPECTED,ready=True)
    attest(chain['attestor'])
    database=TARGET/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    before,frontend=ledger_state(database),host.ui_state()
    assert_idle(before)
    bindings=dict(chain['bindings'],VERA_PROTOCOL_EXECUTION='PAPER',VERA_PROTOCOL_LENDING='DEMO')
    if any('\n' in value or '\r' in value for value in bindings.values()): raise RuntimeError('Invalid public binding')
    proposed={
        ENVFILE:(''.join(k+'='+v+'\n' for k,v in sorted(bindings.items()))).encode(),
        BACKEND_DROP:('[Service]\nEnvironmentFile='+str(ENVFILE)+'\nEnvironmentFile='+str(ATTESTOR)+'\n').encode(),
        GATEWAY_DROP:b'[Service]\nEnvironment=VELLA_TESTNET_NATIVE_WORKFLOW=1\n',
    }
    for path in proposed:
        if path.is_symlink() or path.parent.is_symlink() or path.exists():
            raise RuntimeError('Preserve an existing or redirected native binding: '+str(path))
    result=dict(activated=False,backendFingerprint=EXPECTED,contractTransaction=chain['transactionHash'],
                factory=chain['factory'],pool=chain['pool'],poolReserveWei=chain['reserveWei'],
                signerReferenceBoundInPlace=True,keysCopied=False,databaseReplaced=False,
                chainTransactionsSent=0,rewardPayoutVerified=False,liveProtectionVerified=False)
    if not apply:return result
    recovery=TARGET/'.runtime/deployments'/('native-activation-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    recovery.mkdir(mode=0o700,parents=True,exist_ok=False)
    changed=[]; lock=None; stopped=False
    try:
        stopped=True;host.stop()
        lock=open(str(database)+'.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        frozen=ledger_state(database);assert_idle(frozen);assert_continuity(before,frozen)
        if source_fingerprint(TARGET)!=EXPECTED: raise RuntimeError('Source changed during activation')
        for path,data in proposed.items():
            if path.exists(): raise RuntimeError('Concurrent binding change')
            atomic_write(path,data,0o600);changed.append(path)
        if ledger_state(database)['exposureDigest']!=frozen['exposureDigest']:
            raise RuntimeError('Exposure changed during activation')
        host.systemctl('daemon-reload')
        lock.close();lock=None
        host.start();host.accept(EXPECTED)
        config=host.fetch('https://defivela.xyz/api/config')
        if not native_ready(config) or config['nativeRewardDeployment']['factory'].lower()!=chain['factory'].lower():
            raise RuntimeError('Published native capability failed acceptance')
        hashes=chain['runtimeHashes'];published=config['nativeRewardDeployment']
        if any(published[k]!=hashes[name] for k,name in [('factoryCodeHash','RewardedVaultFactory'),('poolCodeHash','NativeRewardPool'),('vaultCodeHash','RewardedCollateralVault')]):
            raise RuntimeError('Published runtime bindings differ')
        assert_continuity(frozen,ledger_state(database))
        if host.ui_state()!=frontend: raise RuntimeError('Frontend changed during activation')
        result.update(activated=True,nativeGatewayReady=True,ledgerContinuity=True,frontendUnchanged=True,
                      healthHttpsWebSocketVerified=True)
        return result
    except BaseException:
        if changed:
            host.stop()
            for path in reversed(changed):
                if path.read_bytes()!=proposed[path]: raise RuntimeError('Concurrent native binding change; preserve for reconciliation')
                path.unlink()
            host.systemctl('daemon-reload')
        if lock is not None:lock.close();lock=None
        if stopped:host.start();host.accept(EXPECTED)
        result['rolledBack']=True
        raise
    finally:
        if lock is not None:lock.close()
        atomic_write(recovery/'result.json',(json.dumps(result,indent=2)+'\n').encode(),0o600)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();result=activate(args.apply)
    (ROOT/'verification/noah/native-rewards-activation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
