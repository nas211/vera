"""Shared verifier vocabulary and a small HTTP helper (stdlib only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .. import config

VERIFIED = "VERIFIED"
REJECTED = "REJECTED"
UNKNOWN = "UNKNOWN"       # we could not look; a human decides


@dataclass
class Result:
    status: str = UNKNOWN
    verifier: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED


def get_json(url: str, headers: dict = None, timeout: float = None):
    """(json, http_status, error). Never raises — a verifier that throws on a
    502 turns an outage into a wave of rejected claims."""
    req = urllib.request.Request(url, headers={"User-Agent": "why-referrals/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT_SEC) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return json.loads(body), r.status, None
            except ValueError:
                return None, r.status, "not json"
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace")), e.code, f"http {e.code}"
        except Exception:
            return None, e.code, f"http {e.code}"
    except Exception as e:
        return None, 0, f"{type(e).__name__}: {e}"


def norm_handle(h: str) -> str:
    return (h or "").strip().lstrip("@").lower()
