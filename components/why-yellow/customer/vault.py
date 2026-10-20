"""Supabase Vault access — server-side only.

API surface used (identical on hosted Supabase and on the test shim):
  select vault.create_secret(new_secret, new_name, new_description) -> uuid
  select vault.update_secret(secret_id, new_secret => ...)
  select decrypted_secret from vault.decrypted_secrets where id = ...
  delete from vault.secrets where id = ...
Plaintext never leaves the server process: read() registers the value with the log
redactor before returning it, and callers hold it in a SecretBox whose repr is opaque."""
from __future__ import annotations
import uuid
from typing import Optional, Tuple

from .log import register_secret, safe_exc


class SecretBox:
    """Holds a secret string; never prints it."""
    __slots__ = ("_v",)

    def __init__(self, value: str):
        self._v = str(value)
        register_secret(self._v)

    def reveal(self) -> str:
        return self._v

    def __repr__(self):
        return "SecretBox(***)"

    __str__ = __repr__

    def __eq__(self, other):
        return isinstance(other, SecretBox) and other._v == self._v

    def __hash__(self):
        return hash(("SecretBox", self._v))


class VaultError(RuntimeError):
    pass


class Vault:
    @staticmethod
    def create(conn, name: str, value: str, description: str = "") -> str:
        register_secret(value)
        try:
            row = conn.execute("select vault.create_secret(%s, %s, %s) as id", (value, name, description)).fetchone()
        except Exception as e:
            raise VaultError(safe_exc(e)) from e
        return str(row["id"])

    @staticmethod
    def update(conn, secret_id: str, value: str) -> None:
        register_secret(value)
        try:
            conn.execute("select vault.update_secret(%s, new_secret => %s)", (uuid.UUID(secret_id), value))
        except Exception as e:
            raise VaultError(safe_exc(e)) from e

    @staticmethod
    def read(conn, secret_id: str) -> Optional[SecretBox]:
        try:
            row = conn.execute("select decrypted_secret from vault.decrypted_secrets where id = %s", (uuid.UUID(secret_id),)).fetchone()
        except Exception as e:
            raise VaultError(safe_exc(e)) from e
        if row is None or row["decrypted_secret"] is None:
            return None
        return SecretBox(row["decrypted_secret"])

    @staticmethod
    def delete(conn, secret_id: str) -> None:
        try:
            conn.execute("delete from vault.secrets where id = %s", (uuid.UUID(secret_id),))
        except Exception as e:
            raise VaultError(safe_exc(e)) from e

    @staticmethod
    def health(conn) -> Tuple[bool, str]:
        """Non-destructive: the functions exist and decryption of the view works."""
        try:
            n = conn.execute("select count(*) as n from pg_proc p join pg_namespace s on s.oid = p.pronamespace "
                             "where s.nspname = 'vault' and p.proname in ('create_secret', 'update_secret')").fetchone()["n"]
            if int(n) < 2:
                return False, "vault_functions_missing"
            conn.execute("select id from vault.decrypted_secrets limit 1").fetchall()
            return True, "ok"
        except Exception as e:
            return False, safe_exc(e)
