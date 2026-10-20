"""Owner CLI for the affiliate dashboard (runs on the box with secrets/customer.env loaded):

  python3 -m affiliate.admin add-user EMAIL [--name N] [--role admin|member]   # prints the password ONCE
  python3 -m affiliate.admin reset-password EMAIL
  python3 -m affiliate.admin deactivate EMAIL | activate EMAIL
  python3 -m affiliate.admin list-users
  python3 -m affiliate.admin add-partner CODE "Name" [--notes ...]
  python3 -m affiliate.admin list-partners
  python3 -m affiliate.admin init-secret        # writes secrets/affiliate.env if missing
  python3 -m affiliate.admin add-partner-user EMAIL CODE [--name N]   # partner-scoped login (sees only its own numbers)
  python3 -m affiliate.admin set-rev-share CODE PCT
  python3 -m affiliate.admin set-secret RESEND_API_KEY      # reads the value from stdin (paste, Enter, Ctrl-D); never in shell history
  python3 -m affiliate.admin set-secret ALERT_TELEGRAM_CHAT_IDS
  python3 -m affiliate.admin test-alert
  python3 -m affiliate.admin list-signups [--partner CODE]
  python3 -m affiliate.admin delete-signup EMAIL   # permanently removes a test/fake signup (never a trading account)
"""
from __future__ import annotations
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load_env():
    for name in ("customer.env", "affiliate.env"):
        p = os.path.join(HERE, "secrets", name)
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())


def main(argv=None) -> int:
    _load_env()
    from customer.db import Database
    from .config import Config
    from .reports import ReportError, Reports
    from .auth import env_secret_line

    ap = argparse.ArgumentParser(prog="affiliate.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user"); a.add_argument("email"); a.add_argument("--name", default=None); a.add_argument("--role", default="member", choices=["admin", "member"])
    r = sub.add_parser("reset-password"); r.add_argument("email")
    d = sub.add_parser("deactivate"); d.add_argument("email")
    e = sub.add_parser("activate"); e.add_argument("email")
    sub.add_parser("list-users")
    p = sub.add_parser("add-partner"); p.add_argument("code"); p.add_argument("name"); p.add_argument("--notes", default=None)
    sub.add_parser("list-partners")
    sub.add_parser("init-secret")
    pu = sub.add_parser("add-partner-user"); pu.add_argument("email"); pu.add_argument("code"); pu.add_argument("--name", default=None)
    rs = sub.add_parser("set-rev-share"); rs.add_argument("code"); rs.add_argument("pct", type=float)
    ss = sub.add_parser("set-secret"); ss.add_argument("key", choices=["RESEND_API_KEY", "ALERT_TELEGRAM_TOKEN", "ALERT_TELEGRAM_CHAT_IDS", "ALERT_EMAILS", "AFFILIATE_MAIL_FROM", "AFFILIATE_BASE_URL"])
    sub.add_parser("test-alert")
    ls = sub.add_parser("list-signups"); ls.add_argument("--partner", default=None)
    ds = sub.add_parser("delete-signup"); ds.add_argument("email")
    ns = ap.parse_args(argv)

    if ns.cmd == "init-secret":
        path = os.path.join(HERE, "secrets", "affiliate.env")
        if os.path.exists(path):
            print(f"exists: {path}")
            return 0
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("# affiliate dashboard session-signing secret — generated, mode 0600, never commit\n" + env_secret_line())
        print(f"written: {path}")
        return 0

    if ns.cmd == "set-secret":
        path = os.path.join(HERE, "secrets", "affiliate.env")
        value = sys.stdin.read().strip()
        if not value:
            print("empty value", file=sys.stderr)
            return 2
        lines = []
        if os.path.exists(path):
            with open(path) as f:
                lines = [l for l in f.read().splitlines() if not l.startswith(ns.key + "=")]
        lines.append(f"{ns.key}={value}")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"{ns.key} stored in {path} ({len(value)} chars). Restart: systemctl restart why-affiliate")
        return 0

    cfg = Config.from_env()
    if not cfg.db_url:
        print("SUPABASE_DB_URL missing (secrets/customer.env)", file=sys.stderr)
        return 2
    rep = Reports(Database(cfg.db_url, max_conn=1), cfg.public_base)
    actor = "cli:" + (os.environ.get("SUDO_USER") or os.environ.get("USER") or "root")
    try:
        if ns.cmd == "add-user":
            out = rep.add_team_user(actor, ns.email, ns.name, ns.role)
            print(f"created {out['email']} role={out['role']}\npassword (shown once): {out['password']}")
        elif ns.cmd == "reset-password":
            out = rep.set_team_password(actor, ns.email)
            print(f"new password for {out['email']} (shown once): {out['password']}")
        elif ns.cmd in ("deactivate", "activate"):
            print(rep.set_team_active(actor, ns.email, ns.cmd == "activate"))
        elif ns.cmd == "list-users":
            for u in rep.team():
                print(f"{u['email']:40} {u['role']:7} {'active' if u['active'] else 'OFF':6} last_login={u['last_login_at'] or '-'}")
        elif ns.cmd == "add-partner":
            out = rep.add_partner(actor, ns.code, ns.name, ns.notes)
            print(f"partner {out['code']} ({out['name']}) link: {out['link']}")
        elif ns.cmd == "add-partner-user":
            out = rep.add_team_user(actor, ns.email, ns.name, "partner", partner_code=ns.code)
            print(f"created partner login {out['email']} for partner {ns.code}\npassword (shown once): {out['password']}")
        elif ns.cmd == "set-rev-share":
            print(rep.set_rev_share(actor, ns.code, ns.pct))
        elif ns.cmd == "test-alert":
            from .alerts import Alerts
            a = Alerts(rep.db, cfg)
            print(a.channels()); print(a.test_message())
        elif ns.cmd == "list-signups":
            for u in rep.signups(partner=ns.partner, limit=1000)["items"]:
                print(f"{u['email']:44} {u['partner']:10} {u['signed_up_at'][:16]:17} {'confirmed' if u['confirmed_at'] else 'unconfirmed':12} {u['yellow_state']}")
        elif ns.cmd == "delete-signup":
            print(rep.delete_signup(actor, ns.email))
        elif ns.cmd == "list-partners":
            for p_ in rep.partners():
                print(f"{p_['code']:16} {p_['name']:28} signups={p_['signups']:4} 7d={p_['signups_7d']:3} connected={p_['connected']:3} active={p_['active_trading']:3} {p_['link'] or ''}")
    except ReportError as ex:
        print(f"error: {ex.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
