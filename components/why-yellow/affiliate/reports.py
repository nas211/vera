"""Data layer for the affiliate dashboard. Every read is server-side (the platform's Postgres
connection): auth.users joined to public.yellow_accounts / yellow_bot_configs, plus the
affiliate_* tables. Harness throwaway users (raw_user_meta_data.why_test_user) are excluded.

Attribution rule (first touch): yellow_accounts.partner_code if set, else the signup metadata
partner, else 'direct'."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .auth import check_password, generate_password, hash_password
from .pnl import PnlCache, empty as pnl_empty, to_public as pnl_public

import hashlib
import secrets as _secrets
from decimal import Decimal

PARTNER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DIRECT = "direct"

PARTNER_EXPR = "coalesce(a.partner_code, nullif(lower(u.raw_user_meta_data->>'partner'), ''), 'direct')"
BASE_FROM = """from auth.users u
  left join public.yellow_accounts a on a.user_id = u.id
  left join public.yellow_bot_configs c on c.account_id = a.id
  where coalesce(u.raw_user_meta_data->>'why_test_user', '') <> 'true'"""


class ReportError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _num(v):
    return None if v is None else float(v)


def _range(days: Optional[int]):
    days = int(days or 30)
    if days < 1 or days > 3650:
        raise ReportError("bad_range")
    return days


MASK = lambda e: (e.split("@")[0][:2] + "***@" + e.split("@", 1)[1]) if e and "@" in e else "unknown"


class Reports:
    def __init__(self, db, public_base: str, pnl: Optional[PnlCache] = None, default_rev_share: float = 25.0):
        self.db, self.public_base = db, public_base.rstrip("/")
        self.pnl = pnl
        self.default_rev_share = float(default_rev_share)

    # ------------------------------------------------------------ trading results (per account, from tenant ledgers)
    def _pnl_for(self, account_ids) -> dict:
        if not self.pnl:
            return {str(a): pnl_empty() for a in account_ids if a}
        return {str(a): self.pnl.get(str(a)) for a in account_ids if a}

    @staticmethod
    def _sum_pnl(results) -> dict:
        tot = pnl_empty()
        for r in results:
            for k in ("closed", "wins", "losses", "open_positions"):
                tot[k] += r[k]
            for k in ("realized_pnl", "fees", "volume"):
                tot[k] += r[k]
        return tot

    def _revenue(self, fees, pct) -> float:
        return float(Decimal(str(fees)) * Decimal(str(pct)) / Decimal(100))

    # ------------------------------------------------------------ links
    def link_for(self, code: str) -> str:
        return f"{self.public_base}/r/{code}"

    # ------------------------------------------------------------ partners
    def partners(self, scope: Optional[str] = None) -> list:
        with self.db.service() as c:
            rows = c.execute("select code, name, active, notes, created_by, created_at, updated_at, rev_share_pct from public.affiliate_partners order by created_at").fetchall()
            accts = c.execute(f"select {PARTNER_EXPR} as partner, a.id as account_id {BASE_FROM} and a.id is not null").fetchall()
            stats = {r["partner"]: r for r in c.execute(f"""
                select {PARTNER_EXPR} as partner,
                       count(*) as signups,
                       count(*) filter (where u.created_at >= now() - interval '7 days') as signups_7d,
                       count(*) filter (where u.created_at >= now() - interval '30 days') as signups_30d,
                       count(*) filter (where u.email_confirmed_at is not null) as confirmed,
                       count(*) filter (where a.connected_at is not null and a.state <> 'DISCONNECTED') as connected,
                       count(*) filter (where a.state in ('ACTIVE','DRAINING')) as active,
                       max(u.created_at) as last_signup_at
                {BASE_FROM} group by 1""").fetchall()}
        by_partner_accts = {}
        for r in accts:
            by_partner_accts.setdefault(r["partner"], []).append(str(r["account_id"]))
        pnl_all = self._pnl_for([a for lst in by_partner_accts.values() for a in lst])
        out = []
        known = set()
        for r in rows:
            known.add(r["code"])
            if scope and r["code"] != scope:
                continue
            s = stats.get(r["code"], {})
            out.append(self._partner_row(r["code"], r["name"], r["active"], r["notes"], r["created_by"], r["created_at"], s,
                                         pnl=self._sum_pnl(pnl_all[a] for a in by_partner_accts.get(r["code"], [])), rev_share=float(r["rev_share_pct"])))
        # traffic tagged with a code nobody registered (or direct signups) still shows up
        for code, s in stats.items():
            if code not in known and (not scope or code == scope):
                out.append(self._partner_row(code, "Direct / no partner" if code == DIRECT else f"{code} (unregistered)", code != DIRECT, None, None, None, s, registered=False,
                                             pnl=self._sum_pnl(pnl_all[a] for a in by_partner_accts.get(code, [])), rev_share=self.default_rev_share))
        out.sort(key=lambda p: (-int(p["signups"]), p["code"]))
        return out

    def _partner_row(self, code, name, active, notes, created_by, created_at, s, registered=True, pnl=None, rev_share=None) -> dict:
        pnl = pnl or pnl_empty()
        pct = self.default_rev_share if rev_share is None else rev_share
        return dict(code=code, name=name, active=bool(active), notes=notes, created_by=created_by, created_at=_iso(created_at),
                    registered=registered, link=self.link_for(code) if code != DIRECT else None,
                    rev_share_pct=pct, fees=float(pnl["fees"]), revenue=self._revenue(pnl["fees"], pct), realized_pnl=float(pnl["realized_pnl"]),
                    closed_trades=pnl["closed"], wins=pnl["wins"], volume=float(pnl["volume"]), open_positions=pnl["open_positions"],
                    signups=int(s.get("signups", 0) or 0), signups_7d=int(s.get("signups_7d", 0) or 0), signups_30d=int(s.get("signups_30d", 0) or 0),
                    confirmed=int(s.get("confirmed", 0) or 0), connected=int(s.get("connected", 0) or 0), active_trading=int(s.get("active", 0) or 0),
                    last_signup_at=_iso(s.get("last_signup_at")))

    def add_partner(self, actor: str, code: str, name: str, notes: Optional[str] = None) -> dict:
        code = (code or "").strip().lower()
        name = (name or "").strip()
        if not PARTNER_RE.match(code) or code == DIRECT:
            raise ReportError("invalid_partner_code", 422)
        if not name or len(name) > 80:
            raise ReportError("invalid_partner_name", 422)
        with self.db.service() as c:
            dup = c.execute("select 1 from public.affiliate_partners where code = %s", (code,)).fetchone()
            if dup:
                raise ReportError("partner_exists", 409)
            c.execute("insert into public.affiliate_partners(code, name, notes, created_by) values (%s, %s, %s, %s)", (code, name, (notes or None), actor))
            self._audit(c, actor, "partner_added", {"code": code, "name": name})
        return dict(code=code, name=name, link=self.link_for(code))

    def update_partner(self, actor: str, code: str, name: Optional[str] = None, notes: Optional[str] = None, active: Optional[bool] = None) -> dict:
        with self.db.service() as c:
            row = c.execute("select code from public.affiliate_partners where code = %s", (code,)).fetchone()
            if not row:
                raise ReportError("partner_not_found", 404)
            if name is not None:
                name = name.strip()
                if not name or len(name) > 80:
                    raise ReportError("invalid_partner_name", 422)
                c.execute("update public.affiliate_partners set name = %s where code = %s", (name, code))
            if notes is not None:
                c.execute("update public.affiliate_partners set notes = %s where code = %s", (notes.strip() or None, code))
            if active is not None:
                c.execute("update public.affiliate_partners set active = %s where code = %s", (bool(active), code))
            self._audit(c, actor, "partner_updated", {"code": code, "name": name, "active": active, "notes": notes is not None})
        return {"code": code}

    def set_rev_share(self, actor: str, code: str, pct) -> dict:
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            raise ReportError("invalid_rev_share", 422)
        if pct < 0 or pct > 100:
            raise ReportError("invalid_rev_share", 422)
        with self.db.service() as c:
            n = c.execute("update public.affiliate_partners set rev_share_pct = %s where code = %s", (pct, code)).rowcount
            if not n:
                raise ReportError("partner_not_found", 404)
            self._audit(c, actor, "rev_share_set", {"code": code, "pct": pct})
        return {"code": code, "rev_share_pct": pct}

    # ------------------------------------------------------------ overview / series
    def overview(self, days: int = 30, partner: Optional[str] = None) -> dict:
        days = _range(days)
        scope_sql, scope_args = "", ()
        if partner:
            if not PARTNER_RE.match(partner):
                raise ReportError("invalid_partner_code", 422)
            scope_sql, scope_args = f" and {PARTNER_EXPR} = %s", (partner,)
        with self.db.service() as c:
            tot = c.execute(f"""
                select count(*) as signups,
                       count(*) filter (where u.created_at >= now() - interval '1 day') as signups_24h,
                       count(*) filter (where u.created_at >= now() - interval '7 days') as signups_7d,
                       count(*) filter (where u.created_at >= now() - interval '30 days') as signups_30d,
                       count(*) filter (where u.email_confirmed_at is not null) as confirmed,
                       count(*) filter (where a.connected_at is not null and a.state <> 'DISCONNECTED') as connected,
                       count(*) filter (where a.state in ('ACTIVE','DRAINING')) as active,
                       coalesce(sum(a.balance_equity) filter (where a.state in ('ACTIVE','DRAINING')), 0) as active_equity,
                       coalesce(sum(c.position_size_usd) filter (where a.state in ('ACTIVE','DRAINING')), 0) as active_size
                {BASE_FROM}{scope_sql}""", scope_args).fetchone()
            series = c.execute(f"""
                select date_trunc('day', u.created_at)::date as day, {PARTNER_EXPR} as partner, count(*) as n
                {BASE_FROM}{scope_sql} and u.created_at >= (now() at time zone 'utc')::date - %s::int
                group by 1, 2 order by 1""", (*scope_args, days - 1)).fetchall()
            states = c.execute(f"select coalesce(a.state, 'NO_ACCOUNT_ROW') as state, count(*) as n {BASE_FROM}{scope_sql} group by 1", scope_args).fetchall()
            acct_ids = [str(r["id"]) for r in c.execute(f"select a.id {BASE_FROM}{scope_sql} and a.id is not null", scope_args).fetchall()]
        pnl_tot = self._sum_pnl(self._pnl_for(acct_ids).values())
        partners = self.partners(scope=partner)
        revenue = sum(p["revenue"] for p in partners)
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days - 1)
        by_day = {}
        partners_seen = set()
        for r in series:
            d = r["day"].isoformat()
            by_day.setdefault(d, {})[r["partner"]] = int(r["n"])
            partners_seen.add(r["partner"])
        daily = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            daily.append({"day": d, "by_partner": by_day.get(d, {}), "total": sum(by_day.get(d, {}).values())})
        return dict(
            range_days=days, as_of=datetime.now(timezone.utc).isoformat(),
            totals=dict(signups=int(tot["signups"]), signups_24h=int(tot["signups_24h"]), signups_7d=int(tot["signups_7d"]), signups_30d=int(tot["signups_30d"]),
                        confirmed=int(tot["confirmed"]), connected=int(tot["connected"]), active_trading=int(tot["active"]),
                        active_equity=_num(tot["active_equity"]), active_size_usd=_num(tot["active_size"]),
                        fees=float(pnl_tot["fees"]), revenue=revenue, realized_pnl=float(pnl_tot["realized_pnl"]),
                        closed_trades=pnl_tot["closed"], wins=pnl_tot["wins"], volume=float(pnl_tot["volume"]), open_positions=pnl_tot["open_positions"]),
            rev_share_pct=self.default_rev_share, scope=partner,
            daily=daily, series_partners=sorted(partners_seen, key=lambda p: (p == DIRECT, p)),
            states={r["state"]: int(r["n"]) for r in states},
            partners=partners,
        )

    # ------------------------------------------------------------ signups list
    def signups(self, partner: Optional[str] = None, q: Optional[str] = None, days: Optional[int] = None, limit: int = 200, offset: int = 0,
                mask_emails: bool = False) -> dict:
        limit = max(1, min(int(limit or 200), 1000))
        offset = max(0, int(offset or 0))
        where, args = [], []
        if partner:
            partner = partner.strip().lower()
            if not PARTNER_RE.match(partner):
                raise ReportError("invalid_partner_code", 422)
            where.append(f"and {PARTNER_EXPR} = %s"); args.append(partner)
        if q:
            q = q.strip()[:80]
            where.append("and u.email ilike %s"); args.append(f"%{q}%")
        if days:
            where.append("and u.created_at >= now() - make_interval(days => %s)"); args.append(_range(days))
        sql = f"""
            select u.id, u.email, u.created_at, u.email_confirmed_at, u.last_sign_in_at, a.id as account_id,
                   {PARTNER_EXPR} as partner, u.raw_user_meta_data->>'landing' as landing, u.raw_user_meta_data->>'referrer' as referrer,
                   a.state, a.state_reason, a.connected_at, a.verified_at, a.verification_state, a.balance_equity, a.balance_at, a.api_key_masked,
                   c.position_size_usd, c.activated_at, c.entries_paused,
                   count(*) over () as total
            {BASE_FROM} {' '.join(where)}
            order by u.created_at desc limit %s offset %s"""
        with self.db.service() as c:
            rows = c.execute(sql, (*args, limit, offset)).fetchall()
        total = int(rows[0]["total"]) if rows else 0
        pnl = self._pnl_for([r["account_id"] for r in rows])
        items = [dict(user_id=str(r["id"]), email=MASK(r["email"]) if mask_emails else r["email"], signed_up_at=_iso(r["created_at"]), confirmed_at=_iso(r["email_confirmed_at"]),
                      **pnl_public(pnl.get(str(r["account_id"]), pnl_empty())),
                      last_sign_in_at=_iso(r["last_sign_in_at"]), partner=r["partner"], landing=r["landing"], referrer=r["referrer"],
                      yellow_state=r["state"] or "NO_ACCOUNT_ROW", state_reason=r["state_reason"], connected_at=_iso(r["connected_at"]),
                      verified_at=_iso(r["verified_at"]), verification_state=r["verification_state"], balance_equity=_num(r["balance_equity"]),
                      balance_at=_iso(r["balance_at"]), api_key_masked=r["api_key_masked"],
                      # the bot config carries a default size before anyone touched it; only a connected account has a meaningful size
                      position_size_usd=r["position_size_usd"] if r["state"] in ("VERIFIED", "ACTIVE", "PAUSED", "DRAINING") else None,
                      activated_at=_iso(r["activated_at"]), entries_paused=r["entries_paused"]) for r in rows]
        return dict(total=total, limit=limit, offset=offset, items=items)

    def signups_csv(self, **kw) -> str:
        import csv, io
        data = self.signups(limit=1000, **kw)
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = ["email", "partner", "signed_up_at", "confirmed_at", "last_sign_in_at", "yellow_state", "connected_at", "verified_at",
                "position_size_usd", "activated_at", "balance_equity", "closed_trades", "wins", "realized_pnl", "fees", "volume", "open_positions", "landing", "referrer"]
        w.writerow(cols)
        for it in data["items"]:
            w.writerow([it.get(k, "") if it.get(k) is not None else "" for k in cols])
        return buf.getvalue()

    # ------------------------------------------------------------ team
    def team(self) -> list:
        with self.db.service() as c:
            rows = c.execute("select id, email, name, role, active, created_at, last_login_at, partner_code from public.affiliate_team_users order by created_at").fetchall()
        return [dict(id=str(r["id"]), email=r["email"], name=r["name"], role=r["role"], active=r["active"], created_at=_iso(r["created_at"]),
                     last_login_at=_iso(r["last_login_at"]), partner_code=r["partner_code"]) for r in rows]

    def add_team_user(self, actor: str, email: str, name: Optional[str] = None, role: str = "member", password: Optional[str] = None,
                      partner_code: Optional[str] = None) -> dict:
        email = (email or "").strip().lower()
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", email):
            raise ReportError("invalid_email", 422)
        if role not in ("admin", "member", "partner"):
            raise ReportError("invalid_role", 422)
        partner_code = (partner_code or "").strip().lower() or None
        if role == "partner":
            if not partner_code or not PARTNER_RE.match(partner_code):
                raise ReportError("partner_code_required", 422)
        else:
            partner_code = None
        pw = password or generate_password()
        if len(pw) < 12:
            raise ReportError("password_too_short", 422)
        with self.db.service() as c:
            if c.execute("select 1 from public.affiliate_team_users where lower(email) = %s", (email,)).fetchone():
                raise ReportError("team_user_exists", 409)
            if partner_code and not c.execute("select 1 from public.affiliate_partners where code = %s", (partner_code,)).fetchone():
                raise ReportError("partner_not_found", 404)
            row = c.execute("insert into public.affiliate_team_users(email, name, role, password_hash, partner_code) values (%s, %s, %s, %s, %s) returning id",
                            (email, (name or "").strip() or None, role, hash_password(pw), partner_code)).fetchone()
            self._audit(c, actor, "team_user_added", {"email": email, "role": role, "partner_code": partner_code})
        return dict(id=str(row["id"]), email=email, role=role, partner_code=partner_code, password=pw)     # password shown ONCE to the admin who created it

    def set_team_password(self, actor: str, email: str, password: Optional[str] = None) -> dict:
        pw = password or generate_password()
        if len(pw) < 12:
            raise ReportError("password_too_short", 422)
        with self.db.service() as c:
            n = c.execute("update public.affiliate_team_users set password_hash = %s where lower(email) = %s", (hash_password(pw), email.strip().lower())).rowcount
            if not n:
                raise ReportError("team_user_not_found", 404)
            self._audit(c, actor, "team_password_reset", {"email": email.strip().lower()})
        return dict(email=email.strip().lower(), password=pw)

    def set_team_active(self, actor: str, email: str, active: bool) -> dict:
        with self.db.service() as c:
            n = c.execute("update public.affiliate_team_users set active = %s where lower(email) = %s", (bool(active), email.strip().lower())).rowcount
            if not n:
                raise ReportError("team_user_not_found", 404)
            self._audit(c, actor, "team_user_active", {"email": email.strip().lower(), "active": bool(active)})
        return dict(email=email.strip().lower(), active=bool(active))

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        email = (email or "").strip().lower()
        with self.db.service() as c:
            row = c.execute("select id, email, name, role, active, password_hash, partner_code from public.affiliate_team_users where lower(email) = %s", (email,)).fetchone()
            if not row or not row["active"] or not check_password(password or "", row["password_hash"]):
                return None
            c.execute("update public.affiliate_team_users set last_login_at = now() where id = %s", (row["id"],))
            self._audit(c, email, "login", {})
        return dict(id=str(row["id"]), email=row["email"], name=row["name"], role=row["role"], partner_code=row["partner_code"])

    def change_password(self, user_id: str, current: str, new: str) -> dict:
        if not new or len(new) < 12:
            raise ReportError("password_too_short", 422)
        with self.db.service() as c:
            row = c.execute("select email, password_hash from public.affiliate_team_users where id = %s and active", (user_id,)).fetchone()
            if not row or not check_password(current or "", row["password_hash"]):
                raise ReportError("wrong_password", 403)
            c.execute("update public.affiliate_team_users set password_hash = %s where id = %s", (hash_password(new), user_id))
            self._audit(c, row["email"], "password_changed", {})
        return {"ok": True}

    # ------------------------------------------------------------ password reset by email
    RESET_TTL_MIN = 60

    def create_reset(self, email: str) -> Optional[dict]:
        """Returns {token, email, name} for an active user, None otherwise (caller answers identically)."""
        email = (email or "").strip().lower()
        with self.db.service() as c:
            row = c.execute("select id, email, name from public.affiliate_team_users where lower(email) = %s and active", (email,)).fetchone()
            if not row:
                return None
            n = c.execute("select count(*) as n from public.affiliate_password_resets where user_id = %s and created_at > now() - interval '1 hour'", (row["id"],)).fetchone()["n"]
            if int(n) >= 5:
                return None                                     # silently rate-limited
            token = _secrets.token_urlsafe(32)
            c.execute("insert into public.affiliate_password_resets(user_id, token_hash, expires_at) values (%s, %s, now() + make_interval(mins => %s))",
                      (row["id"], hashlib.sha256(token.encode()).hexdigest(), self.RESET_TTL_MIN))
            self._audit(c, row["email"], "password_reset_requested", {})
        return dict(token=token, email=row["email"], name=row["name"])

    def check_reset(self, token: str) -> Optional[str]:
        if not token or len(token) > 128:
            return None
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.db.service() as c:
            row = c.execute("select u.email from public.affiliate_password_resets r join public.affiliate_team_users u on u.id = r.user_id "
                            "where r.token_hash = %s and r.used_at is null and r.expires_at > now() and u.active", (h,)).fetchone()
        return row["email"] if row else None

    def consume_reset(self, token: str, new_password: str) -> str:
        if not new_password or len(new_password) < 12:
            raise ReportError("password_too_short", 422)
        h = hashlib.sha256((token or "").encode()).hexdigest()
        with self.db.service() as c:
            row = c.execute("select r.id, r.user_id, u.email from public.affiliate_password_resets r join public.affiliate_team_users u on u.id = r.user_id "
                            "where r.token_hash = %s and r.used_at is null and r.expires_at > now() and u.active for update", (h,)).fetchone()
            if not row:
                raise ReportError("reset_invalid", 400)
            c.execute("update public.affiliate_password_resets set used_at = now() where id = %s", (row["id"],))
            c.execute("update public.affiliate_password_resets set used_at = now() where user_id = %s and used_at is null", (row["user_id"],))
            c.execute("update public.affiliate_team_users set password_hash = %s where id = %s", (hash_password(new_password), row["user_id"]))
            self._audit(c, row["email"], "password_reset_completed", {})
        return row["email"]

    # ------------------------------------------------------------ owner cleanup (CLI only)
    def delete_signup(self, actor: str, email: str) -> dict:
        """Permanently deletes a signup (auth.users row; yellow_* rows cascade; Vault secrets purged).
        CLI only, for test/fake accounts. Refuses accounts that are trading."""
        email = (email or "").strip().lower()
        with self.db.service() as c:
            u = c.execute("select u.id, a.id as account_id, a.state from auth.users u left join public.yellow_accounts a on a.user_id = u.id where lower(u.email) = %s", (email,)).fetchone()
            if not u:
                raise ReportError("signup_not_found", 404)
            if u["state"] in ("ACTIVE", "DRAINING"):
                raise ReportError("account_is_trading", 409)
            if u["account_id"]:
                try:
                    c.execute("delete from vault.secrets where name like %s", (f"yellow:{u['account_id']}:%",))
                except Exception:
                    pass
            c.execute("delete from auth.users where id = %s", (u["id"],))
            self._audit(c, actor, "signup_deleted", {"email": email})
        return {"deleted": email}

    def audit(self, limit: int = 50) -> list:
        with self.db.service() as c:
            rows = c.execute("select id, actor, action, detail, created_at from public.affiliate_audit order by id desc limit %s", (max(1, min(int(limit), 500)),)).fetchall()
        return [dict(id=r["id"], actor=r["actor"], action=r["action"], detail=r["detail"], at=_iso(r["created_at"])) for r in rows]

    @staticmethod
    def _audit(c, actor: str, action: str, detail: dict) -> None:
        c.execute("insert into public.affiliate_audit(actor, action, detail) values (%s, %s, %s::jsonb)", (actor, action, json.dumps(detail, default=str)))
