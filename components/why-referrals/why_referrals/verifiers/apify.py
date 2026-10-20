"""Apify — the paid scraper of last resort, OFF by default.

The marketing stack already pays Apify for engagement scouting at roughly a
third of a cent per post, so the account and the token exist. It is the only
route to "who liked this" on either platform without an X API tier.

It stays off until an actor is named (WHYR_APIFY_LIKES_ACTOR) because a
verifier that silently costs money per claim is a bill someone discovers later.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from functools import lru_cache
from pathlib import Path

from .. import config
from .base import REJECTED, UNKNOWN, VERIFIED, Result, get_json, norm_handle

API = "https://api.apify.com/v2"
ENV_FILE = os.environ.get("WHYR_IG_ENV_FILE", "/opt/soma-marketing/.env.runtime")


@lru_cache(maxsize=1)
def _file_token() -> str:
    try:
        for line in Path(ENV_FILE).read_text().splitlines():
            if line.strip().startswith("APIFY_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def token() -> str:
    return (os.environ.get("WHYR_APIFY_TOKEN") or os.environ.get("APIFY_TOKEN") or _file_token()).strip()


def actor() -> str:
    return (os.environ.get("WHYR_APIFY_LIKES_ACTOR") or "").strip()


def available() -> bool:
    return bool(token() and actor())


def _run_sync(input_obj: dict, timeout: float = 120.0):
    """Run the actor and return its dataset items. One synchronous call: a
    verification that needs a callback needs a public URL, and this service is
    loopback-only by design."""
    url = (f"{API}/acts/{urllib.parse.quote(actor(), safe='')}/run-sync-get-dataset-items"
           f"?token={urllib.parse.quote(token())}&timeout={int(timeout)}")
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(input_obj).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "why-referrals/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def verify_engagement(post_url: str, handle: str, kind: str = "LIKE") -> Result:
    if not available():
        return Result(UNKNOWN, "apify",
                      {"reason": "apify verifier not configured (needs WHYR_APIFY_LIKES_ACTOR)"})
    items, err = _run_sync({"postUrls": [post_url], "urls": [post_url], "type": kind.lower(),
                            "resultsLimit": 1000})
    if items is None:
        return Result(UNKNOWN, "apify", {"error": err})
    h = norm_handle(handle)
    users = {norm_handle(i.get("username") or i.get("userName") or i.get("handle") or "")
             for i in items if isinstance(i, dict)}
    users.discard("")
    return Result(VERIFIED if h in users else REJECTED, "apify",
                  {"n_seen": len(users), "actor": actor(), "kind": kind})


def probe() -> dict:
    return {"available": available(),
            "detail": ("ok" if available() else
                       ("token present, no actor configured" if token() else "no APIFY_TOKEN")),
            "reads": ["likers", "commenters"] if available() else [],
            "cost": "paid per run (~$0.003/post on the marketing account)"}
