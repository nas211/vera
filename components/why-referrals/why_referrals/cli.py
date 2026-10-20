"""whyrefctl — the operator's door into the referral engine.

    whyrefctl probe                     what can we verify right now
    whyrefctl stats                     people, $Y issued/spent, flags
    whyrefctl leaderboard
    whyrefctl who telegram 7712         one person's whole picture
    whyrefctl grant predict 42 500 "goodwill"
    whyrefctl holds | release <id> | refuse <id>
    whyrefctl claims                    quest claims waiting for a human
    whyrefctl approve <claim_id> | reject <claim_id>
    whyrefctl quest add --code REPLY_1 --kind REPLY --title "..." --y 100 \
                        --url https://x.com/whyprotocolx/status/123 --marker '#WHYTRADE'
    whyrefctl quest edit REPLY_1 --y 150 --title "..."
    whyrefctl quest list | quest pause CODE | quest resume CODE
    whyrefctl post add x 1234567890 --url ... --auto-quest
    whyrefctl sweep                     re-check pending claims, run spot checks
"""
from __future__ import annotations

import argparse
import json
import sys

from .engine import Referrals, ReferralError
from .quests import Quests
from . import rules, verifiers


def _out(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="whyrefctl", description="WHY referral + quest engine")
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe")
    sub.add_parser("stats")
    sub.add_parser("rates")
    lb = sub.add_parser("leaderboard"); lb.add_argument("-n", type=int, default=20)
    who = sub.add_parser("who"); who.add_argument("platform"); who.add_argument("external_id")
    g = sub.add_parser("grant")
    g.add_argument("platform"); g.add_argument("external_id"); g.add_argument("amount", type=int)
    g.add_argument("reason", nargs="?", default="manual grant")
    sus = sub.add_parser("suspend")
    sus.add_argument("platform"); sus.add_argument("external_id")
    sus.add_argument("--off", action="store_true"); sus.add_argument("--note", default="")

    sub.add_parser("holds")
    for name in ("release", "refuse"):
        p = sub.add_parser(name); p.add_argument("hold_id", type=int); p.add_argument("--note", default="")

    sub.add_parser("claims")
    for name in ("approve", "reject"):
        p = sub.add_parser(name); p.add_argument("claim_id", type=int); p.add_argument("--note", default="")

    sw = sub.add_parser("sweep"); sw.add_argument("-n", type=int, default=50)

    q = sub.add_parser("quest").add_subparsers(dest="qcmd", required=True)
    qa = q.add_parser("add")
    qa.add_argument("--code", required=True); qa.add_argument("--kind", required=True)
    qa.add_argument("--title", required=True); qa.add_argument("--y", type=int, required=True)
    qa.add_argument("--platform", default="x"); qa.add_argument("--url", default="")
    qa.add_argument("--ref", default=""); qa.add_argument("--marker", default="")
    qa.add_argument("--desc", default=""); qa.add_argument("--mode", default=None)
    qa.add_argument("--max-claims", type=int, default=0)
    qa.add_argument("--repeat-hours", type=float, default=0)
    qe = q.add_parser("edit")
    qe.add_argument("code")
    qe.add_argument("--title"); qe.add_argument("--desc", dest="description")
    qe.add_argument("--y", dest="y_reward", type=int); qe.add_argument("--marker")
    qe.add_argument("--url", dest="target_url"); qe.add_argument("--ref", dest="target_ref")
    qe.add_argument("--mode", dest="verify_mode")
    q.add_parser("list")
    for name in ("pause", "resume"):
        p = q.add_parser(name); p.add_argument("code")

    po = sub.add_parser("post").add_subparsers(dest="pcmd", required=True)
    pa = po.add_parser("add")
    pa.add_argument("platform"); pa.add_argument("post_id")
    pa.add_argument("--url", default=""); pa.add_argument("--caption", default="")
    pa.add_argument("--auto-quest", action="store_true")
    po.add_parser("list")

    a = ap.parse_args(argv)
    r = Referrals(path=a.db)
    qs = Quests(r)

    try:
        if a.cmd == "probe":
            _out({"verifiers": verifiers.probe(), "db": str(r.store.path)})
        elif a.cmd == "stats":
            _out(r.stats())
        elif a.cmd == "rates":
            _out(rules.rate_card())
        elif a.cmd == "leaderboard":
            _out(r.leaderboard(a.n))
        elif a.cmd == "who":
            s = r.summary(a.platform, a.external_id)
            s["handles"] = qs.handles(a.platform, a.external_id)
            s["claims"] = qs.my_claims(a.platform, a.external_id)
            _out(s)
        elif a.cmd == "grant":
            _out({"balance": r.grant(a.platform, a.external_id, a.amount, a.reason, "cli")})
        elif a.cmd == "suspend":
            _out(r.suspend(a.platform, a.external_id, not a.off, "cli", a.note))
        elif a.cmd == "holds":
            _out(r.holds())
        elif a.cmd == "release":
            _out(r.release(a.hold_id, "cli", a.note) or {"error": "not held"})
        elif a.cmd == "refuse":
            _out(r.refuse(a.hold_id, "cli", a.note) or {"error": "not held"})
        elif a.cmd == "claims":
            _out(qs.pending())
        elif a.cmd in ("approve", "reject"):
            _out(qs.admin_decide(a.claim_id, a.cmd == "approve", "cli", a.note))
        elif a.cmd == "sweep":
            _out(qs.sweep(a.n))
        elif a.cmd == "quest":
            if a.qcmd == "add":
                qid = qs.add_quest(code=a.code, kind=a.kind, title=a.title, y_reward=a.y,
                                   platform=a.platform, target_url=a.url, target_ref=a.ref,
                                   marker=a.marker, description=a.desc, verify_mode=a.mode,
                                   max_claims=a.max_claims,
                                   repeat_every_sec=a.repeat_hours * 3600, created_by="cli")
                _out({"quest_id": qid, "code": a.code})
            elif a.qcmd == "edit":
                _out(qs.edit_quest(a.code, "cli", title=a.title, description=a.description,
                                   y_reward=a.y_reward, marker=a.marker, target_url=a.target_url,
                                   target_ref=a.target_ref, verify_mode=a.verify_mode))
            elif a.qcmd == "list":
                _out(qs.catalogue("predict"))
            else:
                qs.set_active(a.code, a.qcmd == "resume", "cli")
                _out({"code": a.code, "active": a.qcmd == "resume"})
        elif a.cmd == "post":
            if a.pcmd == "add":
                _out({"id": qs.add_our_post(a.platform, a.post_id, a.url, a.caption,
                                            auto_quest=a.auto_quest)})
            else:
                _out([dict(x) for x in r.store.q("SELECT * FROM our_posts ORDER BY id DESC LIMIT 50")])
    except ReferralError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
