"""Every tunable. Each is overridable by an env var of the same name prefixed
WHYR_ (e.g. WHYR_DB_PATH, WHYR_Y_SIGNUP_REFERRER).

Defaults are deliberately conservative: this ledger will one day be read as a
claim on a token, so it is easier to raise a reward than to explain why an
early balance was inflated.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default, cast=None):
    raw = os.environ.get("WHYR_" + name)
    if raw is None:
        return default
    if cast is bool or isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return (cast or type(default))(raw)


# ------------------------------------------------------------------ paths
# /var/lib and not either host's tree: neither product owns this file, and a
# host being redeployed must not take the ledger with it.
STATE_DIR = Path(_env("STATE_DIR", "/var/lib/why-referrals"))
DB_PATH = Path(_env("DB_PATH", str(STATE_DIR / "referrals.db")))

# ------------------------------------------------------------------ codes
CODE_PREFIX = _env("CODE_PREFIX", "WHY-")
CODE_LEN = _env("CODE_LEN", 6)
# Crockford-ish: no I, L, O, U, 0, 1 — a referral code gets read off a phone
# screen and typed into another one, and 0/O is the classic lost signup.
CODE_ALPHABET = _env("CODE_ALPHABET", "23456789ABCDEFGHJKMNPQRSTVWXYZ")

# ------------------------------------------------------------------ $Y awards
Y_SIGNUP_REFERRER = _env("Y_SIGNUP_REFERRER", 50)      # someone joined on your code
Y_SIGNUP_REFEREE = _env("Y_SIGNUP_REFEREE", 25)        # ...and a welcome for them
Y_QUALIFIED_REFERRER = _env("Y_QUALIFIED_REFERRER", 100)   # they actually used the product
QUALIFY_PREDICTIONS = _env("QUALIFY_PREDICTIONS", 3)   # what "actually used it" means
# Paid conversion: the referrer earns a percentage of what the referee paid,
# denominated in $Y. Recurs on every approved renewal, not just the first.
SUB_COMMISSION_PCT = _env("SUB_COMMISSION_PCT", 20.0)
Y_PER_USD = _env("Y_PER_USD", 10.0)
# Milestones: every Nth qualified referral pays a bonus on top.
MILESTONE_EVERY = _env("MILESTONE_EVERY", 5)
Y_MILESTONE = _env("Y_MILESTONE", 250)

# ------------------------------------------------------------------ tiers
# Same four names the marketing site already uses for $Y holdings, so a
# customer meets one ladder, not two. Thresholds are LIFETIME $Y EARNED —
# spending your balance must never demote you.
TIERS = (
    ("SPARK", 0, 1.00),
    ("EMBER", 500, 1.10),
    ("FORGE", 2500, 1.25),
    ("INFERNO", 10000, 1.50),
)

# ------------------------------------------------------------------ spending
# $Y -> WHY Credits (Predict play-money, itself worth nothing).
CREDITS_PER_Y = _env("CREDITS_PER_Y", 10)
MIN_CONVERT_Y = _env("MIN_CONVERT_Y", 50)
# $Y -> subscription days. Premium lists at $99/30d = $3.30/day; at Y_PER_USD
# that is 33 $Y of value, priced here at 40 so referring is worth more than
# grinding. THE BOT GRANTS THE DAYS — this package only debits the points.
Y_PER_SUB_DAY = _env("Y_PER_SUB_DAY", 40)
MIN_SUB_DAYS = _env("MIN_SUB_DAYS", 7)
MAX_SUB_DAYS = _env("MAX_SUB_DAYS", 90)

# ------------------------------------------------------------------ anti-abuse
BIND_MAX_PER_IP_DAY = _env("BIND_MAX_PER_IP_DAY", 3)      # signups on any code, per IP
REFERRALS_MAX_PER_DAY = _env("REFERRALS_MAX_PER_DAY", 20)  # one referrer, per day
# A referee has to be a NEW account. Binding an account that has been around
# for longer than this is someone attaching an existing user to a code.
BIND_MAX_ACCOUNT_AGE_SEC = _env("BIND_MAX_ACCOUNT_AGE_SEC", 3600.0)
# Rewards for a flagged referral are HELD, not refused: the honest half of a
# shared-IP pair (a couple, an office, a phone on wifi) is a real customer.
HOLD_ON_FLAG = _env("HOLD_ON_FLAG", True)
LINK_TOKEN_TTL_SEC = _env("LINK_TOKEN_TTL_SEC", 900.0)

# ------------------------------------------------------------------ quests
# X ONLY (owner's call, 2026-08-26). The Instagram verifier still exists and
# works — it reads comments on our own media with the marketing stack's token —
# but WHY does not run an Instagram account, and a task nobody can complete is
# worse than no task. Add "instagram" here to turn it back on; nothing else
# needs to change.
SOCIAL_PLATFORMS = tuple(x.strip().lower() for x in
                         _env("SOCIAL_PLATFORMS", "x").split(",") if x.strip())

QUEST_CLAIM_COOLDOWN_SEC = _env("QUEST_CLAIM_COOLDOWN_SEC", 30.0)
QUEST_MAX_CLAIMS_PER_DAY = _env("QUEST_MAX_CLAIMS_PER_DAY", 25)
# Handle ownership: the nonce a user has to put in a post before we believe an
# X/IG handle is theirs.
HANDLE_NONCE_LEN = _env("HANDLE_NONCE_LEN", 6)
HANDLE_NONCE_TTL_SEC = _env("HANDLE_NONCE_TTL_SEC", 86400.0)
# Tasks we cannot read for free (likes, follows) are ATTESTED: the user says
# they did it, we pay less, and a spot-check can claw it back.
ATTESTED_REWARD_PCT = _env("ATTESTED_REWARD_PCT", 50.0)
SPOT_CHECK_PCT = _env("SPOT_CHECK_PCT", 10.0)

HTTP_TIMEOUT_SEC = _env("HTTP_TIMEOUT_SEC", 15.0)


def as_dict() -> dict:
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in globals().items()
            if k.isupper() and not k.startswith("_")}


# ------------------------------------------------------------------ invite links
# What a customer actually shares. Both forms carry the SAME code, so a person
# who was sent a Telegram link and signs up on the web is still attributed.
WEB_INVITE_URL = _env("WEB_INVITE_URL", "https://whytradingbot.com/predict?r={code}")
TG_BOT_USERNAME = _env("TG_BOT_USERNAME", "")          # without the @
TG_INVITE_URL = _env("TG_INVITE_URL", "https://t.me/{bot}?start=r_{code}")


def invite_links(code: str) -> dict:
    body = code[len(CODE_PREFIX):] if code.startswith(CODE_PREFIX) else code
    out = {"web": WEB_INVITE_URL.format(code=code)}
    if TG_BOT_USERNAME:
        out["telegram"] = TG_INVITE_URL.format(bot=TG_BOT_USERNAME, code=body)
    return out
