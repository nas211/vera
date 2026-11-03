#!/usr/bin/env python3
"""Validate the existing multichain artifact. No rebuild or app restart."""
import argparse,datetime,hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if ROOT!=Path('/root/VellaMultichain'):raise SystemExit('Isolated multichain worktree required')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--build',choices=['multichain','candidate'],default='multichain')
args=parser.parse_args()
runtime=ROOT/'.runtime/multichain';runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
report=dict(startedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='RUNNING',tests={},
            scope='Chromium fixtures only. No genuine wallet, testnet transaction or live AI verification.',
            processActions='None; fixture tests only',rebuilt=False,deployed=False,liveProtectionVerified=False,
            artifactDirectory=args.build,artifactManifestSha256=hashlib.sha256((ROOT/'site'/args.build/'build-manifest.json').read_bytes()).hexdigest(),
            sourceCommit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            uncommittedApplicationSources=subprocess.check_output(['git','diff','--name-only','--','site/src','backend'],cwd=ROOT,text=True).splitlines())
def save():
    tmp=runtime/'host-validation.tmp';tmp.write_text(json.dumps(report,indent=2)+'\n');tmp.replace(runtime/'host-validation.json')
save()
for name,cmd,extra in [
    ('dogeosBrowser',['node','checks/modular-browser.mjs'],{'VELLA_BROWSER_BUILD':args.build}),
    ('multichainBrowser',['node','checks/multichain-browser.mjs'],{'VELLA_BROWSER_BUILD':args.build}),
]:
    resultfile=runtime/(name+'-result.json');resultfile.unlink(missing_ok=True)
    extra=dict(extra,VELLA_BROWSER_REPORT=str(resultfile))
    with (runtime/(name+'.log')).open('w') as log:
        try:
            p=subprocess.run(cmd,cwd=ROOT/'site',env=dict(os.environ,**extra),stdout=log,stderr=subprocess.STDOUT,timeout=900)
            detail=json.loads(resultfile.read_text()) if resultfile.is_file() else {}
            status='PASS' if p.returncode==0 and detail.get('status')=='PASS' else 'BLOCKED' if detail.get('status')=='BLOCKED' else 'FAIL'
            report['tests'][name]=dict(status=status,exitCode=p.returncode,log=str(runtime/(name+'.log')),result=str(resultfile))
        except subprocess.TimeoutExpired:report['tests'][name]=dict(status='TIMEOUT',log=str(runtime/(name+'.log')))
    save()
statuses={v['status'] for v in report['tests'].values()}
report['status']='PASS' if statuses=={'PASS'} else 'BLOCKED' if statuses<={'PASS','BLOCKED'} else 'FAIL'
report['finishedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
print(json.dumps(report,indent=2))
sys.exit(0 if report['status']=='PASS' else 1)
