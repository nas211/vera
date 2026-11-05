"""Guarded three-file performance patch to the existing backend; no ledger replacement."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from tools.deploy_backend import TARGET,BACKEND,GATEWAY,Host,digest,source_fingerprint,atomic_write,ledger_state,assert_idle,assert_continuity

PREVIOUS='13805f40549563903c87faab1332a3f2b490c13d7fd6796fa9234d9a7f6f20d2'
FILES=('backend/autodefi/api.py','backend/autodefi/native_vault.py','backend/autodefi/vaults.py')
BASE='33ebd98'

def financial_digest(database):
    db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        loans=db.execute('SELECT id,owner,vault,nonce,principal,interest,original,paid,last,apr,state,seized,bad_debt FROM loans ORDER BY id').fetchall()
        cash=db.execute('SELECT address,balance,initial FROM users ORDER BY address').fetchall()
        return digest(json.dumps([loans,cash]).encode())
    finally:db.close()

def expected_fingerprint(changes):
    paths=sorted({p for folder in ('backend','tools','components/why-discovery/backend/discovery') for p in (TARGET/folder).rglob('*.py')})
    result=hashlib.sha256()
    for path in paths:
        name=path.relative_to(TARGET).as_posix()
        result.update(name.encode()+b'\0'+hashlib.sha256(changes.get(name,path.read_bytes())).digest())
    return result.hexdigest()

def deploy(apply=False):
    if source_fingerprint(TARGET)!=PREVIOUS:raise RuntimeError('Installed source changed; reconcile first')
    old,new,modes={},{},{}
    for name in FILES:
        path=TARGET/name
        if path.is_symlink():raise RuntimeError('Redirected runtime source')
        old[name]=path.read_bytes();new[name]=(ROOT/name).read_bytes();modes[name]=path.stat().st_mode&0o777
        if old[name]!=subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT):raise RuntimeError('Source preimage differs: '+name)
        if old[name]==new[name]:raise RuntimeError('Missing proposed change: '+name)
    expected=expected_fingerprint(new)
    host=Host(TARGET)
    for name,factory in ((BACKEND,'tools.multichain_handoff:create_app'),(GATEWAY,'tools.dogeos_readonly:create_app')):
        state=host.state(name)
        if state.get('ActiveState')!='active' or state.get('WorkingDirectory')!=str(TARGET) or factory not in state.get('ExecStart',''):raise RuntimeError('Service identity differs')
    host.health(expected=PREVIOUS,ready=True)
    database=TARGET/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    before,frontend=ledger_state(database),host.ui_state();assert_idle(before)
    config=host.fetch('https://defivela.xyz/api/config')
    bindings={k:config.get(k) for k in ('nativeVaultActionsEnabled','nativeWorkflowGatewayEnabled','attestorConfigured','nativeRewardDeployment')}
    result=dict(deployed=False,previousFingerprint=PREVIOUS,backendFingerprint=expected,
                files={n:dict(before=digest(old[n]),after=digest(new[n])) for n in FILES},
                databaseReplaced=False,transactionsSent=False,liveProtectionVerified=False)
    if not apply:return result
    recovery=TARGET/'.runtime/deployments'/('native-speed-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    recovery.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in FILES:atomic_write(recovery/(Path(name).name+'.before'),old[name],0o600)
    lock=None;changed=[];stopped=False
    try:
        stopped=True;host.stop()
        lock=open(str(database)+'.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        frozen=ledger_state(database);assert_idle(frozen);assert_continuity(before,frozen)
        finances=financial_digest(database)
        if source_fingerprint(TARGET)!=PREVIOUS:raise RuntimeError('Concurrent source change')
        for name in FILES:
            changed.append(name);atomic_write(TARGET/name,new[name],modes[name])
        if source_fingerprint(TARGET)!=expected or financial_digest(database)!=finances:raise RuntimeError('Patch identity or ledger changed')
        assert_continuity(frozen,ledger_state(database))
        lock.close();lock=None;host.start();host.accept(expected)
        config=host.fetch('https://defivela.xyz/api/config')
        if {k:config.get(k) for k in bindings}!=bindings or host.ui_state()!=frontend:raise RuntimeError('Bindings or frontend changed')
        assert_continuity(frozen,ledger_state(database))
        result.update(deployed=True,ledgerContinuity=True,financialLedgerPreservedDuringPatch=True,
                      healthHttpsWebSocketVerified=True,nativeBindingsUnchanged=True,frontendUnchanged=True)
        return result
    except BaseException:
        if changed:
            host.stop()
            if any((TARGET/n).read_bytes() not in (old[n],new[n]) for n in changed):raise RuntimeError('Concurrent code change; preserve for reconciliation')
            for name in changed:atomic_write(TARGET/name,old[name],modes[name])
        if lock is not None:lock.close();lock=None
        if stopped:host.start();host.accept(PREVIOUS)
        result['rolledBack']=True
        raise
    finally:
        if lock is not None:lock.close()
        atomic_write(recovery/'result.json',(json.dumps(result,indent=2)+'\n').encode(),0o600)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    report=deploy(args.apply)
    (ROOT/'verification/noah/native-speed-backend-deployment.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
