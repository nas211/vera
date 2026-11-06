"""Package the explicit DogeOS account build. Never publishes, signs or restarts."""
import argparse
import gzip
import io
import json
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_noah_preview import inventory, source_state, checked_file, sha, allowed_asset, safe_name, validate_html

PREFIX = '/app/'


def active_source(dashboard):
    dashboard = Path(dashboard)
    files = source_state(dashboard)['files']
    shared = {name: sha(data) for name, data in inventory(dashboard.parent/'src').items()}
    content = dict(files=files, sharedFiles=dict(sorted(shared.items())), profile='DOGEOS_TESTNET')
    return dict(schemaVersion=2, sourceId=sha(json.dumps(content, separators=(',', ':')).encode()), **content)


def hosting(release, version=2):
    if not re.fullmatch(r'vela-app-[0-9a-f]{12}-[0-9a-f]{12}', release):
        raise ValueError('Invalid testnet release identity')
    root = '/var/www/defivela-app/' + release
    headers = '''    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header Referrer-Policy "same-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://rpc.testnet.dogeos.com wss://defivela.xyz; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'" always;
'''
    if version == 3:
        headers = headers.replace("img-src 'self' data:;", "img-src 'self' data: https://web3-assets.mydoge.com;").replace("connect-src 'self' https://rpc.testnet.dogeos.com wss://defivela.xyz;", "connect-src 'self' https://rpc.testnet.dogeos.com wss://defivela.xyz https://web3-assets.mydoge.com https://relay.mydoge.com wss://relay.mydoge.com https://wallet.mydoge.com https://embedded-wallet.mydoge.com; frame-src https://embedded-wallet.mydoge.com https://wallet.mydoge.com;")
    routes=''
    if version in (2, 3):
        for route in ('/app/vault','/app/borrow','/app/trading','/app/risk','/settings'):
            routes+=f'''location = {route} {{
    root {root};
    try_files /vela.html =404;
    default_type text/html;
{headers}}}
'''
        routes+='location = /settings/ { return 308 /settings$is_args$args; }\n'
    elif version!=1:raise ValueError('Unsupported account routing version')
    return f'''# PREPARED ONLY. Existing TLS server, homepage, API proxy and ledger stay in place.
# Requires the separately configured native backend and gateway before wallet actions work.
location = /app {{ return 308 /app/$is_args$args; }}
location = /app/ {{
    root {root};
    try_files /vela.html =404;
    default_type text/html;
{headers}}}
location ^~ /app/ {{
    alias {root}/;
    autoindex off;
{headers}}}
'''+routes


def build(dashboard, destination):
    dashboard, destination = Path(dashboard), Path(destination)
    source = active_source(dashboard)
    if json.loads(checked_file(dashboard/'dist-testnet', 'source-manifest.json')) != source:
        raise ValueError('Rebuild the exact testnet profile and shared wallet source')
    files = {n:d for n,d in inventory(dashboard/'dist-testnet').items() if n != 'robots.txt'}
    if not all(allowed_asset(n) and n not in ('preview.html','index.html') for n in files):
        raise ValueError('Unexpected account release output')
    validate_html(files, source['sourceId'], ('vela.html',), asset_prefix=PREFIX)
    hashes = {n:sha(d) for n,d in files.items()}
    content = sha(json.dumps(dict(files=hashes, prefix=PREFIX), sort_keys=True, separators=(',', ':')).encode())
    release_id = 'vela-app-'+source['sourceId'][:12]+'-'+content[:12]
    release = dict(schemaVersion=1, mode='DOGEOS_TESTNET', sourceId=source['sourceId'], releaseId=release_id,
        chainId=6281971, execution='PAPER', lending='DEMO', entry='vela.html', plannedPrefix=PREFIX,
        serviceEnabled=True, walletActionsBuilt=True, backendGateRequired=True, files=hashes,
        deployed=False, browserVerified=False, livePayoutsVerified=False, liveProtectionVerified=False,
        nativeDogeLending=dict(configuration='/api/doge-lending/config',requiresVerifiedDeployment=True,
            usdcConversionEnabled=False,legacyLedgerCurrencyUnchanged=True))
    files['review-manifest.json'] = (json.dumps(release, indent=2)+'\n').encode()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name,data in files.items():
        p=destination/'website'/name;p.parent.mkdir(parents=True, exist_ok=True);p.write_bytes(data)
    with (destination/'website.tar.gz').open('wb') as raw, gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name,data in sorted(files.items()):
                item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;item.mtime=0
                archive.addfile(item, io.BytesIO(data))
    (destination/'nginx-testnet.conf.pending').write_text(hosting(release_id, 3))
    manifest=dict(schemaVersion=1, hostingVersion=3, release=release, published=False, files={n:sha(d) for n,d in files.items()},
        archiveSha256=sha((destination/'website.tar.gz').read_bytes()),nginxSha256=sha((destination/'nginx-testnet.conf.pending').read_bytes()))
    (destination/'package.json').write_text(json.dumps(manifest, indent=2)+'\n')
    verify(destination)
    return manifest


