"""Instagram Graph — comments on OUR OWN media, which we are entitled to read
with the token the marketing stack already holds.

Graph gives us the commenter's username on our own posts, so IG COMMENT quests
verify for free and in full. It does NOT give us who liked a post (Meta removed
the like-lister); IG likes are therefore attested or scraped, never claimed as
verified.

Credentials are READ FROM THE MARKETING STACK'S ENV FILE rather than copied
here, so there is one place a rotated IG token has to be pasted. Point
WHYR_IG_ENV_FILE somewhere else, or set WHYR_IG_ACCESS_TOKEN directly, and this
never touches that file.
"""
from __future__ import annotations

import os
import urllib.parse
from functools import lru_cache
from pathlib import Path

from .base import REJECTED, UNKNOWN, VERIFIED, Result, get_json, norm_handle

ENV_FILE = os.environ.get("WHYR_IG_ENV_FILE", "/opt/soma-marketing/.env.runtime")
GRAPH = "https://graph.facebook.com"


@lru_cache(maxsize=1)
def _file_env() -> dict:
    out = {}
    try:
        for line in Path(ENV_FILE).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _cred(name: str, *fallbacks) -> str:
    for k in (("WHYR_" + name), name, *fallbacks):
        v = os.environ.get(k)
        if v:
            return v.strip()
    env = _file_env()
    for k in (name, *fallbacks):
        if env.get(k):
            return env[k]
    return ""


def token() -> str:
    return _cred("IG_ACCESS_TOKEN")


def api_version() -> str:
    return _cred("IG_GRAPH_API_VERSION") or "v21.0"


def available() -> bool:
    return bool(token())


def commenters(media_id: str, cap: int = 2000):
    """(set of usernames|None, error) for one of our own media objects."""
    if not available():
        return None, "no IG access token"
    url = (f"{GRAPH}/{api_version()}/{urllib.parse.quote(media_id)}/comments"
           f"?fields=username,text&limit=100&access_token={urllib.parse.quote(token())}")
    out, seen = set(), 0
    while url and seen < cap:
        data, code, err = get_json(url)
        if not data or "data" not in data:
            return None, err or f"http {code}"
        for c in data["data"]:
            if c.get("username"):
                out.add(norm_handle(c["username"]))
        seen += len(data["data"])
        url = ((data.get("paging") or {}).get("next")) or ""
    return out, None


def verify_comment(media_id: str, handle: str) -> Result:
    users, err = commenters(media_id)
    if users is None:
        return Result(UNKNOWN, "ig_graph", {"error": err, "media_id": media_id})
    h = norm_handle(handle)
    return Result(VERIFIED if h in users else REJECTED, "ig_graph",
                  {"n_comments_seen": len(users), "handle": h})


def probe() -> dict:
    if not available():
        return {"available": False, "detail": f"no IG token (looked in env and {ENV_FILE})",
                "reads": [], "cost": "free with our own token"}
    uid = _cred("IG_USER_ID")
    data, code, err = get_json(f"{GRAPH}/{api_version()}/{uid or 'me'}?fields=id,username"
                               f"&access_token={urllib.parse.quote(token())}")
    ok = bool(data and data.get("id"))
    return {"available": ok, "detail": err or (data or {}).get("username") or f"http {code}",
            "reads": ["comments on our own media"], "cost": "free with our own token",
            "not_available": ["who liked a post (Meta removed it)"]}
