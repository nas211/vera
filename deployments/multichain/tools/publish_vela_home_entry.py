"""Change only the published homepage's app links; preserve its design/assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.deploy_noah_testnet import atomic

HOME = Path('/var/www/defivela-ui/simple-tabs-20261101T085948Z')
CHANGES = {
    'className:"vella-launch",href:"/vault"': 'className:"vella-launch",href:"/app/#/trading"',
    '{overview:"/overview",vault:"/vault",borrow:"/borrow",trading:"/trading",agent:"/repay"}':
    '{overview:"/app/#/",vault:"/app/#/vault",borrow:"/app/#/borrow",trading:"/app/#/trading",agent:"/app/#/borrow"}',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def get(path):
    with urllib.request.urlopen('https://defivela.xyz'+path, timeout=20) as response:
        return response.read()


def prepare(directory):
    previous = (HOME/'index.html').read_bytes()
    urls = re.findall(rb'<script[^>]+src="([^"]+)"', previous)
    if len(urls) != 1:
        raise RuntimeError('Unexpected homepage entry')
    url = urls[0].decode()
    if not url.startswith('/ui-assets/simple-tabs-20261101T085948Z/'):
        raise RuntimeError('Unexpected homepage asset origin')
    script = HOME/'assets'/Path(url).name
    before = script.read_text()
    after = before
    for old,new in CHANGES.items():
        if after.count(old) != 1:
            raise RuntimeError('Homepage navigation differs; reconcile first')
        after = after.replace(old,new,1)
    name = 'app-vela-entry-'+sha(after.encode())[:12]+'.js'
    new_url = url.rsplit('/',1)[0]+'/'+name
    html = previous.replace(url.encode(),new_url.encode(),1)
    directory.mkdir(parents=True,exist_ok=False)
    (directory/name).write_text(after)
    (directory/'index.html').write_bytes(html)
    manifest = dict(previousHtmlSha256=sha(previous), previousEntry=script.name,
        previousScriptSha256=sha(before.encode()), htmlSha256=sha(html), script=name,
        scriptSha256=sha(after.encode()), publicScript=new_url, targetedReplacements=len(CHANGES),
        homepageStylesAndOtherBytesUnchanged=True, published=False)
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def publish(directory):
    m=json.loads((directory/'manifest.json').read_text())
    old=(HOME/'index.html').read_bytes()
    html=(directory/'index.html').read_bytes()
    script=(directory/m['script']).read_bytes()
    if (sha(old)!=m['previousHtmlSha256'] or sha((HOME/'assets'/m['previousEntry']).read_bytes())!=m['previousScriptSha256']
            or sha(html)!=m['htmlSha256'] or sha(script)!=m['scriptSha256']):
        raise RuntimeError('Homepage or prepared package changed')
    if b'<title>Vela \xe2\x80\x94 App</title>' not in get('/app/'):
        raise RuntimeError('Vela app must be served before changing homepage links')
    target=HOME/'assets'/m['script']
    if target.exists():
        raise RuntimeError('Preserve existing target asset')
    atomic(target,script,0o644)
    try:
        atomic(HOME/'index.html',html,0o644)
        if sha(get('/'))!=m['htmlSha256'] or sha(get(m['publicScript']))!=m['scriptSha256']:
            raise RuntimeError('Homepage publication check failed')
    except BaseException:
        if (HOME/'index.html').read_bytes()!=html:
            raise RuntimeError('Concurrent homepage update; preserve for reconciliation')
        atomic(HOME/'index.html',old,0o644)
        raise
    return dict(m,published=True,tradeUrl='https://defivela.xyz/app/#/trading')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','publish'])
    parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.directory) if args.command=='prepare' else publish(args.directory)))
