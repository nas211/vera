"""X (Twitter) via publish.twitter.com/oembed — the free read path.

It answers exactly two questions about a public post, with no credentials:
WHO wrote it, and WHAT it says. That is enough to verify authorship of a reply,
a quote or an original post, which is why the quest rewards are weighted toward
those and away from likes.

What it CANNOT tell us is what a post was replying to. X does not put the
parent in the oEmbed payload. A quote carries the quoted URL inside the
rendered html and IS checkable; a plain reply is not, so reply quests carry a
required marker (a campaign tag or our @handle) and the verifier reports
`parent_confirmed: false` rather than pretending.
"""
from __future__ import annotations

import html as htmllib
import re
import urllib.parse

from .. import config
from .base import REJECTED, UNKNOWN, VERIFIED, Result, get_json, norm_handle

OEMBED = "https://publish.twitter.com/oembed"
STATUS_RE = re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{1,25})", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def parse_url(url: str):
    """(handle, post_id) from any x.com/twitter.com status URL, or (None, None)."""
    m = STATUS_RE.search(url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def canonical(url: str) -> str:
    h, pid = parse_url(url)
    return f"https://x.com/{h}/status/{pid}" if pid else (url or "").strip()


def fetch(url: str) -> tuple:
    """(post_dict|None, error). post = {author, text, url, id, html}."""
    handle, pid = parse_url(url)
    if not pid:
        return None, "not an x.com post URL"
    q = urllib.parse.urlencode({"url": f"https://twitter.com/{handle}/status/{pid}",
                                "omit_script": "1", "dnt": "true"})
    data, code, err = get_json(f"{OEMBED}?{q}")
    if not data:
        # 404 here means deleted, private, or never existed — all of which are
        # "we cannot see it", not "they are lying".
        return None, err or f"http {code}"
    raw = data.get("html") or ""
    text = htmllib.unescape(TAG_RE.sub(" ", raw))
    return {"author": norm_handle(data.get("author_name") or handle),
            "author_url": data.get("author_url") or "",
            "text": " ".join(text.split()),
            "html": raw, "url": data.get("url") or url, "id": pid}, None


def verify_post(proof_url: str, *, handle: str, marker: str = "",
                target_post_id: str = "", target_handle: str = "") -> Result:
    """Did `handle` write `proof_url`, and does it carry what the quest asked for?"""
    post, err = fetch(proof_url)
    if not post:
        return Result(UNKNOWN, "x_oembed", {"error": err, "url": proof_url})

    claimed = norm_handle(handle)
    if claimed and post["author"] != claimed:
        return Result(REJECTED, "x_oembed",
                      {"reason": "that post was written by someone else",
                       "author": post["author"], "expected": claimed})

    low = post["text"].lower()
    if marker and marker.lower() not in low:
        return Result(REJECTED, "x_oembed",
                      {"reason": f"the post does not mention {marker}", "text": post["text"][:280]})

    parent = False
    if target_post_id:
        parent = target_post_id in post["html"]      # true for quotes, not for plain replies
    if target_handle and not parent:
        parent = norm_handle(target_handle) in low.replace("@", " @").replace("/", " ")

    return Result(VERIFIED, "x_oembed",
                  {"author": post["author"], "text": post["text"][:280],
                   "post_id": post["id"], "parent_confirmed": bool(parent)})


def verify_handle_nonce(proof_url: str, *, handle: str, nonce: str) -> Result:
    """Handle ownership: only the account holder can put our nonce in a post
    that X then attributes to that account."""
    return verify_post(proof_url, handle=handle, marker=nonce)


def probe() -> dict:
    """Live check against a post that has existed since 2006."""
    post, err = fetch("https://x.com/jack/status/20")
    return {"available": bool(post), "detail": err or "ok",
            "reads": ["author", "text"], "cost": "free, no credentials"}
