"""Consistent SQLite recovery copies. Never opens a trading engine or restores in place."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def backup_database(source, destination, timeout=60):
    source, destination = Path(source).resolve(strict=True), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Backup destination already exists')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.stat().st_mode & 0o077:
        raise ValueError('Backup directory must be private (mode 0700)')
    start = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - start > timeout:
            raise TimeoutError('Backup deadline exceeded; source left unchanged')

    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    src = dst = None
    try:
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=2)
        src.execute('PRAGMA query_only=ON')
        # Pin a WAL-aware read snapshot. Otherwise a continuously updated ledger
        # can cause the online backup to restart indefinitely between page batches.
        src.execute('BEGIN')
        src.execute('SELECT count(*) FROM sqlite_master').fetchone()
        dst = sqlite3.connect(destination)
        src.backup(dst, pages=4096, progress=progress, sleep=.02)
        src.rollback()
        src.close()
        src = None
        if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Backup integrity check failed')
        schema = dst.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = [row[0] for row in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        # Exercise recovery into a new isolated database, never over a live ledger.
        with tempfile.TemporaryDirectory(prefix='restore-', dir=destination.parent) as folder:
            restored = sqlite3.connect(Path(folder) / 'restored.sqlite3')
            try:
                dst.backup(restored)
                if restored.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Restored database integrity check failed')
                if restored.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema:
                    raise ValueError('Restored database schema differs')
                for table in tables:
                    query = 'SELECT count(*) FROM "' + table.replace('"', '""') + '"'
                    if dst.execute(query).fetchone() != restored.execute(query).fetchone():
                        raise ValueError('Restored table count differs')
            finally:
                restored.close()
        dst.close()
        dst = None
        # A standalone recovery artifact must not depend on a sidecar WAL.
        with sqlite3.connect(destination) as check:
            check.execute('PRAGMA journal_mode=DELETE')
        check.close()
        with destination.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return dict(source=str(source), backup=str(destination), sha256=digest,
                    bytes=destination.stat().st_size, integrity='ok', restoreVerified=True,
                    schemaVerified=True, tableCountsVerified=True,
                    createdAt=time.time(), elapsedSeconds=round(time.monotonic()-start, 3))
    except BaseException:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
        for suffix in ('', '-wal', '-shm', '-journal'):
            Path(str(destination) + suffix).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    result = backup_database(args.source, args.destination)
    manifest = args.destination.with_suffix('.manifest.json')
    with manifest.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
