"""Deploy Start Trading paper permission and its read-only native lending routes, requiring an idle PAPER ledger.

Exact source preimages, no open positions or pending orders, financial/exposure continuity and code rollback.
No database copies, wallet transactions, contract deployments or service config edits.
"""
import argparse
import fcntl
import json
import re
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from tools.deploy_backend import TARGET,BACKEND,GATEWAY,Host,digest,source_fingerprint,atomic_write,ledger_state,assert_idle,assert_continuity
from tools.deploy_reward_migration import financial_digest,expected_fingerprint

class CurrentHost(Host):
    def ui_state(self):
        nginx=Path('/etc/nginx/sites-available/defivela.xyz').read_bytes()
        roots=set(re.findall(rb'root\s+(/var/www/defivela-(?:app|home|ui)/[^\s;]+);',nginx))
        if not roots:raise RuntimeError('Current frontend roots not recognized')
        result={'nginx':digest(nginx)}
        for raw in roots:
            root=Path(raw.decode())
            for path in sorted(root.rglob('*')):
                if path.is_file():result[str(path)]=digest(path.read_bytes())
        return result

PREVIOUS='1633ef6b53628843694a6df029e8bdecb2f2287ed28092a1edd23f212b0ac7fa'
PREIMAGES={'backend/autodefi/api.py': '9c4ff25c2193306306fdce3b9cd55b12a91c35d0570bc4c8cecd27b83d7f5374', 'backend/autodefi/trading.py': '5fb78341b6842b1b51107973244050bf62a3673889995afb6e5abea51cbea4b0', 'backend/autodefi/doge_lending.py': None, 'config/doge-lending.json': None, 'config/TestDogeLending.abi.json': None}

def read_files(root):
    result={}
    for name in PREIMAGES:
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):raise RuntimeError('Redirected source path')
        result[name]=p.read_bytes() if p.exists() else None
    return result

def check_previous():
    old=read_files(TARGET)
    if source_fingerprint(TARGET)!=PREVIOUS or {n:digest(b) if b is not None else None for n,b in old.items()}!=PREIMAGES:
        raise RuntimeError('Installed source or policy changed; reconcile before deployment')
    return old

def prepare(release):
    old=check_previous();new=read_files(ROOT)
    if any(old[n]==new[n] for n in PREIMAGES):raise RuntimeError('Missing reviewed policy change')
    fingerprint=expected_fingerprint({n:b for n,b in new.items() if n.endswith('.py')})
    manifest=dict(previousFingerprint=PREVIOUS,backendFingerprint=fingerprint,preimages=PREIMAGES,
                  files={n:digest(b) for n,b in new.items()},risk=json.loads((TARGET/'config/risk.json').read_text()),
                  deployed=False,databaseReplaced=False,transactionsSent=False,liveProtectionVerified=False)
    release.mkdir(mode=0o700,parents=True,exist_ok=False)
    for label,contents in [('source',new),('rollback',old)]:
        for name,data in contents.items():
            if data is not None:atomic_write(release/label/name,data)
    atomic_write(release/'manifest.json',(json.dumps(manifest,indent=2)+'\n').encode(),0o600)
    return manifest

def verify(release):
    manifest=json.loads((release/'manifest.json').read_text())
    if manifest.get('preimages')!=PREIMAGES or manifest.get('previousFingerprint')!=PREVIOUS:raise RuntimeError('Different release baseline')
    new,old=read_files(release/'source'),read_files(release/'rollback')
    if {n:digest(b) for n,b in new.items()}!=manifest['files'] or {n:digest(b) if b is not None else None for n,b in old.items()}!=PREIMAGES:
        raise RuntimeError('Release source checksum mismatch')
    if json.loads((TARGET/'config/risk.json').read_text())!=manifest['risk']:raise RuntimeError('Policy changed')
    return manifest,new,old

def deploy(release,apply=False):
    manifest,new,old=verify(release);check_previous()
    if expected_fingerprint({n:b for n,b in new.items() if n.endswith('.py')})!=manifest['backendFingerprint']:
        raise RuntimeError('Runtime fingerprint changed')
    host=CurrentHost(TARGET)
    for name,factory in ((BACKEND,'tools.multichain_handoff:create_app'),(GATEWAY,'tools.dogeos_readonly:create_app')):
        state=host.state(name)
        if state.get('ActiveState')!='active' or state.get('WorkingDirectory')!=str(TARGET) or factory not in state.get('ExecStart',''):raise RuntimeError('Service identity differs')
    host.health(expected=PREVIOUS,ready=True)
    database=TARGET/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    before,frontend=ledger_state(database),host.ui_state();assert_idle(before)
    config=host.fetch('https://defivela.xyz/api/config')
    bindings={k:config.get(k) for k in ('nativeVaultActionsEnabled','nativeWorkflowGatewayEnabled','attestorConfigured','nativeRewardDeployment')}
    result=dict(deployed=False,preflightPassed=True,backendFingerprint=manifest['backendFingerprint'],risk=manifest['risk'],
                riskSha256=digest((TARGET/'config/risk.json').read_bytes()),databaseReplaced=False,transactionsSent=False,liveProtectionVerified=False)
    if not apply:return result
    recovery=TARGET/'.runtime/deployments'/('paper-start-permission-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    recovery.mkdir(mode=0o700,parents=True,exist_ok=False)
    modes={n:(TARGET/n).stat().st_mode&0o777 if (TARGET/n).exists() else 0o644 for n in PREIMAGES}
    changed=[];lock=None;stopped=False
    try:
        stopped=True;host.stop()
        lock=open(str(database)+'.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        frozen=ledger_state(database);assert_idle(frozen);assert_continuity(before,frozen)
        finances=financial_digest(database);exposure=frozen['exposureDigest'];check_previous()
        for name,data in new.items():changed.append(name);atomic_write(TARGET/name,data,modes[name])
        if source_fingerprint(TARGET)!=manifest['backendFingerprint'] or financial_digest(database)!=finances or read_files(TARGET)!=new or ledger_state(database)['exposureDigest']!=exposure:
            raise RuntimeError('Source or financial continuity check failed')
        assert_continuity(frozen,ledger_state(database))
        lock.close();lock=None;host.start();host.accept(manifest['backendFingerprint'])
        after=host.fetch('https://defivela.xyz/api/config')
        if after.get('risk')!=manifest['risk'] or after.get('chain',{}).get('assets',[{}])[0].get('illustrativePrice')!='1.00':raise RuntimeError('Published borrowing policy differs')
        if {k:after.get(k) for k in bindings}!=bindings or host.ui_state()!=frontend:raise RuntimeError('Bindings or frontend changed')
        assert_continuity(frozen,ledger_state(database))
        result.update(deployed=True,ledgerContinuity=True,financialLedgerPreservedDuringPatch=True,noOpenPositionsAtRestart=True,healthHttpsWebSocketVerified=True)
        return result
    except BaseException:
        if changed:
            host.stop()
            if any((TARGET/n).read_bytes() not in (old[n],new[n]) for n in changed):raise RuntimeError('Concurrent source change; preserve for reconciliation')
            for name in changed:
                if old[name] is None:(TARGET/name).unlink()
                else:atomic_write(TARGET/name,old[name],modes[name])
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
