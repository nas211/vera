"""Publish the read-only market API or new homepage without restarting trading."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.deploy_noah_testnet import SITE, atomic, block, fetch, run, sha

SERVICE='vela-doge-lending-readonly.service'
UNIT=Path('/etc/systemd/system')/SERVICE
ANCHOR='    location / {\n        proxy_pass http://127.0.0.1:8805;'

def pid():
    return subprocess.check_output(['systemctl','show','vela-multichain-candidate.service','-p','MainPID','--value'],text=True).strip()

def until(check):
    deadline=time.monotonic()+10
    while True:
        try:
            if check():return
        except Exception:
            if time.monotonic()>=deadline:raise
        if time.monotonic()>=deadline:raise RuntimeError('Published route did not match prepared content')
        time.sleep(.3)

def reader_fragment():
    return block('''location ^~ /api/doge-lending/ {
    proxy_pass http://127.0.0.1:8809;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Wallet-Address $http_x_wallet_address;
    proxy_set_header Authorization "";
    proxy_set_header Cookie "";
    proxy_connect_timeout 3s;
    proxy_read_timeout 30s;
    client_max_body_size 1k;
    limit_except GET { deny all; }
}
''',begin='    # BEGIN VELA DOGE READER\n',end='    # END VELA DOGE READER\n')

def reader_unit():
    return f'''[Unit]
Description=Vela public test DOGE market observations (read only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={ROOT}/.venv/bin/python -m uvicorn autodefi.doge_lending:readonly_app --app-dir {ROOT}/backend --host 127.0.0.1 --port 8809 --no-access-log
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
InaccessiblePaths=-/root/.ssh -/root/.codex -/root/Vera -/root/VellaMultichain/.runtime

[Install]
WantedBy=multi-user.target
'''

def publish(stage,apply):
    before=SITE.read_bytes();original_pid=pid();mode=SITE.stat().st_mode&0o777
    if original_pid in ('','0'):raise RuntimeError('Trading runtime not running')
    if before.decode().count(ANCHOR)!=1:raise RuntimeError('Unexpected routing anchor')
    home_before=sha(fetch('/')[0]);app_before=sha(fetch('/app/')[0])
    reader_config=json.loads((ROOT/'config/doge-lending.json').read_text())
    if stage=='reader':
        fragment=reader_fragment();unit=reader_unit()
        if '    # BEGIN VELA DOGE READER\n' in before.decode():raise RuntimeError('Reader already routed; reconcile before updating')
        if UNIT.exists():raise RuntimeError('Preserve existing reader service')
        proposed=before.decode().replace(ANCHOR,fragment+ANCHOR,1).encode()
        def check():return json.loads(fetch('/api/doge-lending/config')[0])['enabled']==reader_config['enabled']
    else:
        from tools.build_noah_testnet import verify
        package=ROOT/'.runtime/releases/vela-simple-doge-20261106-ready';verify(package)
        manifest=json.loads((package/'package.json').read_text())
        if sha(fetch('/app/')[0])!=manifest['files']['vela.html']:raise RuntimeError('New app must be verified before homepage cutover')
        home=(ROOT/'design/vela-home.html').read_bytes();home_hash=sha(home)
        directory=Path('/var/www/defivela-home')/('simple-doge-'+home_hash[:12])
        kit=(ROOT/'verification/noah/vela-simple-noah-update.zip').read_bytes()
        kit_directory=Path('/var/www/defivela-submission')/('noah-update-'+sha(kit)[:12])
        fragment=block(f'''location = / {{
    root {directory};
    try_files /index.html =404;
    default_type text/html;
    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy "same-origin" always;
    add_header Content-Security-Policy "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'" always;
}}
location = /submission/vela-noah-update.zip {{
    alias {kit_directory}/vela-noah-update.zip;
    default_type application/zip;
    add_header X-Content-Type-Options nosniff always;
    add_header Content-Disposition 'attachment; filename="vela-noah-update.zip"';
    add_header Cache-Control "no-store" always;
}}
location = /submission/noah-update.txt {{
    alias {kit_directory}/noah-update.txt;
    default_type text/plain;
    add_header X-Content-Type-Options nosniff always;
    add_header Cache-Control "no-store" always;
}}
''',begin='    # BEGIN VELA SIMPLE HOME\n',end='    # END VELA SIMPLE HOME\n')
        if '    # BEGIN VELA SIMPLE HOME\n' in before.decode():raise RuntimeError('Homepage already changed; reconcile first')
        start='    # BEGIN VELA CONCEPT PREVIEW\n';end='    # END VELA CONCEPT PREVIEW\n'
        if before.decode().count(start)!=1 or before.decode().count(end)!=1:raise RuntimeError('Unexpected concept routing')
        a,rest=before.decode().split(start);_,b=rest.split(end)
        redirects=block('''location = /concept { return 302 /$is_args$args; }
location = /concept/ { return 302 /$is_args$args; }
location = /concept/app { return 302 /app/$is_args$args; }
location = /concept/app/ { return 302 /app/$is_args$args; }
''',begin=start,end=end)
        current=a+redirects+b
        old_home=re.search(r'    location = / \{\n.*?\n    \}\n',current,re.DOTALL)
        if not old_home or 'root /var/www/defivela-ui/simple-tabs-20261101T085948Z;' not in old_home[0]:
            raise RuntimeError('Existing homepage route differs; preserve it')
        current=current.replace(old_home[0],fragment,1)
        how=re.search(r'    location = /how-it-works \{\n.*?\n    \}\n',current,re.DOTALL)
        if not how or 'root /var/www/defivela-ui/simple-tabs-20261101T085948Z;' not in how[0]:
            raise RuntimeError('Existing information route differs')
        proposed=current.replace(how[0],'    location = /how-it-works { return 302 "/$is_args$args#how-it-works"; }\n',1).encode()
        def check():return sha(fetch('/')[0])==home_hash
    run('nginx','-t')
    result=dict(stage=stage,preflightPassed=True,deployed=False,backendRestarted=False,liveProtectionChanged=False,
                nativeMarketEnabled=reader_config['enabled'],walletTransactionsSent=False)
    if not apply:return result
    if stage=='reader':
        atomic(UNIT,unit.encode(),0o644);run('systemctl','daemon-reload');run('systemctl','enable','--now',SERVICE)
        def local():
            with urllib.request.urlopen('http://127.0.0.1:8809/api/doge-lending/config',timeout=3) as response:
                return json.load(response)['enabled']==reader_config['enabled']
        until(local)
    else:
        directory.mkdir(mode=0o755,parents=True,exist_ok=True)
        destination=directory/'index.html'
        if destination.exists() and destination.read_bytes()!=home:raise RuntimeError('Preserve existing homepage artifact')
        atomic(destination,home,0o644)
        kit_directory.mkdir(mode=0o755,parents=True,exist_ok=True)
        atomic(kit_directory/'vela-noah-update.zip',kit,0o644)
        atomic(kit_directory/'noah-update.txt',(ROOT/'noah/vela-simple-lending/01-UPDATE-EXISTING-NOAH-PROJECT.txt').read_bytes(),0o644)
    changed=False
    try:
        if SITE.read_bytes()!=before:raise RuntimeError('Concurrent routing change')
        atomic(SITE,proposed,mode);changed=True
        run('nginx','-t');run('systemctl','reload','nginx');until(check)
        if pid()!=original_pid:raise RuntimeError('Trading process changed during publication')
        if sha(fetch('/app/')[0])!=app_before:raise RuntimeError('Unexpected app change')
        if stage=='reader' and sha(fetch('/')[0])!=home_before:raise RuntimeError('Unexpected homepage change')
        if stage=='home':
            if sha(fetch('/concept')[0])!=home_hash or sha(fetch('/concept/app')[0])!=app_before:raise RuntimeError('Concept redirects differ')
            if sha(fetch('/submission/vela-noah-update.zip')[0])!=sha(kit):raise RuntimeError('Noah kit differs')
        result.update(deployed=True,tradingPidUnchanged=True,nginxBeforeSha256=sha(before),nginxAfterSha256=sha(proposed))
        return result
    except BaseException:
        if changed and SITE.read_bytes()==proposed:
            atomic(SITE,before,mode);run('nginx','-t');run('systemctl','reload','nginx')
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['reader','home']);p.add_argument('--apply',action='store_true');p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    result=publish(args.stage,args.apply);args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
