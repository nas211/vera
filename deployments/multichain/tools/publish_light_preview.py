"""Publish only the isolated colour preview, preserving every live product route."""
import base64,hashlib,json,re,sys,time,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.deploy_noah_testnet import SITE,atomic,block,fetch,run,sha
D=ROOT/'design/previews/vela-light';manifest=json.loads((D/'manifest.json').read_text());files={n:(D/'html'/n).read_bytes() for n in manifest['pages']}
assert {n:sha(d) for n,d in files.items()}==manifest['pages']
checks=json.loads((D/'browser-checks.json').read_text());assert len(checks['results'])==12 and all(x['passed'] for x in checks['results']) and checks['landingCopyAndLayoutPreserved']
identity=sha(json.dumps(manifest['pages'],sort_keys=True).encode())[:12];dest=Path('/var/www/defivela-previews')/('light-'+identity)
script=(D/'preview.js').read_text();hash64=base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
for data in files.values():assert re.findall(r'<script>(.*?)</script>',data.decode(),re.S)==[script]
before=SITE.read_bytes();assert '/preview/light' not in before.decode()
anchor='    location / {\n        proxy_pass http://127.0.0.1:8805;';assert before.decode().count(anchor)==1
baseline={p:sha(fetch(p)[0]) for p in ['/','/app/','/app/trading','/settings']}
def pid():return subprocess.check_output(['systemctl','show','vela-multichain-candidate.service','-p','MainPID','--value'],text=True).strip()
before_pid=pid();assert before_pid not in ('','0')
fragment=block(f'''location = /preview/light {{ return 302 /preview/light/$is_args$args; }}
location ^~ /preview/light/ {{
    alias {dest}/;
    index index.html;
    autoindex off;
    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Content-Security-Policy "default-src 'none'; script-src 'sha256-{hash64}'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'" always;
}}
''',begin='    # BEGIN VELA LIGHT PREVIEW\n',end='    # END VELA LIGHT PREVIEW\n')
proposed=before.decode().replace(anchor,fragment+anchor,1).encode();run('nginx','-t');dest.mkdir(parents=True,exist_ok=True,mode=0o755)
for name,data in files.items():
 p=dest/name
 if p.exists():assert p.read_bytes()==data
 atomic(p,data,0o644)
with zipfile.ZipFile(D/'preview.zip','w',zipfile.ZIP_DEFLATED) as z:
 for n,d in files.items():z.writestr(n,d)
atomic(dest/'preview.zip',(D/'preview.zip').read_bytes(),0o644)
mode=SITE.stat().st_mode&0o777;changed=False
try:
 assert SITE.read_bytes()==before
 atomic(SITE,proposed,mode);changed=True;run('nginx','-t');run('systemctl','reload','nginx')
 for attempt in range(20):
  try:
   if sha(fetch('/preview/light/')[0])==manifest['pages']['index.html']:break
  except Exception:pass
  time.sleep(.3)
 else:raise RuntimeError('Preview route unavailable')
 for name,expected in manifest['pages'].items():assert sha(fetch('/preview/light/'+name)[0])==expected
 assert {p:sha(fetch(p)[0]) for p in baseline}==baseline
 assert pid()==before_pid
 out=dict(previewPublished=True,url='https://defivela.xyz/preview/light/',directory=str(dest),servedFilesVerified=4,liveRoutesUnchanged=True,tradingPidUnchanged=True,liveProtectionChanged=False,preparedBrowserChecks=12,publicBrowserVerified=False,baseline=baseline)
 (D/'published.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
except BaseException:
 if changed and SITE.read_bytes()==proposed:atomic(SITE,before,mode);run('nginx','-t');run('systemctl','reload','nginx')
 raise
