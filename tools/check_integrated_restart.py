"""Read-only restart gate. Never starts/stops services or opens a trading database."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_lighter_paper import load_model_file


def database_gate(path, runtime_root=ROOT / '.runtime/lighter-showcase'):
    """Probe the existing ownership lock without creating it or touching SQLite."""
    if not path:
        return 'VELA_PAPER_DB must explicitly select the existing paper ledger'
    path = Path(path).resolve()
    if not path.is_relative_to(runtime_root.resolve()):
        return 'Paper ledger must remain in the isolated paper runtime directory'
    if not path.is_file() or path.stat().st_size == 0:
        return 'Selected paper ledger does not exist; refuse a new empty balance store'
    lock = path.with_suffix('.lock')
    if not lock.is_file():
        return 'Existing engine ownership lock is missing; host inspection is required'
    try:
        with lock.open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'Paper ledger has an active engine owner; do not restart the API against it'
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        return 'Paper engine ownership could not be inspected'
    return None


def proxy_configured(path):
    """Validate the private file without returning its secret, even on errors."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 512:
            return False
        return bool(re.fullmatch(r'VELA_SITE_PROXY_TOKEN=[A-Za-z0-9_-]{32,256}\n?', path.read_text()))
    except (OSError, UnicodeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paper-db', type=Path, default=os.getenv('VELA_PAPER_DB'))
    parser.add_argument('--model-env', type=Path, default=ROOT / '.runtime/lighter-showcase/model.env')
    parser.add_argument('--proxy-env', type=Path, default=ROOT / '.runtime/lighter-showcase/site-proxy.env')
    args = parser.parse_args()
    blockers = []
    issue = database_gate(args.paper_db)
    if issue:
        blockers.append(issue)
    try:
        model_ok = bool(load_model_file(args.model_env))
    except (ValueError, OSError, UnicodeError):
        model_ok = False
    if not model_ok:
        blockers.append('Dedicated owner-only Vela model configuration is unavailable')
    proxy_ok = proxy_configured(args.proxy_env)
    if not proxy_ok:
        blockers.append('Owner-only shared proxy configuration is unavailable or invalid')
    print(json.dumps(dict(localRestartPrerequisitesReady=not blockers,
                         paperDatabase=str(args.paper_db) if args.paper_db else None,
                         modelConfigurationPresent=model_ok, proxyConfigurationPresent=proxy_ok,
                         blockers=blockers,
                         deploymentVerified=False,
                         note='Requires host service/env review, current backups, matching Sites secret and live acceptance. The runtime also takes an atomic ownership lock.'), indent=2))
    return 1 if blockers else 0


if __name__ == '__main__':
    raise SystemExit(main())
