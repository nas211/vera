"""Private database backups and read-only health checks for the existing Vela backend."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.backup_state import backup_database


def check(root=ROOT, now=None, fetch=None):
    now = time.time() if now is None else now
    if fetch is None:
        def fetch(url):
            with urllib.request.urlopen(url, timeout=8) as response:
                return json.loads(response.read(65536))
    problems = []
    for label, url in [('backend', 'http://127.0.0.1:8804/api/ready'),
                       ('gateway', 'http://127.0.0.1:8805/api/health')]:
        try:
            state = fetch(url)
            if (state.get('mode') != 'PAPER' or state.get('realFunding') is not False or
                    state.get('liveEnabled') is not False or state.get('marketData') is not True or
                    state.get('engineReady') is not True or state.get('runtimeError')):
                problems.append(label+' is not healthy and paper-only')
        except Exception:
            problems.append(label+' health unavailable')
    if shutil.disk_usage(root).free < 5*1024**3:
        problems.append('Free disk below 5 GiB')
    manifests = list((root/'.runtime/backups').glob('*/manifest.json'))
    good = []
    for path in manifests:
        try:
            item = json.loads(path.read_text())
            if item.get('integrity') == 'ok' and item.get('restoreVerified') is True:
                good.append(float(item['createdAt']))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if not good or not 0 <= now-max(good) <= 36*3600:
        problems.append('No verified backup in the last 36 hours')
    return dict(healthy=not problems, problems=problems, readOnly=True, liveProtectionVerified=False)


def backup(root=ROOT):
    # Never prune customer data or existing recovery copies automatically.
    if shutil.disk_usage(root).free < 5*1024**3:
        raise RuntimeError('Insufficient free disk for a new verified backup')
    folder = root/'.runtime/backups'/time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = backup_database(root/'.runtime/lighter-showcase/multichain-candidate.sqlite3', folder/'ledger.sqlite3')
    (folder/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    (folder/'manifest.json').chmod(0o600)
    return dict(backup=str(folder), integrity=result['integrity'], restoreVerified=result['restoreVerified'])


if __name__ == '__main__':
    import os
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('check', 'backup'))
    args = parser.parse_args()
    try:
        result = backup() if args.operation == 'backup' else check()
        print(json.dumps(result))
        raise SystemExit(0 if result.get('healthy', True) else 1)
    except Exception as exc:
        print(json.dumps(dict(ok=False, errorType=type(exc).__name__, operation=args.operation)))
        raise SystemExit(1)
