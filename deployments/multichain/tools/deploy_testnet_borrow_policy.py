"""Package and apply the reviewed demo lending policy to the existing testnet app.

Exact source preimages, idle ledger, financial continuity and code rollback only.
No database copies, wallet transactions, contract deployments or service config edits.
"""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from tools.deploy_backend import TARGET,BACKEND,GATEWAY,Host,digest,source_fingerprint,atomic_write,ledger_state,assert_idle,assert_continuity
from tools.deploy_reward_migration import financial_digest,expected_fingerprint

PREVIOUS='259ee8231786e3bbc898adcdcda58852319c41f3ddee59b1b5c1d75e50a66582'
PREIMAGES={
    'backend/autodefi/adapters.py':'1ef00222e1aded4cbef0524afdc61d070091873286ce8ef33e4404b5ff4e57f5',
    'backend/autodefi/lending.py':'568845b8b3fc6fd0c5be4c8763f12a8e8be3f11c4dff42476b30837a324cc321',
    'backend/autodefi/risk.py':'47227db27f81a9bccdbad1fee24bca0a914ad2c3fe902012214455e18f7f5ab4',
    'backend/vela/lighter/engine.py':'2619cd579ec60e79895f2137986e36de1dd0903b53106fee50975c8a0056098b',
    'config/risk.json':'73c7b8eb8212d3d99330cfe110e0a1097cd98fea4629f3b3164e62d21cf8664e',
}

def read_files(root):
    result={}
    for name in PREIMAGES:
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):raise RuntimeError('Redirected source path')
        result[name]=p.read_bytes()
    return result

def check_previous():
    old=read_files(TARGET)
    if source_fingerprint(TARGET)!=PREVIOUS or {n:digest(b) for n,b in old.items()}!=PREIMAGES:
        raise RuntimeError('Installed source or policy changed; reconcile before deployment')
    return old

def prepare(release):
    old=check_previous();new=read_files(ROOT)
    if any(old[n]==new[n] for n in PREIMAGES):raise RuntimeError('Missing reviewed policy change')
    fingerprint=expected_fingerprint({n:b for n,b in new.items() if n.endswith('.py')})
    manifest=dict(previousFingerprint=PREVIOUS,backendFingerprint=fingerprint,preimages=PREIMAGES,
                  files={n:digest(b) for n,b in new.items()},risk=json.loads(new['config/risk.json']),
                  deployed=False,databaseReplaced=False,transactionsSent=False,liveProtectionVerified=False)
    release.mkdir(mode=0o700,parents=True,exist_ok=False)
    for label,contents in [('source',new),('rollback',old)]:
        for name,data in contents.items():atomic_write(release/label/name,data)
    atomic_write(release/'manifest.json',(json.dumps(manifest,indent=2)+'\n').encode(),0o600)
    return manifest

def verify(release):
    manifest=json.loads((release/'manifest.json').read_text())
    if manifest.get('preimages')!=PREIMAGES or manifest.get('previousFingerprint')!=PREVIOUS:raise RuntimeError('Different release baseline')
    new,old=read_files(release/'source'),read_files(release/'rollback')
    if {n:digest(b) for n,b in new.items()}!=manifest['files'] or {n:digest(b) for n,b in old.items()}!=PREIMAGES:
        raise RuntimeError('Release source checksum mismatch')
    if json.loads(new['config/risk.json'])!=manifest['risk']:raise RuntimeError('Policy checksum mismatch')
    return manifest,new,old

def deploy(release,apply=False):
    manifest,new,old=verify(release);check_previous()
    if expected_fingerprint({n:b for n,b in new.items() if n.endswith('.py')})!=manifest['backendFingerprint']:
        raise RuntimeError('Runtime fingerprint changed')
    host=Host(TARGET)
    for name,factory in ((BACKEND,'tools.multichain_handoff:create_app'),(GATEWAY,'tools.dogeos_readonly:create_app')):
        state=host.state(name)
        if state.get('ActiveState')!='active' or state.get('WorkingDirectory')!=str(TARGET) or factory not in state.get('ExecStart',''):raise RuntimeError('Service identity differs')
    host.health(expected=PREVIOUS,ready=True)
    database=TARGET/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    before,frontend=ledger_state(database),host.ui_state();assert_idle(before)
    config=host.fetch('https://defivela.xyz/api/config')
    bindings={k:config.get(k) for k in ('nativeVaultActionsEnabled','nativeWorkflowGatewayEnabled','attestorConfigured','nativeRewardDeployment')}
    result=dict(deployed=False,preflightPassed=True,backendFingerprint=manifest['backendFingerprint'],risk=manifest['risk'],
                riskSha256=manifest['files']['config/risk.json'],databaseReplaced=False,transactionsSent=False,liveProtectionVerified=False)
    if not apply:return result
    recovery=TARGET/'.runtime/deployments'/('borrow-policy-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    recovery.mkdir(mode=0o700,parents=True,exist_ok=False)
    modes={n:(TARGET/n).stat().st_mode&0o777 for n in PREIMAGES}
    changed=[];lock=None;stopped=False
    try:
        stopped=True;host.stop()
        lock=open(str(database)+'.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        frozen=ledger_state(database);assert_idle(frozen);assert_continuity(before,frozen)
        finances=financial_digest(database);check_previous()
        for name,data in new.items():changed.append(name);atomic_write(TARGET/name,data,modes[name])
        if source_fingerprint(TARGET)!=manifest['backendFingerprint'] or financial_digest(database)!=finances or read_files(TARGET)!=new:
            raise RuntimeError('Source or financial continuity check failed')
        assert_continuity(frozen,ledger_state(database))
        lock.close();lock=None;host.start();host.accept(manifest['backendFingerprint'])
        after=host.fetch('https://defivela.xyz/api/config')
        if after.get('risk')!=manifest['risk'] or after.get('chain',{}).get('assets',[{}])[0].get('illustrativePrice')!='1.00':raise RuntimeError('Published borrowing policy differs')
        if {k:after.get(k) for k in bindings}!=bindings or host.ui_state()!=frontend:raise RuntimeError('Bindings or frontend changed')
        assert_continuity(frozen,ledger_state(database))
        result.update(deployed=True,ledgerContinuity=True,financialLedgerPreservedDuringPatch=True,healthHttpsWebSocketVerified=True)
        return result
    except BaseException:
        if changed:
            host.stop()
            if any((TARGET/n).read_bytes() not in (old[n],new[n]) for n in changed):raise RuntimeError('Concurrent source change; preserve for reconciliation')
            for name in changed:atomic_write(TARGET/name,old[name],modes[name])
        if lock is not None:lock.close();lock=None
        if stopped:host.start();host.accept(PREVIOUS)
        result['rolledBack']=True
        raise
    finally:
        if lock is not None:lock.close()
        atomic_write(recovery/'result.json',(json.dumps(result,indent=2)+'\n').encode(),0o600)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','verify','deploy']);parser.add_argument('--release',type=Path,required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if args.command=='prepare':result=prepare(args.release.resolve())
    elif args.command=='verify':result=verify(args.release.resolve())[0]
    else:result=deploy(args.release.resolve(),args.apply)
    print(json.dumps(result))
