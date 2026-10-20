"""Anti-abuse for referrals. Every check answers one of two questions:

  REFUSE — this bind is not a referral at all (yourself, a second code, an
           account that already existed, a loop). Nothing is recorded as owed.
  HOLD   — this looks like a referral and might be a real one, but something
           about it wants a human. The award is PARKED, not refused.

The distinction matters commercially. Two people on one home wifi are the most
ordinary referral there is, and a system that silently refuses them teaches its
best customers that the programme does not pay.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import config


@dataclass
class Screen:
    refuse: str = ""                       # non-empty -> reject the bind outright
    flags: list = field(default_factory=list)   # [(kind, detail_dict)]

    @property
    def ok(self) -> bool:
        return not self.refuse

    @property
    def hold(self) -> bool:
        return bool(self.flags) and bool(config.HOLD_ON_FLAG)


def flag(con, kind: str, *, person_id=None, ip=None, detail=None, ts=None) -> int:
    cur = con.execute("INSERT INTO abuse_flags(kind,person_id,ip,detail,ts) VALUES(?,?,?,?,?)",
                      (kind, person_id, ip, json.dumps(detail or {}), ts or time.time()))
    return cur.lastrowid


def _chain_contains(con, start_person: int, target: int, max_depth: int = 25) -> bool:
    """Walk UP the referral chain from `start_person`. True if `target` is one
    of the people who referred them, at any depth."""
    seen, cur, depth = set(), start_person, 0
    while cur and depth < max_depth:
        if cur in seen:
            return False
        seen.add(cur)
        r = con.execute("SELECT referrer_id FROM referrals WHERE referee_id=?", (cur,)).fetchone()
        if not r:
            return False
        if r["referrer_id"] == target:
            return True
        cur, depth = r["referrer_id"], depth + 1
    return False


def screen_bind(con, *, referrer_id: int, referee_id: int, ip: str = None,
                account_age_sec: float = 0.0, now: float = None) -> Screen:
    now = now or time.time()
    s = Screen()

    if referrer_id == referee_id:
        s.refuse = "you cannot refer yourself"
        return s

    ref = con.execute("SELECT suspended, merged_into FROM persons WHERE id=?", (referrer_id,)).fetchone()
    if not ref:
        s.refuse = "unknown code"
        return s
    if ref["suspended"]:
        s.refuse = "that code is not active"
        return s

    if con.execute("SELECT 1 FROM referrals WHERE referee_id=?", (referee_id,)).fetchone():
        s.refuse = "this account was already referred"
        return s

    # A -> B -> A. Pays both sides forever off two accounts if you let it.
    if _chain_contains(con, referrer_id, referee_id):
        s.refuse = "circular referral"
        return s

    # The referee must be NEW. Attaching an account that has been around for a
    # week is not a referral, it is a code applied retroactively.
    if account_age_sec and account_age_sec > config.BIND_MAX_ACCOUNT_AGE_SEC:
        s.refuse = "referral codes apply to new accounts only"
        return s

    if ip:
        n_ip = con.execute("SELECT COUNT(*) c FROM referrals WHERE ip=? AND bound_at>?",
                           (ip, now - 86400)).fetchone()["c"]
        if n_ip >= config.BIND_MAX_PER_IP_DAY:
            s.flags.append(("BIND_IP_FLOOD", {"ip": ip, "binds_24h": n_ip}))
        # The referrer signing up their own second account from their own address.
        same = con.execute(
            "SELECT 1 FROM referrals WHERE referrer_id=? AND ip=?", (referrer_id, ip)).fetchone()
        if same:
            s.flags.append(("SHARED_IP_WITH_REFERRER", {"ip": ip}))

    n_day = con.execute("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND bound_at>?",
                        (referrer_id, now - 86400)).fetchone()["c"]
    if n_day >= config.REFERRALS_MAX_PER_DAY:
        s.flags.append(("REFERRER_VELOCITY", {"referrals_24h": n_day}))

    return s


def record(con, screen: Screen, *, person_id: int, ip: str = None, now: float = None):
    for kind, detail in screen.flags:
        flag(con, kind, person_id=person_id, ip=ip, detail=detail, ts=now)
