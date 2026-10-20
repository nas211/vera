"""WHY Referrals — the shared referral + quest engine behind WHY TRADE.

ONE PERSON, ONE CODE, ONE BALANCE. A human who arrives through the Telegram bot
and a human who signs up on whytradingbot.com/predict are the same customer, so
the identity this package works in is a `person`, not a Telegram id and not a
Predict user row. Each host binds its own account to a person through
`person_subjects`; the balance follows the person across both.

WHY A SEPARATE PACKAGE AND NOT A TABLE IN EITHER HOST: the two hosts are
different processes on different interpreters with different sandboxes (the bot
is a long-poll loop on the system python, Predict is uvicorn in its own venv).
Whichever one owned the ledger would make the other one a client of a private
schema, and a schema that two products edit from two directions stops being
anybody's. Here the rules live in one place, both hosts call the same facade,
and the ledger file is the single record.

STDLIB ONLY. It has to import cleanly into both hosts, and one of them is a
system interpreter with no venv.

$Y IS NOT MONEY, AND NOT A TOKEN YET. It is a points ledger for the token the
site teases. Nothing in this package moves cash or crypto; the only things a
balance converts into are (a) internal play-credits in WHY Predict, which have
no value themselves, and (b) subscription time, which is granted by the bot's
own billing code, never here. See rules.py for the rates and RULES.md for why.
"""
from __future__ import annotations

from .engine import Referrals, ReferralError
from .store import Store

__all__ = ["Referrals", "ReferralError", "Store", "__version__"]
__version__ = "1.0.0"
