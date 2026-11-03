"""Read-only acceptance evidence for the existing Vela services. Never restart or trade."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

SERVICES=('vela-testnet-api','vela-testnet-worker','vela-testnet-tunnel',
          'vela-multichain-candidate','vela-dogeos-readonly')
PROPERTIES=('ActiveState','SubState','MainPID','FragmentPath','UnitFileState','Restart','WorkingDirectory')


def inspect():
    result=dict(at=time.time(),readOnly=True,services={},http={},blockers=[],
                deploymentVerified=False,liveProtectionVerified=False)
    for service in SERVICES:
        try:
            command=['systemctl','show',service,'--no-pager']
            for field in PROPERTIES:command.extend(['-p',field])
            completed=subprocess.run(command,capture_output=True,text=True,timeout=5)
            if completed.returncode:raise RuntimeError('service manager unavailable')
            fields=dict(line.split('=',1) for line in completed.stdout.splitlines() if '=' in line)
            result['services'][service]=fields
            if fields.get('ActiveState')!='active':result['blockers'].append(service+' is not active')
            if fields.get('FragmentPath','').startswith('/run/'):result['blockers'].append(service+' uses a transient unit')
            if fields.get('Restart')=='no':result['blockers'].append(service+' has no failure restart policy')
        except Exception as exc:
            result['services'][service]={'available':False,'errorType':type(exc).__name__}
            result['blockers'].append(service+' cannot be inspected')
    for label,url in [('integrated','http://127.0.0.1:8787/api/paper/health'),
                      ('candidate','http://127.0.0.1:8804/api/health'),
                      ('gateway','http://127.0.0.1:8805/api/health')]:
        try:
            request=urllib.request.Request(url,headers={'Host':'defivela.xyz'})
            with urllib.request.urlopen(request,timeout=5) as response:
                body=json.loads(response.read(65536))
                allowed=('mode','lending','liveEnabled','realFunding','marketData','engineReady','runtimeError')
                state={key:body[key] for key in allowed if key in body}
                state['httpStatus']=response.status
            result['http'][label]=state
            if state.get('mode')!='PAPER' or state.get('liveEnabled') is not False:
                result['blockers'].append(label+' simulation guard not verified')
            if state.get('marketData') is not True:result['blockers'].append(label+' market data not fresh')
        except Exception as exc:
            result['http'][label]={'available':False,'errorType':type(exc).__name__}
            result['blockers'].append(label+' HTTP unavailable')
    usage=shutil.disk_usage('/root')
    result['disk']=dict(freeBytes=usage.free,totalBytes=usage.total)
    if usage.free<5*1024**3:result['blockers'].append('Less than 5 GiB free disk space')
    result['note']='Does not approve a restart, prove wallet ownership, send model requests, open ledgers or verify live exchange protection.'
    return result


if __name__=='__main__':
    result=inspect()
    print(json.dumps(result,indent=2))
    raise SystemExit(bool(result['blockers']))
