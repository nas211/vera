"""X API v2 — the PAID read path, used only if a bearer token is configured.

Likes, reposts and follows have no free read. The endpoints exist
(`/2/tweets/:id/liking_users`, `/2/tweets/:id/retweeted_by`,
`/2/users/:id/following`) but they sit behind a paid tier, and this box
currently has posting credentials only.

So this module is OFF unless WHYR_X_BEARER is set, and when it is off it says
UNKNOWN — never REJECTED. Rejecting a real like because we did not buy the API
is the one failure mode that makes people stop doing the task.
"""
from __future__ import annotations

import os
import urllib.parse

from .base import REJECTED, UNKNOWN, VERIFIED, Result, get_json, norm_handle

API = "https://api.twitter.com/2"


def _bearer() -> str:
    return (os.environ.get("WHYR_X_BEARER") or os.environ.get("X_BEARER_TOKEN") or "").strip()


def available() -> bool:
    return bool(_bearer())


def _h():
    return {"Authorization": f"Bearer {_bearer()}"}


def _paged_usernames(url: str, cap: int = 1000):
    """Walk the pagination once, up to `cap` users. (set|None, error)."""
    out, token, seen = set(), None, 0
    while seen < cap:
        u = url + (("&" if "?" in url else "?") + f"pagination_token={token}" if token else "")
        data, code, err = get_json(u, _h())
        if not data or "data" not in data:
            if data and data.get("meta", {}).get("result_count") == 0:
                return out, None                       # genuinely nobody
            return None, err or f"http {code}"
        for u_ in data["data"]:
            out.add(norm_handle(u_.get("username")))
        seen += len(data["data"])
        token = (data.get("meta") or {}).get("next_token")
        if not token:
            break
    return out, None


def _engaged(kind: str, post_id: str, handle: str) -> Result:
    if not available():
        return Result(UNKNOWN, "x_api", {"reason": "no X API bearer configured"})
    path = {"LIKE": "liking_users", "REPOST": "retweeted_by"}[kind]
    users, err = _paged_usernames(f"{API}/tweets/{post_id}/{path}?max_results=100")
    if users is None:
        return Result(UNKNOWN, "x_api", {"error": err, "endpoint": path})
    h = norm_handle(handle)
    return Result(VERIFIED if h in users else REJECTED, "x_api",
                  {"endpoint": path, "n_seen": len(users), "handle": h})


def verify_like(post_id: str, handle: str) -> Result:
    return _engaged("LIKE", post_id, handle)


def verify_repost(post_id: str, handle: str) -> Result:
    return _engaged("REPOST", post_id, handle)


def verify_follow(handle: str, target_handle: str) -> Result:
    """Reads the FOLLOWER's following list, not our follower list: ours can run
    to tens of thousands of rows to answer one question."""
    if not available():
        return Result(UNKNOWN, "x_api", {"reason": "no X API bearer configured"})
    data, code, err = get_json(f"{API}/users/by/username/{urllib.parse.quote(norm_handle(handle))}", _h())
    uid = ((data or {}).get("data") or {}).get("id")
    if not uid:
        return Result(UNKNOWN, "x_api", {"error": err or "user lookup failed", "code": code})
    users, err = _paged_usernames(f"{API}/users/{uid}/following?max_results=1000", cap=5000)
    if users is None:
        return Result(UNKNOWN, "x_api", {"error": err})
    return Result(VERIFIED if norm_handle(target_handle) in users else REJECTED, "x_api",
                  {"endpoint": "following", "n_seen": len(users)})


def probe() -> dict:
    if not available():
        return {"available": False, "detail": "WHYR_X_BEARER not set",
                "reads": [], "cost": "paid tier (Basic+) required for likes/reposts/follows"}
    data, code, err = get_json(f"{API}/tweets/20", _h())
    ok = bool(data and data.get("data"))
    return {"available": ok, "detail": err or f"http {code}",
            "reads": ["likes", "reposts", "follows"], "cost": "paid X API tier"}
