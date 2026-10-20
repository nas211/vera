"""Social quests: paid tasks on our own posts, and the machinery that checks them.

THE RULE THIS FILE IS BUILT AROUND: a task pays what we can prove. Three modes,
and which one a quest gets is decided by what the platform lets us read, not by
what we would like to pay for.

  AUTO    We can check it for free and in full — an X reply/quote/post
          (authorship + text via oEmbed) or an IG comment on our own media.
          Pays the full reward the moment it verifies.
  ATTEST  There is no free read: likes, reposts, follows. The user tells us,
          we pay a fraction (config.ATTESTED_REWARD_PCT), and a random slice is
          spot-checked later — if a paid verifier is configured — and clawed
          back when it was not true.
  MANUAL  A human looks at it.

And the failure rule: a verifier that could not look returns UNKNOWN, and an
UNKNOWN claim goes to PENDING, never REJECTED. An outage at X must not read as
"our users are cheating".
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Optional

from . import config, ids, ledger, rules
from .engine import Referrals, ReferralError
from .store import audit
from .verifiers import REJECTED, UNKNOWN, VERIFIED, Result, ig_graph, x_api, x_oembed, apify

# What each kind needs, and who can check it.
KINDS = {
    "POST":    {"needs_proof": True,  "platform": "x", "auto": True},
    "REPLY":   {"needs_proof": True,  "platform": "x", "auto": True},
    "QUOTE":   {"needs_proof": True,  "platform": "x", "auto": True},
    "COMMENT": {"needs_proof": False, "platform": "instagram", "auto": True},
    "LIKE":    {"needs_proof": False, "platform": "x", "auto": False},
    "REPOST":  {"needs_proof": False, "platform": "x", "auto": False},
    "FOLLOW":  {"needs_proof": False, "platform": "x", "auto": False},
}


class QuestError(ReferralError):
    pass


def _pretty(platform: str) -> str:
    return {"x": "X", "instagram": "Instagram"}.get(platform, platform)


class Quests:
    def __init__(self, referrals: Referrals):
        self.r = referrals
        self.store = referrals.store
        self._clock = referrals._clock

    # ------------------------------------------------------------ handles
    def link_handle(self, platform: str, external_id, *, social: str, handle: str) -> dict:
        """Start proving a social handle belongs to this person.

        The nonce is the whole mechanism: anyone can type @elonmusk into a form,
        but only the account holder can make a post that X or Instagram then
        attributes to that handle."""
        social = social.lower().strip()
        if social not in config.SOCIAL_PLATFORMS:
            raise QuestError("we only run tasks on "
                             + " and ".join(_pretty(p) for p in config.SOCIAL_PLATFORMS))
        h = (handle or "").strip().lstrip("@")
        if not h or len(h) > 40 or not all(c.isalnum() or c in "._-" for c in h):
            raise QuestError("that is not a valid handle")
        pid = self.r.person_id(platform, external_id)
        now = self._clock()
        n = ids.nonce()
        with self.store as con:
            owner = con.execute("SELECT person_id, verified FROM social_accounts "
                                "WHERE platform=? AND handle_lc=?", (social, h.lower())).fetchone()
            if owner and owner["person_id"] != pid and owner["verified"]:
                raise QuestError("that handle is already linked to another account")
            con.execute(
                "INSERT INTO social_accounts(person_id,platform,handle,handle_lc,nonce,nonce_expires,linked_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(platform,handle_lc) DO UPDATE SET person_id=excluded.person_id, "
                "  handle=excluded.handle, nonce=excluded.nonce, nonce_expires=excluded.nonce_expires",
                (pid, social, h, h.lower(), n, now + config.HANDLE_NONCE_TTL_SEC, now))
        return {"handle": h, "platform": social, "nonce": n,
                "expires_at": now + config.HANDLE_NONCE_TTL_SEC,
                "instructions": (f"Post this code from @{h} and send us the link: {n}" if social == "x"
                                 else f"Comment this code on our latest post from @{h}: {n}")}

    def verify_handle(self, platform: str, external_id, *, social: str,
                      proof_url: str = "", media_id: str = "") -> dict:
        pid = self.r.person_id(platform, external_id)
        now = self._clock()
        row = self.store.one("SELECT * FROM social_accounts WHERE person_id=? AND platform=?",
                             (pid, social))
        if not row:
            raise QuestError("link a handle first")
        if row["verified"]:
            return {"ok": True, "already": True, "handle": row["handle"]}
        if row["nonce_expires"] < now:
            raise QuestError("that code expired — link the handle again")

        if social == "x":
            res = x_oembed.verify_handle_nonce(proof_url, handle=row["handle"], nonce=row["nonce"])
        else:
            media = media_id or self._latest_our_post("instagram")
            if not media:
                raise QuestError("no Instagram post to check against yet")
            found, err = ig_graph.commenters(media)
            if found is None:
                res = Result(UNKNOWN, "ig_graph", {"error": err})
            else:
                # Graph gives us the commenter list; the nonce has to be in
                # THEIR comment, so re-read with text to match it.
                res = self._ig_nonce(media, row["handle"], row["nonce"])

        if res.status == VERIFIED:
            with self.store as con:
                con.execute("UPDATE social_accounts SET verified=1, verified_at=?, verify_method=?, nonce='' "
                            "WHERE id=?", (now, res.verifier, row["id"]))
                audit(con, f"{platform}:{external_id}", "HANDLE_VERIFIED",
                      f"{social}:{row['handle']}", json.dumps(res.detail)[:500])
            return {"ok": True, "handle": row["handle"], "detail": res.detail}
        return {"ok": False, "status": res.status, "detail": res.detail}

    def _ig_nonce(self, media_id: str, handle: str, nonce: str) -> Result:
        from .verifiers.base import get_json, norm_handle
        import urllib.parse
        tok = ig_graph.token()
        url = (f"{ig_graph.GRAPH}/{ig_graph.api_version()}/{urllib.parse.quote(media_id)}/comments"
               f"?fields=username,text&limit=100&access_token={urllib.parse.quote(tok)}")
        data, code, err = get_json(url)
        if not data or "data" not in data:
            return Result(UNKNOWN, "ig_graph", {"error": err or f"http {code}"})
        for c in data["data"]:
            if norm_handle(c.get("username")) == norm_handle(handle) and nonce in (c.get("text") or ""):
                return Result(VERIFIED, "ig_graph", {"comment": (c.get("text") or "")[:200]})
        return Result(REJECTED, "ig_graph", {"reason": "no comment with that code from that handle",
                                             "n_seen": len(data["data"])})

    def handles(self, platform: str, external_id) -> list:
        pid = self.r.person_id(platform, external_id)
        return [dict(r) for r in self.store.q(
            "SELECT platform, handle, verified, verified_at FROM social_accounts WHERE person_id=?", (pid,))]

    def _handle_for(self, con, person_id: int, social: str, *, verified_only=True):
        q = "SELECT * FROM social_accounts WHERE person_id=? AND platform=?"
        if verified_only:
            q += " AND verified=1"
        return con.execute(q, (person_id, social)).fetchone()

    # ------------------------------------------------------------ catalogue
    def add_quest(self, *, code: str, kind: str, title: str, y_reward: int, platform: str = "x",
                  target_ref: str = "", target_url: str = "", description: str = "",
                  marker: str = "", verify_mode: str = None, max_claims: int = 0,
                  repeat_every_sec: float = 0, starts_at=None, ends_at=None,
                  created_by: str = "admin") -> int:
        kind = kind.upper()
        if kind not in KINDS:
            raise QuestError(f"kind must be one of {sorted(KINDS)}")
        spec = KINDS[kind]
        if spec["platform"] not in config.SOCIAL_PLATFORMS:
            raise QuestError(f"{kind} is a {_pretty(spec['platform'])} task and "
                             f"{_pretty(spec['platform'])} is switched off")
        # The default is never "AUTO because we said so": it is AUTO only for
        # the kinds that have a free read path.
        mode = (verify_mode or ("AUTO" if spec["auto"] else "ATTEST")).upper()
        if target_url and not target_ref and platform == "x":
            _, pid_ = x_oembed.parse_url(target_url)
            target_ref = pid_ or ""
        with self.store as con:
            cur = con.execute(
                "INSERT INTO quests(code,platform,kind,target_ref,target_url,title,description,y_reward,"
                "verify_mode,marker,active,starts_at,ends_at,max_claims,repeat_every_sec,created_at,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)",
                (code, platform, kind, target_ref, target_url, title, description, int(y_reward),
                 mode, marker, starts_at, ends_at, int(max_claims), float(repeat_every_sec),
                 self._clock(), created_by))
            audit(con, created_by, "QUEST_ADDED", code, f"{kind} {y_reward}Y {mode}")
            return cur.lastrowid

    def edit_quest(self, code: str, actor: str = "admin", **fields) -> dict:
        """Change a live task in place. Rewards, titles and targets all get
        adjusted after a campaign starts; the alternative is an operator with a
        sqlite prompt open on the ledger."""
        allowed = {"title", "description", "y_reward", "marker", "target_url",
                   "target_ref", "verify_mode", "max_claims", "repeat_every_sec"}
        bad = set(fields) - allowed
        if bad:
            raise QuestError(f"not an editable field: {sorted(bad)}")
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return {"code": code, "changed": []}
        with self.store as con:
            if not con.execute("SELECT 1 FROM quests WHERE code=?", (code,)).fetchone():
                raise QuestError("no such task", 404)
            sets = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE quests SET {sets} WHERE code=?", (*fields.values(), code))
            audit(con, actor, "QUEST_EDITED", code, json.dumps(fields)[:400])
        return {"code": code, "changed": sorted(fields)}

    def set_active(self, code: str, active: bool, actor: str = "admin"):
        with self.store as con:
            con.execute("UPDATE quests SET active=? WHERE code=?", (int(active), code))
            audit(con, actor, "QUEST_ACTIVE" if active else "QUEST_PAUSED", code)

    def _slot(self, quest, now: float) -> str:
        every = float(quest["repeat_every_sec"] or 0)
        return "once" if every <= 0 else str(int(now // every))

    def catalogue(self, platform: str, external_id=None) -> list:
        """Live quests, each annotated with what THIS person has already done."""
        now = self._clock()
        pid = self.r.person_id(platform, external_id) if external_id is not None else None
        out = []
        with self.store as con:
            life = ledger.lifetime_earned(con, pid) if pid else 0
            for q in con.execute("SELECT * FROM quests WHERE active=1 ORDER BY id"):
                if q["starts_at"] and now < q["starts_at"]:
                    continue
                if q["ends_at"] and now > q["ends_at"]:
                    continue
                slot = self._slot(q, now)
                claim = None
                if pid:
                    c = con.execute("SELECT status, y_awarded, submitted_at FROM quest_claims "
                                    "WHERE quest_id=? AND person_id=? AND slot=?",
                                    (q["id"], pid, slot)).fetchone()
                    claim = dict(c) if c else None
                attested = q["verify_mode"] == "ATTEST"
                out.append({
                    "code": q["code"], "kind": q["kind"], "platform": q["platform"],
                    "title": q["title"], "description": q["description"],
                    "url": q["target_url"], "marker": q["marker"],
                    "verify_mode": q["verify_mode"],
                    "needs_proof": KINDS[q["kind"]]["needs_proof"],
                    "y_reward": rules.quest_award(q["y_reward"], attested=attested, lifetime_y=life),
                    "y_base": q["y_reward"],
                    "repeatable": bool(q["repeat_every_sec"]),
                    "my_claim": claim,
                })
        return out

    # ------------------------------------------------------------ claiming
    def claim(self, code: str, platform: str, external_id, *, proof_url: str = "",
              ip: str = None, attest: bool = False) -> dict:
        now = self._clock()
        pid = self.r.person_id(platform, external_id)
        q = self.store.one("SELECT * FROM quests WHERE code=?", (code,))
        if not q or not q["active"]:
            raise QuestError("that task is not running")
        if q["starts_at"] and now < q["starts_at"]:
            raise QuestError("that task has not started")
        if q["ends_at"] and now > q["ends_at"]:
            raise QuestError("that task has finished")
        spec = KINDS[q["kind"]]
        if spec["needs_proof"] and not proof_url:
            raise QuestError("send the link to your post")

        with self.store as con:
            p = con.execute("SELECT suspended FROM persons WHERE id=?", (pid,)).fetchone()
            if p and p["suspended"]:
                raise QuestError("this account cannot claim tasks")
            self._rate_check(con, pid, now)
            if q["max_claims"] and con.execute(
                    "SELECT COUNT(*) c FROM quest_claims WHERE quest_id=? AND status IN ('PAID','VERIFIED')",
                    (q["id"],)).fetchone()["c"] >= q["max_claims"]:
                raise QuestError("that task is fully claimed")

            handle_row = self._handle_for(con, pid, spec["platform"])
            if not handle_row:
                raise QuestError(f"verify your {spec['platform']} handle first")
            handle = handle_row["handle"]

            slot = self._slot(q, now)
            try:
                cur = con.execute(
                    "INSERT INTO quest_claims(quest_id,person_id,slot,handle,proof_url,status,submitted_at,ip) "
                    "VALUES(?,?,?,?,?,'PENDING',?,?)",
                    (q["id"], pid, slot, handle, x_oembed.canonical(proof_url) if proof_url else "",
                     now, ip))
            except sqlite3.IntegrityError as e:
                if "proof_url" in str(e):
                    raise QuestError("that post has already been submitted")
                raise QuestError("you have already claimed this task")
            claim_id = cur.lastrowid

        # Verification runs OUTSIDE the transaction: it is a network call, and
        # holding a write lock across the internet blocks the other process.
        res = self._verify(q, handle, proof_url)
        return self._settle(claim_id, q, res, now)

    def _rate_check(self, con, pid: int, now: float):
        last = con.execute("SELECT MAX(submitted_at) t FROM quest_claims WHERE person_id=?",
                           (pid,)).fetchone()["t"]
        if last and now - last < config.QUEST_CLAIM_COOLDOWN_SEC:
            raise QuestError(f"slow down — {int(config.QUEST_CLAIM_COOLDOWN_SEC - (now - last))}s to go")
        n = con.execute("SELECT COUNT(*) c FROM quest_claims WHERE person_id=? AND submitted_at>?",
                        (pid, now - 86400)).fetchone()["c"]
        if n >= config.QUEST_MAX_CLAIMS_PER_DAY:
            raise QuestError("daily task limit reached")

    def _verify(self, q, handle: str, proof_url: str) -> Result:
        kind, mode = q["kind"], q["verify_mode"]
        if mode == "MANUAL":
            return Result(UNKNOWN, "manual", {"reason": "queued for review"})
        if kind in ("POST", "REPLY", "QUOTE"):
            return x_oembed.verify_post(proof_url, handle=handle, marker=q["marker"],
                                        target_post_id=q["target_ref"],
                                        target_handle=self._our_handle())
        if kind == "COMMENT":
            media = q["target_ref"] or self._latest_our_post("instagram")
            if not media:
                return Result(UNKNOWN, "ig_graph", {"reason": "no target media"})
            return ig_graph.verify_comment(media, handle)
        if kind in ("LIKE", "REPOST"):
            r = (x_api.verify_like if kind == "LIKE" else x_api.verify_repost)(q["target_ref"], handle)
            if r.status == UNKNOWN and apify.available():
                r = apify.verify_engagement(q["target_url"], handle, kind)
            return r
        if kind == "FOLLOW":
            return x_api.verify_follow(handle, q["target_ref"] or self._our_handle())
        return Result(UNKNOWN, "none", {"reason": f"no verifier for {kind}"})

    def _settle(self, claim_id: int, q, res: Result, now: float) -> dict:
        """Turn a verifier result into money, a rejection, or a queue entry."""
        attested = (q["verify_mode"] == "ATTEST")
        with self.store as con:
            c = con.execute("SELECT * FROM quest_claims WHERE id=?", (claim_id,)).fetchone()
            if not c or c["status"] in ("PAID", "REJECTED", "CLAWED"):
                return {"status": c["status"] if c else "GONE", "claim_id": claim_id}
            pid = c["person_id"]

            if res.status == REJECTED and not attested:
                con.execute("UPDATE quest_claims SET status='REJECTED', verifier=?, detail=?, checked_at=? "
                            "WHERE id=?", (res.verifier, json.dumps(res.detail)[:1000], now, claim_id))
                return {"status": "REJECTED", "claim_id": claim_id, "reason": res.detail, "y": 0}

            # ATTESTED claims pay on the user's word (at a discount) even when
            # nothing could check them; a paid verifier saying REJECTED still
            # kills it.
            pay = res.status == VERIFIED or (attested and res.status == UNKNOWN)
            if attested and res.status == REJECTED:
                con.execute("UPDATE quest_claims SET status='REJECTED', verifier=?, detail=?, checked_at=? "
                            "WHERE id=?", (res.verifier, json.dumps(res.detail)[:1000], now, claim_id))
                return {"status": "REJECTED", "claim_id": claim_id, "reason": res.detail, "y": 0}

            if not pay:
                con.execute("UPDATE quest_claims SET status='PENDING', verifier=?, detail=?, checked_at=? "
                            "WHERE id=?", (res.verifier, json.dumps(res.detail)[:1000], now, claim_id))
                return {"status": "PENDING", "claim_id": claim_id,
                        "message": "we could not check this yet — it is queued", "y": 0}

            life = ledger.lifetime_earned(con, pid)
            amount = rules.quest_award(q["y_reward"], attested=attested, lifetime_y=life)
            spot = attested and (secrets.randbelow(100) < int(config.SPOT_CHECK_PCT))
            paid = self.r.award(con, pid, amount, "QUEST", f"quest:{q['code']}",
                                f"QUEST:claim:{claim_id}", False, None, now)
            con.execute("UPDATE quest_claims SET status='PAID', verifier=?, detail=?, checked_at=?, "
                        "paid_at=?, y_awarded=?, attested=?, spot_check=? WHERE id=?",
                        (res.verifier, json.dumps(res.detail)[:1000], now, now, paid,
                         int(attested), int(spot), claim_id))
            audit(con, f"person:{pid}", "QUEST_PAID", q["code"], f"{paid} Y claim:{claim_id}")
        return {"status": "PAID", "claim_id": claim_id, "y": paid, "attested": attested,
                "detail": res.detail}

    # ------------------------------------------------------------ daemon
    def sweep(self, limit: int = 50) -> dict:
        """Re-check what we could not check at claim time, and spot-check the
        attested. Runs from a timer; safe to run twice."""
        now = self._clock()
        out = {"rechecked": 0, "paid": 0, "rejected": 0, "still_pending": 0, "spot_checked": 0,
               "clawed": 0}
        rows = self.store.q(
            "SELECT c.*, q.code qcode FROM quest_claims c JOIN quests q ON q.id=c.quest_id "
            "WHERE c.status='PENDING' ORDER BY c.submitted_at LIMIT ?", (limit,))
        for c in rows:
            q = self.store.one("SELECT * FROM quests WHERE id=?", (c["quest_id"],))
            if q["verify_mode"] == "MANUAL":
                out["still_pending"] += 1
                continue
            res = self._verify(q, c["handle"], c["proof_url"])
            out["rechecked"] += 1
            r = self._settle(c["id"], q, res, now)
            out["paid" if r["status"] == "PAID" else
                "rejected" if r["status"] == "REJECTED" else "still_pending"] += 1

        # Spot checks only mean something once a paid verifier exists.
        if x_api.available() or apify.available():
            for c in self.store.q(
                    "SELECT c.* FROM quest_claims c WHERE c.status='PAID' AND c.spot_check=1 LIMIT ?",
                    (limit,)):
                q = self.store.one("SELECT * FROM quests WHERE id=?", (c["quest_id"],))
                res = self._verify(q, c["handle"], c["proof_url"])
                out["spot_checked"] += 1
                with self.store as con:
                    con.execute("UPDATE quest_claims SET spot_check=0, detail=? WHERE id=?",
                                (json.dumps(res.detail)[:1000], c["id"]))
                    if res.status == REJECTED:
                        ledger.clawback(con, c["person_id"], c["y_awarded"], "QUEST_SPOT_CHECK",
                                        f"claim:{c['id']}", f"CLAW:claim:{c['id']}", now)
                        con.execute("UPDATE quest_claims SET status='CLAWED' WHERE id=?", (c["id"],))
                        audit(con, "system", "QUEST_CLAWBACK", q["code"],
                              f"claim:{c['id']} -{c['y_awarded']} Y")
                        out["clawed"] += 1
        return out

    def admin_decide(self, claim_id: int, approve: bool, actor: str, note: str = "") -> dict:
        now = self._clock()
        c = self.store.one("SELECT * FROM quest_claims WHERE id=?", (claim_id,))
        if not c:
            raise QuestError("no such claim", 404)
        q = self.store.one("SELECT * FROM quests WHERE id=?", (c["quest_id"],))
        if not approve:
            with self.store as con:
                con.execute("UPDATE quest_claims SET status='REJECTED', detail=?, checked_at=? WHERE id=?",
                            (json.dumps({"admin": actor, "note": note}), now, claim_id))
                audit(con, actor, "QUEST_REJECTED", q["code"], note)
            return {"status": "REJECTED", "claim_id": claim_id}
        return self._settle(claim_id, q, Result(VERIFIED, f"admin:{actor}", {"note": note}), now)

    def pending(self, limit: int = 200) -> list:
        return [dict(r) for r in self.store.q(
            "SELECT c.*, q.code qcode, q.title, q.kind FROM quest_claims c JOIN quests q ON q.id=c.quest_id "
            "WHERE c.status='PENDING' ORDER BY c.submitted_at LIMIT ?", (limit,))]

    # ------------------------------------------------------------ our posts
    def add_our_post(self, platform: str, post_id: str, url: str = "", caption: str = "",
                     posted_at: float = None, auto_quest: bool = False) -> int:
        with self.store as con:
            cur = con.execute(
                "INSERT INTO our_posts(platform,post_id,url,caption,posted_at,added_at,auto_quest) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(platform,post_id) DO UPDATE SET "
                "url=excluded.url, caption=excluded.caption, auto_quest=excluded.auto_quest",
                (platform, str(post_id), url, caption, posted_at, self._clock(), int(auto_quest)))
            return cur.lastrowid

    def _latest_our_post(self, platform: str) -> str:
        r = self.store.one("SELECT post_id FROM our_posts WHERE platform=? ORDER BY "
                           "COALESCE(posted_at, added_at) DESC LIMIT 1", (platform,))
        return r["post_id"] if r else ""

    def _our_handle(self) -> str:
        import os
        return (os.environ.get("WHYR_OUR_X_HANDLE") or os.environ.get("X_HANDLE") or "").lstrip("@")

    def my_claims(self, platform: str, external_id, limit: int = 50) -> list:
        pid = self.r.person_id(platform, external_id)
        return [dict(r) for r in self.store.q(
            "SELECT c.id, c.status, c.y_awarded, c.submitted_at, c.proof_url, q.code, q.title "
            "FROM quest_claims c JOIN quests q ON q.id=c.quest_id "
            "WHERE c.person_id=? ORDER BY c.id DESC LIMIT ?", (pid, limit))]
