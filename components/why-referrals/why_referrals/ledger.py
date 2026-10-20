"""The $Y ledger. Balance is SUM(delta) — there is no balance column, because a
cached balance and a ledger disagree exactly once and then permanently.

Every earn carries an `award_key` naming the thing it pays for
('SIGNUP:ref:12', 'SUB:payment:57', 'QUEST:claim:900'). The column is UNIQUE,
so replaying an event — a poll that reads the same payment twice, an admin
double-tap, a restart mid-write — inserts nothing and pays nothing. Idempotency
is a constraint here, not a convention someone has to remember at each call.
"""
from __future__ import annotations

import sqlite3
import time

from . import config


def _who(person):
    """(sql_fragment, params) for one person id or a whole merged group.

    A merge NEVER rewrites ledger rows — the trigger below forbids it, and that
    is the point: history stays where it was written and the merge is expressed
    by persons.merged_into. So every read has to be able to ask about a GROUP
    of ids, not just one.
    """
    if isinstance(person, (list, tuple, set, frozenset)):
        ids = tuple(person) or (-1,)
        return "person_id IN (" + ",".join("?" * len(ids)) + ")", ids
    return "person_id=?", (person,)


def balance(con, person) -> int:
    where, args = _who(person)
    r = con.execute(f"SELECT COALESCE(SUM(delta),0) b FROM y_ledger WHERE {where}", args).fetchone()
    return int(r["b"])


# Lifetime is EARN + ADJUST + CLAWBACK and deliberately excludes SPEND, so
# spending never demotes a tier — but a clawback does, because those points
# were never really earned.
_LIFETIME_KINDS = "('EARN','ADJUST','CLAWBACK')"


def lifetime_earned(con, person) -> int:
    where, args = _who(person)
    r = con.execute(f"SELECT COALESCE(SUM(delta),0) b FROM y_ledger "
                    f"WHERE {where} AND kind IN {_LIFETIME_KINDS}", args).fetchone()
    return max(0, int(r["b"]))


def post(con, person_id: int, delta: int, kind: str, reason: str,
         ref: str = "", award_key: str = None, ts: float = None) -> bool:
    """Append one row. Returns False when `award_key` has already been paid."""
    if not delta:
        return False
    try:
        con.execute(
            "INSERT INTO y_ledger(person_id,delta,kind,reason,ref,award_key,ts) VALUES(?,?,?,?,?,?,?)",
            (person_id, int(delta), kind, reason, ref, award_key, ts or time.time()))
    except sqlite3.IntegrityError:
        return False           # UNIQUE(award_key) — already paid for this exact thing
    return True


def paid_already(con, award_key: str) -> bool:
    if not award_key:
        return False
    return bool(con.execute("SELECT 1 FROM y_ledger WHERE award_key=?", (award_key,)).fetchone()
                or con.execute("SELECT 1 FROM held_awards WHERE award_key=? AND status<>'REFUSED'",
                               (award_key,)).fetchone())


def hold(con, person_id: int, delta: int, reason: str, ref: str,
        award_key: str, flag_kind: str, ts: float = None) -> bool:
    """Park an award for review instead of paying it. Same key space as the
    ledger, so a held award cannot later be paid twice by another path."""
    try:
        con.execute("INSERT INTO held_awards(person_id,delta,reason,ref,award_key,flag_kind,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (person_id, int(delta), reason, ref, award_key, flag_kind, ts or time.time()))
    except sqlite3.IntegrityError:
        return False
    return True


def release_hold(con, hold_id: int, actor: str, note: str = "", ts: float = None) -> dict | None:
    ts = ts or time.time()
    h = con.execute("SELECT * FROM held_awards WHERE id=? AND status='HELD'", (hold_id,)).fetchone()
    if not h:
        return None
    con.execute("UPDATE held_awards SET status='RELEASED', decided_at=?, decided_by=?, decision_note=? WHERE id=?",
                (ts, actor, note, hold_id))
    post(con, h["person_id"], h["delta"], "EARN", h["reason"], h["ref"], h["award_key"], ts)
    return dict(h)


def refuse_hold(con, hold_id: int, actor: str, note: str = "", ts: float = None) -> dict | None:
    h = con.execute("SELECT * FROM held_awards WHERE id=? AND status='HELD'", (hold_id,)).fetchone()
    if not h:
        return None
    con.execute("UPDATE held_awards SET status='REFUSED', decided_at=?, decided_by=?, decision_note=? WHERE id=?",
                (ts or time.time(), actor, note, hold_id))
    return dict(h)


class InsufficientY(Exception):
    pass


def spend(con, person_id: int, amount: int, reason: str, ref: str = "",
          award_key: str = None, ts: float = None, group=None) -> int:
    """Debit. The balance is read INSIDE the caller's transaction, which is why
    every caller must already hold one — read-then-write on a balance without
    it is the classic double-spend."""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")
    bal = balance(con, group or person_id)
    if bal < amount:
        raise InsufficientY(f"balance {bal} < {amount}")
    if not post(con, person_id, -amount, "SPEND", reason, ref, award_key, ts):
        raise ValueError("duplicate spend key")
    return bal - amount


def clawback(con, person_id: int, amount: int, reason: str, ref: str = "",
             award_key: str = None, ts: float = None) -> int:
    """Take back an award that turned out to be false (a spot-checked like that
    was never there). Allowed to drive a balance NEGATIVE on purpose: zeroing
    it instead would let someone farm, spend, and keep the difference."""
    if not post(con, person_id, -abs(int(amount)), "CLAWBACK", reason, ref, award_key, ts):
        raise ValueError("duplicate clawback key")
    return balance(con, person_id)


def history(con, person, limit: int = 200):
    where, args = _who(person)
    return con.execute(f"SELECT id,delta,kind,reason,ref,ts FROM y_ledger WHERE {where} "
                       f"ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()


def leaderboard(con, limit: int = 20):
    """Raw per-person-id totals. Merged ids are folded by the caller, which is
    the only place that can resolve a merge chain."""
    return con.execute(
        f"SELECT l.person_id, COALESCE(SUM(CASE WHEN l.kind IN {_LIFETIME_KINDS} THEN l.delta ELSE 0 END),0) earned, "
        "       COALESCE(SUM(l.delta),0) balance, "
        "       (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id=l.person_id AND r.status='QUALIFIED') qualified "
        "FROM y_ledger l JOIN persons p ON p.id=l.person_id "
        "WHERE p.suspended=0 "
        "GROUP BY l.person_id HAVING earned>0 ORDER BY earned DESC, l.person_id LIMIT ?",
        (int(limit),)).fetchall()


def totals(con) -> dict:
    r = con.execute("SELECT COALESCE(SUM(CASE WHEN delta>0 THEN delta ELSE 0 END),0) issued, "
                    "COALESCE(SUM(CASE WHEN delta<0 THEN -delta ELSE 0 END),0) spent, "
                    "COALESCE(SUM(delta),0) outstanding, COUNT(*) rows FROM y_ledger").fetchone()
    return {"issued": int(r["issued"]), "spent": int(r["spent"]),
            "outstanding": int(r["outstanding"]), "rows": int(r["rows"])}
