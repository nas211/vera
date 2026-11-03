"""Acquire the existing engine lock before opening or initializing its ledger."""
import fcntl
from pathlib import Path


class OwnedDatabase:
    def __init__(self, path, opener):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = None
        self._lock = open(str(path) + '.lock', 'a')
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._db = opener(path)
        except BaseException:
            self._lock.close()
            self._lock = None
            raise

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        try:
            if self._db is not None:
                self._db.close()
                self._db = None
        finally:
            if self._lock is not None:
                self._lock.close()
                self._lock = None
