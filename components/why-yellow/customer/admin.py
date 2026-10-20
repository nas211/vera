"""Owner CLI (server-side only; never exposed over HTTP).

  python3 -m customer.admin accounts                 list accounts (no secrets)
  python3 -m customer.admin plan <user_id> <free|core|pro|prime> [--status active]
  python3 -m customer.admin grant-paper <user_id> <simulated_usd>
  python3 -m customer.admin reset-paper <user_id>
  python3 -m customer.admin disable-paper <user_id>
  python3 -m customer.admin stop-live <user_id>
  python3 -m customer.admin readiness                readiness snapshot
  python3 -m customer.admin attest <account_id> --scopes read:futures,trade:futures --evidence "..."
      Records that the OWNER verified the key's scopes out-of-band (Yellow UI) because
      Yellow does not expose scopes to API-key auth (audit Q-02). A withdraw/transfer scope
      REJECTS the credential (purged, never VERIFIED) and records the evidence.
  /opt/why-yellow/venv/bin/python -m customer.admin yellow-key-scopes   (venv: eth_account)
      READ-ONLY: logs the box wallet (secrets/wallet.json) into Yellow with a signed challenge
      and lists the API keys minted under it with their scopes/expiry (JWT-only endpoint).
      Prints key ids and masked prefixes only. Real Yellow scope evidence for keys of that wallet."""
from __future__ import annotations
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from .config import Config


def yellow_key_scopes() -> int:
    import yellow_exec as ye
    from .log import redact
    addr, pk = ye.load_or_create_wallet() if os.path.exists(ye.WALLET_F) else (None, None)
    if not addr:
        print("no box wallet on disk"); return 2
    tok = ye.jwt_login(addr, pk)["access_token"]
    keys = ye.jwt_get("/accounts/api-keys", tok, base=ye.TRADE_BASE)
    items = keys if isinstance(keys, list) else (keys.get("keys") or keys.get("api_keys") or keys.get("data") or [])
    out = []
    for k in items:
        kk = str(k.get("api_key") or k.get("key") or k.get("prefix") or "")
        out.append({"id": k.get("id"), "prefix": (kk[:6] + "…") if kk else None, "scopes": k.get("scopes"), "description": k.get("description"),
                    "expires_at": k.get("expires_at"), "state": k.get("state", k.get("status")), "last_used_at": k.get("last_used_at"),
                    "ip_whitelist": k.get("ip_whitelist"), "app_session_id": k.get("app_session_id"),
                    "created_at": k.get("created_at"), "raw_fields": sorted(k.keys())})
    print(redact(json.dumps({"wallet": addr, "keys": out}, indent=2, default=str)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts")
    sub.add_parser("readiness")
    a = sub.add_parser("attest")
    a.add_argument("account_id"); a.add_argument("--scopes", required=True); a.add_argument("--evidence", required=True)
    sub.add_parser("yellow-key-scopes")
    p = sub.add_parser("plan"); p.add_argument("user_id"); p.add_argument("plan", choices=("free", "core", "pro", "prime")); p.add_argument("--billing", choices=("monthly", "annual"), default="monthly"); p.add_argument("--status", choices=("active", "past_due", "cancelled", "expired"), default="active")
    g = sub.add_parser("grant-paper"); g.add_argument("user_id"); g.add_argument("simulated_usd", type=float)
    r = sub.add_parser("reset-paper"); r.add_argument("user_id")
    d = sub.add_parser("disable-paper"); d.add_argument("user_id")
    x = sub.add_parser("stop-live"); x.add_argument("user_id")
    args = ap.parse_args()
    if args.cmd == "yellow-key-scopes":
        return yellow_key_scopes()
    from .accounts import AccountService
    from .db import Database
    from .readiness import Readiness
    from .yellow_probe import YellowVerifier
    cfg = Config.from_env()
    if not cfg.supabase_configured:
        print("supabase not configured (SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_DB_URL)"); return 2
    db = Database(cfg.supabase_db_url)
    svc = AccountService(db, YellowVerifier())
    if args.cmd == "accounts":
        with db.service() as c:
            rows = c.execute("""select u.id user_id,u.email,s.plan,s.status billing_status,p.equity paper_equity,
                              p.session_id paper_session,p.enabled paper_enabled,
                              (y.state is not null and y.state<>'DISCONNECTED') yellow_connected,
                              (w.state is not null and w.state<>'DISCONNECTED') weex_connected,
                              coalesce(y.state in ('ACTIVE','DRAINING') or w.state='ACTIVE',false) live_trading
                              from auth.users u left join public.why_subscriptions s on s.user_id=u.id
                              left join public.why_paper_accounts p on p.user_id=u.id
                              left join public.yellow_accounts y on y.user_id=u.id
                              left join public.weex_accounts w on w.user_id=u.id order by u.created_at""").fetchall()
        print(json.dumps([{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows], indent=2)); return 0
    if args.cmd == "readiness":
        print(json.dumps(Readiness(cfg, db).snapshot(force=True), indent=2, default=str)); return 0
    if args.cmd == "attest":
        print(json.dumps(svc.attest_permissions(args.account_id, [s.strip() for s in args.scopes.split(",") if s.strip()], args.evidence, actor="owner-cli"))); return 0
    if args.cmd == "plan":
        billing = None if args.plan == "free" else args.billing
        with db.service() as c:
            c.execute("""insert into public.why_subscriptions(user_id,plan,billing,status,activated_via)
                         values (%s,%s,%s,%s,'owner') on conflict(user_id) do update set plan=excluded.plan,
                         billing=excluded.billing,status=excluded.status,activated_via='owner',updated_at=now()""",
                      (args.user_id,args.plan,billing,args.status))
        print(json.dumps({"updated": True, "user_id": args.user_id, "plan": args.plan, "status": args.status})); return 0
    if args.cmd in ("grant-paper", "reset-paper"):
        from .auth import Identity
        from .paper import PaperService
        from .subscriptions import SubscriptionService
        with db.service() as c:
            user = c.execute("select id,email from auth.users where id=%s", (args.user_id,)).fetchone()
        if not user: print("user not found"); return 2
        paper = PaperService(db, SubscriptionService(db)); ident = Identity(str(user["id"]), user["email"], "authenticated")
        if args.cmd == "grant-paper": paper.grant(args.user_id, args.simulated_usd)
        else: paper.reset(ident)
        print(json.dumps({"updated": True, "user_id": args.user_id, "action": args.cmd})); return 0
    if args.cmd == "disable-paper":
        with db.service() as c: c.execute("update public.why_paper_accounts set enabled=false,running=false,updated_at=now() where user_id=%s", (args.user_id,))
        print(json.dumps({"updated": True, "user_id": args.user_id, "paper_enabled": False})); return 0
    if args.cmd == "stop-live":
        with db.service() as c:
            c.execute("update public.yellow_bot_configs set entries_paused=true where user_id=%s", (args.user_id,))
            c.execute("update public.weex_accounts set entries_paused=true,state=case when state='ACTIVE' then 'PAUSED' else state end,updated_at=now() where user_id=%s", (args.user_id,))
        print(json.dumps({"updated": True, "user_id": args.user_id, "new_live_entries": False})); return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
