"""SQLite (WAL) plus an ordered migration runner.

TWO PROCESSES WRITE THIS FILE: the Telegram bot and the WHY Predict web app,
from separate interpreters. That is what WAL is for, and it is why every write
here goes through `BEGIN IMMEDIATE` — deferred transactions in two writers is
how you get SQLITE_BUSY at commit time, after the work is done, instead of at
the start where it can just wait.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from . import config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def migrate(con: sqlite3.Connection) -> list:
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at REAL NOT NULL)")
    done = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
    applied = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.stem in done:
            continue
        con.executescript("BEGIN;\n" + f.read_text() + "\nCOMMIT;")
        con.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                    (f.stem, time.time()))
        applied.append(f.stem)
    return applied


class Store:
    """One connection guarded by a re-entrant lock.

    `with store as con:` opens an IMMEDIATE transaction and commits on the way
    out, rolling back on any exception. It nests: an inner `with` joins the
    transaction its caller opened rather than starting a second one, so the
    engine's building blocks (post an award, flag abuse, write audit) compose
    into one atomic operation without any of them knowing who called them.
    """

    def __init__(self, path=None):
        self.path = Path(path or config.DB_PATH)
        self.con = connect(self.path)
        self.lock = threading.RLock()
        self._depth = 0
        migrate(self.con)

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        if self._depth == 0:
            self.con.execute("BEGIN IMMEDIATE")
        self._depth += 1
        return self.con

    def __exit__(self, et, ev, tb):
        try:
            self._depth -= 1
            if self._depth == 0:
                self.con.execute("ROLLBACK" if et else "COMMIT")
            elif et:
                # An inner failure must not let the outer block commit half of
                # the operation; mark the whole transaction doomed.
                self._depth = 0
                self.con.execute("ROLLBACK")
        finally:
            self.lock.release()

    def q(self, sql, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchone()

    def setting(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key, value):
        with self as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def close(self):
        self.con.close()


def audit(con, actor: str, action: str, target: str = "", detail: str = "", ts: float = None):
    con.execute("INSERT INTO audit(ts,actor,action,target,detail) VALUES(?,?,?,?,?)",
                (ts or time.time(), actor, action, target, detail))
