#!/usr/bin/env python3
"""Partner account admin — the ONLY way accounts are created.

  partnersctl create <username> [--name "Display Name"]   (password prompted)
  partnersctl disable <username>
  partnersctl enable  <username>
  partnersctl reset   <username>                          (password prompted)
  partnersctl list
Passwords are never taken on the command line (they would land in shell
history / ps); set PARTNERS_PASSWORD in the env for non-interactive use.
"""
import argparse
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402


def _pw() -> str:
    p = os.environ.get("PARTNERS_PASSWORD")
    if p:
        return p
    a = getpass.getpass("Password (min 12 chars): ")
    b = getpass.getpass("Repeat: ")
    if a != b:
        sys.exit("passwords differ")
    return a


def main() -> int:
    ap = argparse.ArgumentParser(prog="partnersctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("username"); c.add_argument("--name", default="")
    c.add_argument("--admin", action="store_true", help="role=admin"); c.add_argument("--force", action="store_true", help="skip the password length rule")
    sub.add_parser("pending", help="list signups awaiting their emailed code")
    sub.add_parser("approve", help="activate a pending signup without the code").add_argument("email")
    for k in ("disable", "enable", "reset"):
        sub.add_parser(k).add_argument("username")
    sub.add_parser("list")
    a = ap.parse_args()
    auth.init_db()
    if a.cmd == "create":
        pid = auth.create_partner(a.username, _pw(), a.name, role="admin" if a.admin else "partner", force=a.force)
        print(f"created {'admin' if a.admin else 'partner'} #{pid} {a.username.strip().lower()}")
    elif a.cmd == "pending":
        rows = auth.pending_signups()
        print("(none)" if not rows else "\n".join(f"{r['email']:<32} attempts={r['attempts']} expires={time.strftime('%H:%M:%SZ', time.gmtime(r['expires_ts']))}  \"{r['display_name']}\"" for r in rows))
    elif a.cmd == "approve":
        print("approved" if auth.approve_signup(a.email) else "no pending signup for that email")
    elif a.cmd == "reset":
        print("password reset, sessions revoked" if auth.reset_password(a.username, _pw()) else "no such partner")
    elif a.cmd in ("disable", "enable"):
        ok = auth.set_enabled(a.username, a.cmd == "enable")
        print(f"{a.cmd}d" if ok else "no such partner")
    elif a.cmd == "list":
        rows = auth.list_partners()
        if not rows:
            print("(no partners)")
        for r in rows:
            last = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(r["last_login_ts"])) if r["last_login_ts"] else "never"
            print(f"#{r['id']:<3} {r['username']:<28} {r['role']:<7} {'enabled ' if r['enabled'] else 'DISABLED'} "
                  f"sessions={r['sessions']} last_login={last}  \"{r['display_name']}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
