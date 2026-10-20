"""Referral codes and one-time tokens.

Codes are RANDOM, not derived from the person id. A derived code leaks how many
customers exist and lets anyone enumerate them; random costs one retry loop.

The alphabet excludes 0, 1, I, L, O and U, so a code never contains a character
that is confusable with another one. That is a decision made at GENERATION time
precisely so lookup does not need to guess: there is no fuzzy match here, and
adding one would mean two different real codes could resolve to each other.
"""
from __future__ import annotations

import secrets

from . import config


def random_code() -> str:
    body = "".join(secrets.choice(config.CODE_ALPHABET) for _ in range(config.CODE_LEN))
    return config.CODE_PREFIX + body


def normalise(code: str) -> str:
    """What a user typed -> what we look up.

    People do not send back the code, they send back whatever they were holding:
    the whole invite URL, the ?r= query string, the bot's `r_WHYABC123` deep-link
    payload, or the code with the prefix dropped. All five are the same code.
    """
    c = (code or "").strip()
    if "?" in c:
        # A pasted invite link. The code is in the query, not the path — take
        # it from there before the path-stripping below throws the query away.
        head, _, query = c.partition("?")
        picked = ""
        for part in query.split("&"):
            k, _, v = part.partition("=")
            if k.strip().lower() in ("r", "ref", "code", "start", "invite") and v:
                picked = v
                break
        c = picked or head
    if "/" in c:
        c = c.rstrip("/").split("/")[-1]
    c = c.replace(" ", "").replace("_", "-").upper()
    if c.startswith("R-"):                # telegram deep link: /start r_WHYABC123
        c = c[2:]
    pre = config.CODE_PREFIX.upper()
    if pre and not c.startswith(pre):
        c = pre + c.removeprefix(pre.rstrip("-")).lstrip("-")
    return c


def looks_like_code(code: str) -> bool:
    c = normalise(code)
    body = c[len(config.CODE_PREFIX):]
    return len(body) == config.CODE_LEN and all(ch in config.CODE_ALPHABET for ch in body)


def token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def nonce() -> str:
    return "".join(secrets.choice(config.CODE_ALPHABET) for _ in range(config.HANDLE_NONCE_LEN))
