"""Build/verify an isolated static Noah preview package. Never installs or publishes it."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/vela-preview/'
INPUTS = ('src', 'public', 'index.html', 'preview.html', 'vela.html', 'vite.config.ts',
          'package.json', 'package-lock.json', 'tailwind.config.js', 'postcss.config.js', 'tsconfig.app.json')
GATES = 'src/lib/vela/integrationConfig.ts'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    return bool(name and not p.is_absolute() and '..' not in p.parts and '.' not in p.parts
                and str(p) == name and re.fullmatch(r'[A-Za-z0-9_./@-]+', name))


def checked_file(root, name):
    if not safe_name(name):
        raise ValueError('Unsafe file name')
    path = root / name
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent):
        raise ValueError('Redirected package input')
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing or redirected package file: ' + name)
    return path.read_bytes()


def inventory(root):
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Redirected package input')
        if path.is_file():
            name = path.relative_to(root).as_posix()
            files[name] = checked_file(root, name)
    return files


def source_state(dashboard):
    files = {}
    for name in INPUTS:
        path = dashboard / name
        if path.is_symlink():
            raise ValueError('Redirected source input')
        if path.is_dir():
            files.update({name + '/' + p: sha(data) for p, data in inventory(path).items()})
        else:
            files[name] = sha(checked_file(dashboard, name))
    files = dict(sorted(files.items()))
    return dict(schemaVersion=1, sourceId=sha(json.dumps(files, ensure_ascii=False, separators=(',', ':')).encode()), files=files)


def nginx_configuration(release_id):
    if not re.fullmatch(r'noah-[0-9a-f]{12}-[0-9a-f]{12}', release_id):
        raise ValueError('Invalid release id')
    root = '/var/www/defivela-preview/' + release_id
    headers = '''    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'" always;
'''
    return f'''# PREPARED ONLY. Insert these locations inside the existing defivela.xyz TLS server
# only after a separate publication decision. Never replace its homepage/API routes.
# Static destination: {root}
location = /vela-preview {{ return 308 /vela-preview/$is_args$args; }}
location = /vela-preview/ {{
    alias {root}/preview.html;
    default_type text/html;
{headers}}}
location ^~ /vela-preview/ {{
    alias {root}/;
    autoindex off;
{headers}}}
'''


def validate_html(files, source_id, entries=('preview.html', 'vela.html'), asset_prefix=None):
    for entry in entries:
        html = files[entry].decode()
        if f'name="vela-source-id" content="{source_id}"' not in html:
            raise ValueError('HTML/source identity mismatch')
        if 'noindex, nofollow' not in html:
            raise ValueError('Preview indexing must be disabled')
        for target in re.findall(r'(?:src|href)="([^"]+)"', html):
            remote = urlsplit(target)
            if remote.scheme == 'https' and remote.hostname in ('fonts.googleapis.com', 'fonts.gstatic.com') and not remote.username and remote.port in (None, 443):
                continue
            local=target[2:] if target.startswith('./') else target[len(asset_prefix):] if asset_prefix and target.startswith(asset_prefix) else None
            if local not in files:
                raise ValueError('Missing or non-local entry asset: ' + target)


def allowed_asset(name):
    return safe_name(name) and (
        name in ('preview.html', 'vela.html', 'source-manifest.json') or
        (name.startswith('assets/') and Path(name).suffix in ('.js', '.css', '.svg', '.png', '.webp', '.jpg', '.jpeg', '.woff', '.woff2', '.ttf')) or
        (len(PurePosixPath(name).parts) == 1 and Path(name).suffix in ('.svg', '.png', '.webp', '.ico'))
    )


def build(dashboard, destination):
    dashboard, destination = Path(dashboard).resolve(), Path(destination)
    provenance = source_state(dashboard)
    built = json.loads(checked_file(dashboard / 'dist', 'source-manifest.json'))
    if built != provenance:
        raise ValueError('Build is stale; rebuild the exact source before packaging')
    gates = checked_file(dashboard, GATES).decode()
    for key in ('serviceEnabled', 'writesEnabled'):
        if not re.search(r'\b' + key + r'\s*:\s*false\s+as\s+const', gates):
            raise ValueError('Preview requires service and blockchain writes OFF')
    all_files = inventory(dashboard / 'dist')
    # The original standalone fixture entry uses root routes. Only the hash-routed
    # preview and inactive service entries are exported beneath this nested prefix.
    files = {name: data for name, data in all_files.items() if name not in ('index.html', 'robots.txt')}
    if not all(allowed_asset(name) for name in files):
        raise ValueError('Unexpected build output; refuse source maps, runtime files or secrets')
    validate_html(files, provenance['sourceId'])
    hashes = {name: sha(data) for name, data in files.items()}
    content_id = sha(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode())
    release_id = 'noah-' + provenance['sourceId'][:12] + '-' + content_id[:12]
    review = dict(schemaVersion=1, releaseId=release_id, sourceId=provenance['sourceId'], contentId=content_id,
                  mode='NOAH_UI_PREVIEW', data='SAMPLE_ONLY', serviceEnabled=False, blockchainWritesEnabled=False,
                  deployed=False, browserVerified=False, liveProtectionVerified=False,
                  entry='preview.html', serviceEntry='vela.html', plannedPrefix=PREFIX,
                  files=hashes)
    review_bytes = (json.dumps(review, indent=2) + '\n').encode()
    files['review-manifest.json'] = review_bytes
    config = nginx_configuration(release_id).encode()
    # All validation precedes creation; existing outputs are never overwritten.
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    website = destination / 'website'
    for name, data in files.items():
        path = website / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    archive = destination / 'website.tar.gz'
    with archive.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), 0o644, 0
                tar.addfile(info, io.BytesIO(data))
    (destination / 'nginx-preview.conf.pending').write_bytes(config)
    manifest = dict(schemaVersion=1, release=review, archiveSha256=sha(archive.read_bytes()),
                    reviewManifestSha256=sha(review_bytes), nginxSha256=sha(config),
                    packagedFiles={name: sha(data) for name, data in files.items()}, published=False)
    (destination / 'package.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verify(destination)
    return manifest


def verify(directory):
    directory = Path(directory).resolve()
    manifest = json.loads(checked_file(directory, 'package.json'))
    release = manifest.get('release', {})
    expected = manifest.get('packagedFiles', {})
    if (manifest.get('schemaVersion') != 1 or not expected or release.get('mode') != 'NOAH_UI_PREVIEW'
            or release.get('data') != 'SAMPLE_ONLY' or release.get('plannedPrefix') != PREFIX
            or release.get('serviceEnabled') is not False or release.get('blockchainWritesEnabled') is not False
            or release.get('deployed') is not False or manifest.get('published') is not False
            or not re.fullmatch(r'[0-9a-f]{64}', release.get('sourceId', ''))):
        raise ValueError('Invalid inactive preview package')
    if not all(safe_name(name) and (allowed_asset(name) or name == 'review-manifest.json')
               and re.fullmatch(r'[0-9a-f]{64}', value) for name, value in expected.items()):
        raise ValueError('Invalid package file record')
    actual = inventory(directory / 'website')
    if {name: sha(data) for name, data in actual.items()} != expected:
        raise ValueError('Website file checksum mismatch')
    if sha(actual['review-manifest.json']) != manifest['reviewManifestSha256'] or json.loads(actual['review-manifest.json']) != release:
        raise ValueError('Review manifest checksum mismatch')
    if release['files'] != {name: h for name, h in expected.items() if name != 'review-manifest.json'}:
        raise ValueError('Review file list mismatch')
    if actual['source-manifest.json'] and json.loads(actual['source-manifest.json'])['sourceId'] != release['sourceId']:
        raise ValueError('Source identity mismatch')
    validate_html(actual, release['sourceId'])
    config = checked_file(directory, 'nginx-preview.conf.pending')
    if sha(config) != manifest['nginxSha256'] or config.decode() != nginx_configuration(release['releaseId']):
        raise ValueError('Hosting configuration mismatch')
    archive = checked_file(directory, 'website.tar.gz')
    if sha(archive) != manifest['archiveSha256']:
        raise ValueError('Archive checksum mismatch')
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile() or not safe_name(member.name) or member.name in seen or member.name not in expected:
                raise ValueError('Unsafe archive member')
            seen.add(member.name)
            if sha(tar.extractfile(member).read()) != expected[member.name]:
                raise ValueError('Archive member checksum mismatch')
    if seen != set(expected):
        raise ValueError('Missing archive file')
    return dict(verified=True, releaseId=release['releaseId'], files=len(expected), published=False,
                browserVerified=False, serviceEnabled=False, blockchainWritesEnabled=False, liveProtectionVerified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--dashboard', type=Path, default=ROOT / 'site/vela-dashboard')
    builder.add_argument('--destination', type=Path, required=True)
    checker = commands.add_parser('verify')
    checker.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        build(args.dashboard, args.destination)
        print(json.dumps(verify(args.destination)))
    else:
        print(json.dumps(verify(args.directory)))


if __name__ == '__main__':
    main()
