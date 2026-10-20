"""The facade both products call. Nothing outside this file writes the ledger.

A host adapter only ever knows two things about a customer: which platform it
is ('predict' | 'telegram') and that platform's own id. Everything else —
persons, codes, awards, tiers, holds — is resolved here.

    r = Referrals()
    r.code_for("telegram", 7712)                      -> 'WHY-4KTP9M'
    r.bind("WHY-4KTP9M", "predict", 99, ip=...)       -> {'ok': True, ...}
    r.note_qualified("predict", 99, evidence="3 predictions")
    r.note_payment("telegram", 7712, usd=99.0, tier="premium", payment_id=57)
    r.summary("predict", 99)                          -> the whole dashboard
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from . import abuse, config, ids, ledger, rules
from .store import Store, audit

PLATFORMS = ("predict", "telegram", "web")


class ReferralError(Exception):
    def __init__(self, msg, status: int = 400):
        super().__init__(msg)
        self.status = status


class Referrals:
    def __init__(self, store: Optional[Store] = None, *, path=None, clock=time.time):
        self.store = store or Store(path)
        self._clock = clock

    # ------------------------------------------------------------ identity
    def _subject(self, con, platform: str, external_id) -> Optional[sqlite3.Row]:
        return con.execute("SELECT * FROM person_subjects WHERE platform=? AND external_id=?",
                           (platform, str(external_id))).fetchone()

    def person_id(self, platform: str, external_id, *, display: str = "", create: bool = True) -> Optional[int]:
        """The person behind one host's account, creating them on first sight.

        Following `merged_into` matters: after a link, the losing person id is
        still referenced by rows written before the merge, and by any adapter
        that cached it."""
        if platform not in PLATFORMS:
            raise ReferralError(f"unknown platform {platform!r}")
        with self.store as con:
            row = self._subject(con, platform, external_id)
            if row:
                pid = row["person_id"]
                if display and display != row["display"]:
                    con.execute("UPDATE person_subjects SET display=? WHERE platform=? AND external_id=?",
                                (display, platform, str(external_id)))
                return self._alive(con, pid)
            if not create:
                return None
            now = self._clock()
            cur = con.execute("INSERT INTO persons(created_at) VALUES(?)", (now,))
            pid = cur.lastrowid
            con.execute("INSERT INTO person_subjects(platform,external_id,person_id,display,linked_at) "
                        "VALUES(?,?,?,?,?)", (platform, str(external_id), pid, display, now))
            audit(con, f"{platform}:{external_id}", "PERSON_CREATED", f"person:{pid}", display)
            return pid

    def _alive(self, con, person_id: int) -> int:
        seen = set()
        while person_id not in seen:
            seen.add(person_id)
            r = con.execute("SELECT merged_into FROM persons WHERE id=?", (person_id,)).fetchone()
            if not r or r["merged_into"] is None:
                return person_id
            person_id = r["merged_into"]
        return person_id

    def _group(self, con, person_id: int) -> tuple:
        """Every id whose ledger rows belong to this person: themselves plus
        anyone merged into them, at any depth."""
        out, frontier = {person_id}, [person_id]
        while frontier:
            cur = frontier.pop()
            for r in con.execute("SELECT id FROM persons WHERE merged_into=?", (cur,)):
                if r["id"] not in out:
                    out.add(r["id"])
                    frontier.append(r["id"])
        return tuple(sorted(out))

    def subjects(self, con, person_id: int):
        return con.execute("SELECT platform, external_id, display FROM person_subjects WHERE person_id=?",
                           (person_id,)).fetchall()

    # ------------------------------------------------------------ codes
    def code_for(self, platform: str, external_id, *, display: str = "") -> str:
        pid = self.person_id(platform, external_id, display=display)
        with self.store as con:
            row = con.execute("SELECT code FROM referral_codes WHERE person_id=? AND active=1 "
                              "ORDER BY created_at LIMIT 1", (pid,)).fetchone()
            if row:
                return row["code"]
            for _ in range(12):
                code = ids.random_code()
                try:
                    con.execute("INSERT INTO referral_codes(code,person_id,created_at) VALUES(?,?,?)",
                                (code, pid, self._clock()))
                except sqlite3.IntegrityError:
                    continue                    # collision; the loop is the retry
                audit(con, f"{platform}:{external_id}", "CODE_ISSUED", code)
                return code
        raise ReferralError("could not allocate a referral code", 500)

    def resolve_code(self, code: str) -> Optional[int]:
        c = ids.normalise(code)
        row = self.store.one("SELECT person_id FROM referral_codes WHERE code=? AND active=1", (c,))
        if not row:
            return None
        with self.store as con:
            return self._alive(con, row["person_id"])

    # ------------------------------------------------------------ binding
    def bind(self, code: str, platform: str, external_id, *, ip: str = None,
             display: str = "", account_age_sec: float = 0.0) -> dict:
        """Attach a new account to a referral code. First touch wins and is final.

        Returns {'ok': bool, ...}. A refusal is a NORMAL outcome — most of them
        are people trying their own code — so it is a return value, not an
        exception: a signup must never fail because the referral did.
        """
        now = self._clock()
        referrer = self.resolve_code(code)
        if not referrer:
            return {"ok": False, "reason": "unknown code", "code": ids.normalise(code)}
        referee = self.person_id(platform, external_id, display=display)
        with self.store as con:
            s = abuse.screen_bind(con, referrer_id=referrer, referee_id=referee, ip=ip,
                                  account_age_sec=account_age_sec, now=now)
            if not s.ok:
                return {"ok": False, "reason": s.refuse}
            cur = con.execute(
                "INSERT INTO referrals(referrer_id,referee_id,code,platform,ip,bound_at,status) "
                "VALUES(?,?,?,?,?,?,'PENDING')",
                (referrer, referee, ids.normalise(code), platform, ip, now))
            rid = cur.lastrowid
            abuse.record(con, s, person_id=referee, ip=ip, now=now)
            audit(con, f"{platform}:{external_id}", "REFERRAL_BOUND", f"referral:{rid}",
                  f"code={ids.normalise(code)} held={s.hold}")

            held = s.hold
            lifetime = ledger.lifetime_earned(con, self._group(con, referrer))
            self._award(con, referrer, rules.signup_award(lifetime), "REFERRAL_SIGNUP",
                        f"referral:{rid}", f"SIGNUP:ref:{rid}", held, s, now)
            # The referee's welcome is paid immediately even when the referrer's
            # side is held: the new customer did nothing wrong and is watching.
            self._award(con, referee, rules.welcome_award(), "REFERRAL_WELCOME",
                        f"referral:{rid}", f"WELCOME:ref:{rid}", False, s, now)
        return {"ok": True, "referral_id": rid, "referrer_person": referrer,
                "held": held, "flags": [k for k, _ in s.flags],
                "welcome_y": rules.welcome_award()}

    def award(self, con, person_id: int, amount: int, reason: str, ref: str,
              award_key: str, held: bool = False, screen=None, now=None) -> int:
        """Public entry to the same pay-or-park path the referral events use.
        `quests.py` calls this so there is exactly one place that credits $Y."""
        return self._award(con, person_id, amount, reason, ref, award_key, held, screen, now)

    def _award(self, con, person_id: int, amount: int, reason: str, ref: str,
               award_key: str, held: bool, screen=None, now=None) -> int:
        """Pay, or park. Returns what was actually credited (0 when held)."""
        if amount <= 0:
            return 0
        if held:
            kind = ",".join(k for k, _ in (screen.flags if screen else [])) or "FLAGGED"
            ledger.hold(con, person_id, amount, reason, ref, award_key, kind, now)
            return 0
        return amount if ledger.post(con, person_id, amount, "EARN", reason, ref, award_key, now) else 0

    # ------------------------------------------------------------ lifecycle events
    def note_qualified(self, platform: str, external_id, *, evidence: str = "") -> dict:
        """The referee actually used the product. Pays the referrer the bigger
        half, plus a milestone if this is their Nth. Idempotent: a second call
        for the same referral pays nothing."""
        now = self._clock()
        referee = self.person_id(platform, external_id, create=False)
        if not referee:
            return {"ok": False, "reason": "no such account"}
        with self.store as con:
            r = con.execute("SELECT * FROM referrals WHERE referee_id=?", (referee,)).fetchone()
            if not r:
                return {"ok": False, "reason": "not referred"}
            if r["status"] != "PENDING":
                return {"ok": True, "already": r["status"], "referral_id": r["id"]}
            con.execute("UPDATE referrals SET status='QUALIFIED', qualified_at=?, evidence=? WHERE id=?",
                        (now, evidence, r["id"]))
            held = bool(con.execute("SELECT 1 FROM held_awards WHERE award_key=? AND status='HELD'",
                                    (f"SIGNUP:ref:{r['id']}",)).fetchone())
            referrer = r["referrer_id"]
            lifetime = ledger.lifetime_earned(con, self._group(con, referrer))
            paid = self._award(con, referrer, rules.qualified_award(lifetime), "REFERRAL_QUALIFIED",
                               f"referral:{r['id']}", f"QUALIFIED:ref:{r['id']}", held, None, now)
            n = con.execute("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='QUALIFIED'",
                            (referrer,)).fetchone()["c"]
            bonus = self._award(con, referrer, rules.milestone_award(n, lifetime), "REFERRAL_MILESTONE",
                                f"milestone:{n}", f"MILESTONE:{referrer}:{n}", held, None, now)
            audit(con, "system", "REFERRAL_QUALIFIED", f"referral:{r['id']}", evidence)
        return {"ok": True, "referral_id": r["id"], "referrer_person": referrer,
                "y_paid": paid, "milestone_y": bonus, "qualified_count": n, "held": held}

    def note_activity(self, platform: str, external_id, *, n_actions: int, what: str = "predictions") -> dict:
        """Convenience for the hosts: qualify once the referee has done enough."""
        if n_actions < int(config.QUALIFY_PREDICTIONS):
            return {"ok": False, "reason": "below threshold", "have": n_actions,
                    "need": int(config.QUALIFY_PREDICTIONS)}
        return self.note_qualified(platform, external_id, evidence=f"{n_actions} {what}")

    def note_payment(self, platform: str, external_id, *, usd: float, tier: str = "",
                     payment_id=None) -> dict:
        """A referee's payment CLEARED. Pays the referrer a commission in $Y,
        every time, renewals included.

        Called only from the approval path — never when an invoice is raised.
        The one thing this must not do is pay for money that never arrived."""
        now = self._clock()
        referee = self.person_id(platform, external_id, create=False)
        if not referee:
            return {"ok": False, "reason": "no such account"}
        key = f"SUB:payment:{payment_id}" if payment_id is not None else f"SUB:{referee}:{int(now)}"
        with self.store as con:
            r = con.execute("SELECT * FROM referrals WHERE referee_id=?", (referee,)).fetchone()
            if not r:
                return {"ok": False, "reason": "not referred"}
            # Paying money is the strongest possible proof the referee is real,
            # so it qualifies the referral if nothing else had yet.
            if r["status"] == "PENDING":
                con.execute("UPDATE referrals SET status='QUALIFIED', qualified_at=?, evidence=? WHERE id=?",
                            (now, f"paid ${usd:.2f}", r["id"]))
            referrer = r["referrer_id"]
            lifetime = ledger.lifetime_earned(con, self._group(con, referrer))
            amount = rules.commission_award(usd, lifetime)
            held = bool(con.execute("SELECT 1 FROM held_awards WHERE award_key=? AND status='HELD'",
                                    (f"SIGNUP:ref:{r['id']}",)).fetchone())
            paid = self._award(con, referrer, amount, "REFERRAL_COMMISSION",
                               f"payment:{payment_id} {tier} ${usd:.2f}", key, held, None, now)
            audit(con, "system", "REFERRAL_COMMISSION", f"referral:{r['id']}",
                  f"${usd:.2f} {tier} -> {paid} Y (held={held})")
        return {"ok": True, "referral_id": r["id"], "referrer_person": referrer,
                "y_paid": paid, "y_amount": amount, "held": held}

    # ------------------------------------------------------------ spending
    def convert_to_credits(self, platform: str, external_id, y_amount: int) -> dict:
        """$Y -> WHY Predict play-credits. Returns how many credits the CALLER
        must post to its own ledger; this package cannot reach Predict's DB.

        Debit here, credit there, in that order: a crash between the two costs
        the customer points and no credits, which is recoverable from the audit
        row. The other order mints credits nobody paid for."""
        y_amount = int(y_amount)
        if y_amount < int(config.MIN_CONVERT_Y):
            raise ReferralError(f"minimum is {config.MIN_CONVERT_Y} $Y")
        pid = self.person_id(platform, external_id)
        now = self._clock()
        key = f"CONVERT:{pid}:{int(now * 1000)}"
        with self.store as con:
            try:
                after = ledger.spend(con, pid, y_amount, "CONVERT_CREDITS", f"{platform}:{external_id}",
                                     key, now, group=self._group(con, pid))
            except ledger.InsufficientY as e:
                raise ReferralError(str(e))
            credits = rules.credits_for(y_amount)
            audit(con, f"{platform}:{external_id}", "CONVERT_CREDITS", f"person:{pid}",
                  f"{y_amount} Y -> {credits} credits")
        return {"ok": True, "y_spent": y_amount, "credits": credits, "balance": after, "ref": key}

    def spend_for_sub_days(self, platform: str, external_id, days: int) -> dict:
        """$Y -> subscription days. THIS ONLY DEBITS THE POINTS. The bot's own
        billing code grants the time and is the only thing that may — a package
        that can hand out paid product from a points table is a package that can
        be made to hand out paid product."""
        days = int(days)
        if days < int(config.MIN_SUB_DAYS) or days > int(config.MAX_SUB_DAYS):
            raise ReferralError(f"days must be between {config.MIN_SUB_DAYS} and {config.MAX_SUB_DAYS}")
        cost = rules.sub_days_cost(days)
        pid = self.person_id(platform, external_id)
        now = self._clock()
        key = f"SUBDAYS:{pid}:{int(now * 1000)}"
        with self.store as con:
            try:
                after = ledger.spend(con, pid, cost, "SUB_DAYS", f"{days}d", key, now,
                                     group=self._group(con, pid))
            except ledger.InsufficientY as e:
                raise ReferralError(str(e))
            audit(con, f"{platform}:{external_id}", "SPEND_SUB_DAYS", f"person:{pid}",
                  f"{cost} Y -> {days} days")
        return {"ok": True, "y_spent": cost, "days": days, "balance": after, "ref": key}

    def refund(self, platform: str, external_id, y_amount: int, reason: str, ref: str = "") -> int:
        """Put points back when the thing they bought could not be delivered."""
        pid = self.person_id(platform, external_id)
        now = self._clock()
        with self.store as con:
            ledger.post(con, pid, int(y_amount), "ADJUST", f"REFUND:{reason}", ref,
                        f"REFUND:{pid}:{int(now * 1000)}", now)
            audit(con, "system", "REFUND", f"person:{pid}", f"{y_amount} Y {reason}")
            return ledger.balance(con, pid)

    # ------------------------------------------------------------ linking
    def issue_link_token(self, platform: str, external_id) -> dict:
        pid = self.person_id(platform, external_id)
        now = self._clock()
        tok = ids.token(9).upper().replace("_", "").replace("-", "")[:10]
        with self.store as con:
            con.execute("DELETE FROM link_tokens WHERE person_id=? AND used_at IS NULL", (pid,))
            con.execute("INSERT INTO link_tokens(token,person_id,created_at,expires_at) VALUES(?,?,?,?)",
                        (tok, pid, now, now + config.LINK_TOKEN_TTL_SEC))
        return {"token": tok, "expires_in": config.LINK_TOKEN_TTL_SEC,
                "expires_at": now + config.LINK_TOKEN_TTL_SEC}

    def claim_link_token(self, token: str, platform: str, external_id, *, display: str = "") -> dict:
        """Merge the caller's account into the person that issued the token."""
        now = self._clock()
        tok = (token or "").strip().upper()
        with self.store as con:
            row = con.execute("SELECT * FROM link_tokens WHERE token=?", (tok,)).fetchone()
            if not row or row["used_at"] or row["expires_at"] < now:
                return {"ok": False, "reason": "that link code is not valid any more"}
            target = self._alive(con, row["person_id"])
        mine = self.person_id(platform, external_id, display=display)
        if mine == target:
            return {"ok": True, "already": True, "person": target}
        with self.store as con:
            res = self._merge(con, keep=target, drop=mine, actor=f"{platform}:{external_id}")
            if not res["ok"]:
                return res
            con.execute("UPDATE link_tokens SET used_at=?, used_by=? WHERE token=?",
                        (now, f"{platform}:{external_id}", tok))
        return {"ok": True, "person": target, **res}

    def _merge(self, con, *, keep: int, drop: int, actor: str) -> dict:
        """Fold `drop` into `keep`. Balances add; codes on both sides keep
        working, because someone is holding a screenshot of each."""
        if keep == drop:
            return {"ok": True, "already": True}
        # A merge that makes someone their own referrer would pay them for
        # themselves forever. Refuse it and say why rather than silently
        # deleting one side's history.
        rel = con.execute("SELECT 1 FROM referrals WHERE (referrer_id=? AND referee_id=?) "
                          "OR (referrer_id=? AND referee_id=?)", (keep, drop, drop, keep)).fetchone()
        if rel:
            return {"ok": False, "reason": "those two accounts referred each other"}
        moved = ledger.balance(con, drop)
        # y_ledger is NOT in this list and must not be: it is append-only, and
        # `_group` is what makes the moved balance visible under `keep`.
        for tbl, col in (("person_subjects", "person_id"), ("referral_codes", "person_id"),
                         ("held_awards", "person_id"),
                         ("abuse_flags", "person_id"), ("quest_claims", "person_id"),
                         ("social_accounts", "person_id"), ("referrals", "referrer_id")):
            con.execute(f"UPDATE OR IGNORE {tbl} SET {col}=? WHERE {col}=?", (keep, drop))
        # referee_id is UNIQUE: if BOTH sides were referred, the surviving row
        # is the one already on `keep` and the other is left where it is,
        # pointing at a merged-away person, which `_alive` resolves on read.
        con.execute("UPDATE OR IGNORE referrals SET referee_id=? WHERE referee_id=?", (keep, drop))
        con.execute("UPDATE persons SET merged_into=? WHERE id=?", (keep, drop))
        audit(con, actor, "PERSON_MERGED", f"person:{drop}->{keep}", f"balance_moved={moved}")
        return {"ok": True, "merged_from": drop, "y_moved": moved}

    # ------------------------------------------------------------ reads
    def summary(self, platform: str, external_id, *, display: str = "") -> dict:
        """Everything the invite screen shows, in one query pass."""
        pid = self.person_id(platform, external_id, display=display)
        code = self.code_for(platform, external_id, display=display)
        with self.store as con:
            grp = self._group(con, pid)
            bal = ledger.balance(con, grp)
            life = ledger.lifetime_earned(con, grp)
            tier = rules.tier_for(life)
            nxt = rules.next_tier(life)
            rows = con.execute(
                "SELECT status, COUNT(*) c FROM referrals WHERE referrer_id=? GROUP BY status",
                (pid,)).fetchall()
            counts = {r["status"]: r["c"] for r in rows}
            held = con.execute("SELECT COALESCE(SUM(delta),0) d, COUNT(*) c FROM held_awards "
                               "WHERE person_id=? AND status='HELD'", (pid,)).fetchone()
            mine = con.execute("SELECT referrer_id, code, status FROM referrals WHERE referee_id=?",
                               (pid,)).fetchone()
            recent = [dict(r) for r in ledger.history(con, grp, 25)]
            subs = [dict(r) for r in self.subjects(con, pid)]
            invited = con.execute(
                "SELECT r.id, r.status, r.bound_at, r.platform, "
                "  (SELECT display FROM person_subjects s WHERE s.person_id=r.referee_id "
                "   ORDER BY s.linked_at LIMIT 1) name "
                "FROM referrals r WHERE r.referrer_id=? ORDER BY r.bound_at DESC LIMIT 50",
                (pid,)).fetchall()
        return {
            "person_id": pid, "code": code, "links": config.invite_links(code),
            "y_balance": bal, "y_lifetime": life,
            "tier": {"name": tier[0], "multiplier": tier[2],
                     "next": ({"name": nxt[0], "at": nxt[1], "need": nxt[1] - life} if nxt else None)},
            "referrals": {"pending": counts.get("PENDING", 0),
                          "qualified": counts.get("QUALIFIED", 0),
                          "total": sum(counts.values())},
            "invited": [dict(r) for r in invited],
            "held": {"count": held["c"], "y": int(held["d"])},
            "referred_by": (dict(mine) if mine else None),
            "accounts": subs,
            "ledger": recent,
            "rates": rules.rate_card(),
        }

    def balance(self, platform: str, external_id) -> int:
        pid = self.person_id(platform, external_id, create=False)
        if not pid:
            return 0
        with self.store as con:
            return ledger.balance(con, self._group(con, pid))

    def leaderboard(self, limit: int = 20) -> list:
        with self.store as con:
            merged = {}
            for r in ledger.leaderboard(con, limit * 4):
                alive = self._alive(con, r["person_id"])
                m = merged.setdefault(alive, {"person_id": alive, "y_earned": 0, "qualified": 0})
                m["y_earned"] += int(r["earned"])
                m["qualified"] += int(r["qualified"])
            out = []
            for m in sorted(merged.values(), key=lambda x: (-x["y_earned"], x["person_id"]))[:limit]:
                subs = self.subjects(con, m["person_id"])
                name = next((s["display"] for s in subs if s["display"]), f"WHY-{m['person_id']:04d}")
                out.append({**m, "name": name, "tier": rules.tier_for(m["y_earned"])[0]})
            return out

    # ------------------------------------------------------------ admin
    def clean_referrals(self, person_id: int) -> int:
        """Referrals that passed the anti-abuse screen — the count the waitlist
        calls "verified friends".

        Deliberately NOT the same question as `status='QUALIFIED'`: qualified
        means the referee used the product, which somebody sitting on a
        waitlist has not had the chance to do. Clean means we checked them and
        found nothing wrong, which is what the priority queue is promising.
        """
        r = self.store.one(
            "SELECT COUNT(*) c FROM referrals r WHERE r.referrer_id=? AND r.status<>'REJECTED' "
            "AND NOT EXISTS (SELECT 1 FROM held_awards h "
            "                WHERE h.award_key='SIGNUP:ref:'||r.id AND h.status='HELD')",
            (person_id,))
        return int(r["c"])

    def holds(self, limit: int = 200) -> list:
        return [dict(r) for r in self.store.q(
            "SELECT * FROM held_awards WHERE status='HELD' ORDER BY id DESC LIMIT ?", (limit,))]

    def release(self, hold_id: int, actor: str, note: str = "") -> Optional[dict]:
        with self.store as con:
            out = ledger.release_hold(con, hold_id, actor, note, self._clock())
            if out:
                audit(con, actor, "HOLD_RELEASED", f"hold:{hold_id}", note)
            return out

    def refuse(self, hold_id: int, actor: str, note: str = "") -> Optional[dict]:
        with self.store as con:
            out = ledger.refuse_hold(con, hold_id, actor, note, self._clock())
            if out:
                audit(con, actor, "HOLD_REFUSED", f"hold:{hold_id}", note)
            return out

    def grant(self, platform: str, external_id, amount: int, reason: str, actor: str = "admin") -> int:
        pid = self.person_id(platform, external_id)
        now = self._clock()
        with self.store as con:
            ledger.post(con, pid, int(amount), "ADJUST", reason, actor,
                        f"ADJUST:{pid}:{int(now * 1000)}", now)
            audit(con, actor, "GRANT", f"person:{pid}", f"{amount} Y {reason}")
            return ledger.balance(con, pid)

    def suspend(self, platform: str, external_id, suspended: bool, actor: str, note: str = "") -> dict:
        pid = self.person_id(platform, external_id)
        with self.store as con:
            con.execute("UPDATE persons SET suspended=?, note=? WHERE id=?", (int(suspended), note, pid))
            audit(con, actor, "SUSPEND" if suspended else "UNSUSPEND", f"person:{pid}", note)
        return {"person_id": pid, "suspended": suspended}

    def stats(self) -> dict:
        with self.store as con:
            t = ledger.totals(con)
            n_people = con.execute("SELECT COUNT(*) c FROM persons WHERE merged_into IS NULL").fetchone()["c"]
            refs = con.execute("SELECT status, COUNT(*) c FROM referrals GROUP BY status").fetchall()
            held = con.execute("SELECT COUNT(*) c, COALESCE(SUM(delta),0) d FROM held_awards "
                               "WHERE status='HELD'").fetchone()
            flags = con.execute("SELECT kind, COUNT(*) c FROM abuse_flags GROUP BY kind").fetchall()
        return {"people": n_people, "y": t,
                "referrals": {r["status"]: r["c"] for r in refs},
                "held": {"count": held["c"], "y": int(held["d"])},
                "flags": {r["kind"]: r["c"] for r in flags}}
