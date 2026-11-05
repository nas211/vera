"""Build and verify a backend-only Git overlay and code rollback. Never deploys."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


NATIVE_ABIS = frozenset('config/'+name+'.abi.json' for name in ('NativeRewardPool', 'RewardedCollateralVault', 'RewardedVaultFactory'))


def native_runtime_path(name):
    path = PurePosixPath(name)
    return name in NATIVE_ABIS or (path.parts[0] in {'backend', 'tools'} and path.suffix == '.py')


def allowed(name, native=False):
    path = PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts and
            (path.parts[0] in {'backend', 'tools', 'tests', 'docs', 'ops', 'snapshot'} or (native and name in NATIVE_ABIS)) and
            path.suffix in {'.py', '.md', '.json', '.pending'})


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_archive(path, entries):
    with tarfile.open(path, 'w:gz') as archive:
        for name, data, mode in entries:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), mode
            archive.addfile(member, io.BytesIO(data))


def build(source, destination, base_ref, native=False):
    source, destination = Path(source).resolve(), Path(destination)
    base = git(source, 'rev-parse', '--verify', base_ref+'^{commit}').decode().strip()
    head = git(source, 'rev-parse', 'HEAD').decode().strip()
    changes = git(source, 'diff', '--name-status', '--no-renames', base, head).decode().splitlines()
    if native:
        # A mixed UI/backend branch needs only runtime Python and these three public ABIs.
        dirty = git(source, 'diff', '--name-only', 'HEAD').decode().splitlines()
        untracked = git(source, 'ls-files', '--others', '--exclude-standard').decode().splitlines()
        if any(native_runtime_path(name) for name in dirty + untracked):
            raise ValueError('Commit the exact native backend runtime before packaging')
        changes = [line for line in changes if native_runtime_path(line.split('\t', 1)[1])]
    if not changes:
        raise ValueError('No committed backend changes to package')
    entries, rollback, files = [], [], []
    for line in changes:
        status, name = line.split('\t', 1)
        if status not in {'A', 'M'} or not allowed(name, native):
            raise ValueError('Only additive backend source changes are packageable: '+name)
        mode = git(source, 'ls-tree', head, '--', name).decode().split()[0]
        if mode not in {'100644', '100755'}:
            raise ValueError('Release files must be regular files: '+name)
        data = git(source, 'show', head+':'+name)
        previous = git(source, 'show', base+':'+name) if status == 'M' else None
        entries.append((name, data, int(mode, 8) & 0o777))
        if previous is not None:
            old_mode = git(source, 'ls-tree', base, '--', name).decode().split()[0]
            rollback.append((name, previous, int(old_mode, 8) & 0o777))
        files.append(dict(path=name, sha256=sha(data), previousSha256=sha(previous) if previous is not None else None,
                          rollback='restore original file' if previous is not None else 'remove only if release hash still matches'))
    # Validate every member before creating any artifact; nothing from runtime,
    # the frontend, environment files, databases or generated bundles is included.
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_archive(destination/'backend-overlay.tar.gz', entries)
    write_archive(destination/'backend-rollback.tar.gz', rollback)
    manifest = dict(schemaVersion=1, baseCommit=base, releaseCommit=head, backendOnly=True,nativeRewardRuntime=native,
                    deployed=False, databaseMigration=False, liveProtectionVerified=False, files=files,
                    archives={name:sha((destination/name).read_bytes()) for name in ('backend-overlay.tar.gz', 'backend-rollback.tar.gz')})
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    verify(destination)
    return manifest


def verify(directory, target=None):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    native = manifest.get('nativeRewardRuntime') is True
    if (manifest.get('schemaVersion') != 1 or manifest.get('backendOnly') is not True or
            set(manifest.get('archives', {})) != {'backend-overlay.tar.gz', 'backend-rollback.tar.gz'} or
            not isinstance(manifest.get('files'), list) or not manifest['files']):
        raise ValueError('Incomplete release manifest')
    names = set()
    for row in manifest['files']:
        name = row.get('path', '')
        if (not name or not allowed(name, native) or name in names or
                not re.fullmatch('[0-9a-f]{64}', row.get('sha256', '')) or
                (row.get('previousSha256') is not None and not re.fullmatch('[0-9a-f]{64}', row['previousSha256']))):
            raise ValueError('Invalid release source record')
        names.add(name)
    for archive_name, expected in manifest['archives'].items():
        if archive_name not in ('backend-overlay.tar.gz', 'backend-rollback.tar.gz'):
            raise ValueError('Unknown release archive')
        if sha((directory/archive_name).read_bytes()) != expected:
            raise ValueError('Archive checksum mismatch')
        records = {row['path']: row for row in manifest['files'] if archive_name == 'backend-overlay.tar.gz' or row['previousSha256'] is not None}
        seen = set()
        with tarfile.open(directory/archive_name, 'r:gz') as archive:
            for member in archive:
                if not member.isfile() or not allowed(member.name, native) or member.name in seen or member.name not in records:
                    raise ValueError('Unsafe or duplicate archive member')
                seen.add(member.name)
                field = 'sha256' if archive_name == 'backend-overlay.tar.gz' else 'previousSha256'
                if sha(archive.extractfile(member).read()) != records[member.name][field]:
                    raise ValueError('Source checksum mismatch')
        if seen != set(records):
            raise ValueError('Missing source member')
    if target is not None:
        target = Path(target).resolve()
        for row in manifest['files']:
            path = target/row['path']
            if path.is_symlink() or not path.resolve().is_relative_to(target):
                raise ValueError('Redirected target path')
            expected = row['previousSha256']
            if expected is None:
                if path.exists():raise ValueError('New release path already exists: '+row['path'])
            elif not path.is_file() or sha(path.read_bytes()) != expected:
                raise ValueError('Target has newer or different source: '+row['path'])
    return dict(verified=True, targetMatched=target is not None, files=len(manifest['files']),
                deployed=False, frontendIncluded=False, databasesIncluded=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    builder.add_argument('--base', required=True)
    builder.add_argument('--destination', type=Path, required=True)
    builder.add_argument('--native', action='store_true', help='Select backend/runtime Python and the three public reward ABIs from a mixed branch')
    checker = commands.add_parser('verify')
    checker.add_argument('--directory', type=Path, required=True)
    checker.add_argument('--target', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'build':
        result = build(args.source, args.destination, args.base, native=args.native)
        print(json.dumps(dict(files=len(result['files']), releaseCommit=result['releaseCommit'], deployed=False)))
    else:
        print(json.dumps(verify(args.directory, args.target)))


if __name__ == '__main__':
    main()
