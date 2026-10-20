"""Postgres access for the customer platform (psycopg 3, direct connection).

Two access modes:
  * service(): the server's own privileged connection (Supabase `postgres` role or, in
    tests, the database owner). Used for every mutation and for Vault. Server-side only.
  * as_user(user_id): same connection but `SET LOCAL ROLE authenticated` + the JWT claims
    GUC that Supabase's auth.uid() reads. RLS then applies exactly as it does for the
    browser/PostgREST path, so every customer read goes through the RLS policies even
    though it originates on the server (defense in depth), and tests can prove isolation.
The DSN password is registered as a secret so it can never be logged."""
from __future__ import annotations
import json
import queue
import threading
from contextlib import contextmanager
from typing import Iterator, Optional
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from .log import get_logger, register_secret, safe_exc

log = get_logger("db")


class Database:
    def __init__(self, dsn: str, max_conn: int = 4, connect_timeout: int = 10):
        self.dsn = dsn
        try:
            pw = urlsplit(dsn).password
            if pw:
                register_secret(pw)
        except Exception:
            pass
        self.max_conn = max_conn
        self.connect_timeout = connect_timeout
        self._pool: "queue.LifoQueue[psycopg.Connection]" = queue.LifoQueue()
        self._count = 0
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_conn)

    # ---- pool
    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=self.connect_timeout, autocommit=False)

    def _acquire(self) -> psycopg.Connection:
        if not self._slots.acquire(timeout=self.connect_timeout):
            raise TimeoutError("database connection pool exhausted")
        try:
            while True:
                try:
                    conn = self._pool.get_nowait()
                except queue.Empty:
                    break
                if not conn.closed:
                    return conn
                with self._lock:
                    self._count -= 1
            with self._lock:
                self._count += 1
            try:
                return self._connect()
            except Exception:
                with self._lock:
                    self._count -= 1
                raise
        except Exception:
            self._slots.release()
            raise

    def _release(self, conn: psycopg.Connection) -> None:
        try:
            if conn.closed:
                with self._lock:
                    self._count -= 1
                return
            try:
                if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                    conn.rollback()
            except Exception:
                try:
                    conn.close()
                finally:
                    with self._lock:
                        self._count -= 1
                return
            self._pool.put(conn)
        finally:
            self._slots.release()

    def close(self) -> None:
        while True:
            try:
                self._pool.get_nowait().close()
                with self._lock:
                    self._count -= 1
            except queue.Empty:
                break

    # ---- access modes
    @contextmanager
    def service(self) -> Iterator[psycopg.Connection]:
        conn = self._acquire()
        try:
            with conn.transaction():
                yield conn
        finally:
            self._release(conn)

    @contextmanager
    def as_user(self, user_id: str, email: Optional[str] = None) -> Iterator[psycopg.Connection]:
        conn = self._acquire()
        try:
            with conn.transaction():
                claims = {"sub": str(user_id), "role": "authenticated", "aud": "authenticated"}
                if email:
                    claims["email"] = email
                conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
                conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
                conn.execute("select set_config('request.jwt.claim.role', 'authenticated', true)")
                conn.execute("set local role authenticated")
                yield conn
        finally:
            self._release(conn)

    @contextmanager
    def as_role(self, role: str) -> Iterator[psycopg.Connection]:
        """Tests: act as anon / service_role etc. (no JWT claims)."""
        conn = self._acquire()
        try:
            with conn.transaction():
                conn.execute(psycopg.sql.SQL("set local role {}").format(psycopg.sql.Identifier(role)))
                yield conn
        finally:
            self._release(conn)

    def ping(self) -> tuple:
        """Connectivity AND the RLS path (SET ROLE authenticated must work)."""
        try:
            with self.service() as c:
                c.execute("select 1")
            with self.as_user("00000000-0000-0000-0000-000000000000") as c:
                c.execute("select 1")
            return True, "ok"
        except Exception as e:
            return False, safe_exc(e)