def verify(directory):
    directory=Path(directory)
    manifest=json.loads(checked_file(directory,'package.json'));r=manifest.get('release',{})
    if (manifest.get('schemaVersion')!=1 or manifest.get('published') is not False or r.get('mode')!='DOGEOS_TESTNET'
        or r.get('chainId')!=6281971 or r.get('execution')!='PAPER' or r.get('lending')!='DEMO'
        or r.get('entry')!='vela.html' or r.get('plannedPrefix')!=PREFIX
        or r.get('serviceEnabled') is not True or r.get('walletActionsBuilt') is not True
        or r.get('backendGateRequired') is not True
        or any(r.get(k) is not False for k in ('deployed','browserVerified','livePayoutsVerified','liveProtectionVerified'))):
        raise ValueError('Invalid testnet account release')
    if 'nativeDogeLending' in r and r['nativeDogeLending'] != dict(
            configuration='/api/doge-lending/config',requiresVerifiedDeployment=True,
            usdcConversionEnabled=False,legacyLedgerCurrencyUnchanged=True):
        raise ValueError('Invalid native lending activation boundary')
    files=inventory(directory/'website');hashes={n:sha(d) for n,d in files.items()}
    if hashes!=manifest.get('files') or json.loads(files['review-manifest.json'])!=r:
        raise ValueError('Account release checksum mismatch')
    if r.get('files')!={n:h for n,h in hashes.items() if n!='review-manifest.json'}:
        raise ValueError('Account release inventory mismatch')
    if not all(safe_name(n) and (n=='review-manifest.json' or (allowed_asset(n) and n!='preview.html')) for n in files):
        raise ValueError('Unsafe account release file')
    source=json.loads(files['source-manifest.json'])
    if source.get('profile')!='DOGEOS_TESTNET' or source.get('schemaVersion')!=2 or source.get('sourceId')!=r.get('sourceId'):
        raise ValueError('Account release source mismatch')
    identity={k:source[k] for k in ('files','sharedFiles','profile')}
    content=sha(json.dumps(dict(files=r['files'],prefix=PREFIX),sort_keys=True,separators=(',', ':')).encode())
    if (sha(json.dumps(identity,separators=(',', ':')).encode())!=source['sourceId']
        or r['releaseId']!='vela-app-'+source['sourceId'][:12]+'-'+content[:12]):
        raise ValueError('Account release identity mismatch')
    validate_html(files,r['sourceId'],('vela.html',),asset_prefix=PREFIX if manifest.get('hostingVersion') in (2, 3) else None)
    conf=checked_file(directory,'nginx-testnet.conf.pending')
    if sha(conf)!=manifest['nginxSha256'] or conf.decode()!=hosting(r['releaseId'],manifest.get('hostingVersion',1)):
        raise ValueError('Account hosting configuration mismatch')
    archive=checked_file(directory,'website.tar.gz')
    if sha(archive)!=manifest['archiveSha256']:raise ValueError('Account archive checksum mismatch')
    seen=set()
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        for item in tar:
            if not item.isfile() or item.name not in hashes or item.name in seen:
                raise ValueError('Unsafe account archive member')
            seen.add(item.name)
            if sha(tar.extractfile(item).read())!=hashes[item.name]:raise ValueError('Account archive member checksum mismatch')
    if seen!=set(hashes):raise ValueError('Incomplete account archive')
    return dict(verified=True,releaseId=r['releaseId'],files=len(files),published=False,liveProtectionVerified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['build','verify'])
    parser.add_argument('--dashboard',type=Path,default=ROOT/'site/vela-dashboard')
    parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='build':build(args.dashboard,args.directory)
    print(json.dumps(verify(args.directory)))
