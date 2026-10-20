"""The reward schedule. Arithmetic only — no database, no side effects, so the
question "what is this worth" can be answered and tested without a ledger.

WHY THE NUMBERS ARE SHAPED THIS WAY
  * A signup pays a little, a USED account pays more, and a PAID account pays
    the most. Paying the most for the signup is how you buy a list of empty
    accounts.
  * The referee gets a welcome too. A one-sided referral is a thing you ask
    people to do for you; a two-sided one is a thing they can offer a friend.
  * Commission RECURS on renewals. The referrer keeps earning while the
    customer keeps paying, which is the only version of this that makes anyone
    care whether the person they sent is happy.
  * Tier multiplies EARNINGS, and tier comes from LIFETIME earned. Spending
    your balance can never demote you — a status you can lose by using the
    thing it bought is a trap, not a reward.
"""
from __future__ import annotations

import math

from . import config


def tier_for(lifetime_y: int):
    """(name, threshold, multiplier) for a lifetime $Y total."""
    out = config.TIERS[0]
    for t in config.TIERS:
        if lifetime_y >= t[1]:
            out = t
    return out


def next_tier(lifetime_y: int):
    for t in config.TIERS:
        if lifetime_y < t[1]:
            return t
    return None


def multiplier(lifetime_y: int) -> float:
    return tier_for(lifetime_y)[2]


def _scaled(base: int, lifetime_y: int) -> int:
    """Apply the tier multiplier. Rounds DOWN: a reward that rounds up is a
    rounding error someone can farm by claiming many small things."""
    return int(math.floor(base * multiplier(lifetime_y)))


def signup_award(lifetime_y: int) -> int:
    return _scaled(config.Y_SIGNUP_REFERRER, lifetime_y)


def welcome_award() -> int:
    """The referee's own bonus. NOT tier-scaled: they have no tier yet, and
    scaling it by the REFERRER's tier would pay strangers different amounts for
    the same act depending on who invited them."""
    return int(config.Y_SIGNUP_REFEREE)


def qualified_award(lifetime_y: int) -> int:
    return _scaled(config.Y_QUALIFIED_REFERRER, lifetime_y)


def commission_award(usd: float, lifetime_y: int) -> int:
    """$Y on an approved payment. `usd` is what actually cleared, never what
    was invoiced."""
    if usd <= 0:
        return 0
    return _scaled(int(round(usd * config.SUB_COMMISSION_PCT / 100.0 * config.Y_PER_USD)), lifetime_y)


def milestone_award(n_qualified: int, lifetime_y: int) -> int:
    """Paid on the Nth, 2Nth, ... qualified referral; 0 otherwise."""
    every = int(config.MILESTONE_EVERY)
    if every <= 0 or n_qualified <= 0 or n_qualified % every:
        return 0
    return _scaled(config.Y_MILESTONE, lifetime_y)


def quest_award(base: int, *, attested: bool, lifetime_y: int) -> int:
    """A task we can verify pays in full. A task we can only be TOLD about
    (a like, a follow — X sells that read and we do not buy it) pays a
    fraction, because the honest answer is that we do not know."""
    if attested:
        base = int(math.floor(base * config.ATTESTED_REWARD_PCT / 100.0))
    return _scaled(base, lifetime_y)


# ---------------------------------------------------------------- spending
def credits_for(y: int) -> int:
    return int(y) * int(config.CREDITS_PER_Y)


def sub_days_cost(days: int) -> int:
    return int(days) * int(config.Y_PER_SUB_DAY)


def max_sub_days(balance: int) -> int:
    per = int(config.Y_PER_SUB_DAY)
    return min(int(config.MAX_SUB_DAYS), balance // per if per else 0)


def rate_card() -> dict:
    """Everything a customer is entitled to see before they start earning."""
    return {
        "earn": {
            "signup_referrer": config.Y_SIGNUP_REFERRER,
            "signup_referee": config.Y_SIGNUP_REFEREE,
            "qualified_referrer": config.Y_QUALIFIED_REFERRER,
            "qualify_predictions": config.QUALIFY_PREDICTIONS,
            "commission_pct": config.SUB_COMMISSION_PCT,
            "y_per_usd": config.Y_PER_USD,
            "milestone_every": config.MILESTONE_EVERY,
            "milestone_y": config.Y_MILESTONE,
        },
        "spend": {
            "credits_per_y": config.CREDITS_PER_Y,
            "min_convert_y": config.MIN_CONVERT_Y,
            "y_per_sub_day": config.Y_PER_SUB_DAY,
            "min_sub_days": config.MIN_SUB_DAYS,
            "max_sub_days": config.MAX_SUB_DAYS,
        },
        "tiers": [{"name": n, "from_lifetime_y": t, "multiplier": m} for n, t, m in config.TIERS],
        "notice": ("$Y points are a loyalty balance, not money and not yet a token. "
                   "They are not redeemable for cash or crypto."),
    }
