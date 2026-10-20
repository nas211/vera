"""Per-user Paper USD account ledger.

Each user starts with $25,000 of simulated USD equity. Margin is reserved, never
consumed. Entries reuse canonical production signals and live marks, but this module
has no exchange transport and cannot submit a live order.
"""
from __future__ import annotations

import time
import threading
from functools import wraps
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from .accounts import AccountError
from .config import ALLOWED_LEVERAGE, ALLOWED_POSITION_PCT, MAX_OPEN_POSITIONS
from .subscriptions import PLANS
from .paper_ai import entry_budget, exit_reason, parse

D = lambda v: Decimal(str(v))
Q = Decimal("0.00000001")
FEE_RATE = Decimal("0.0006")
SLIPPAGE_RATE = Decimal("0.0002")
PRODUCTION_STRATEGY_CHOICES = {"WHY Alpha", "why-1"}


def _n(v):
    return float(v or 0)


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class PaperService:
    def __init__(self, db, subscriptions, clock=time.time):
        self.db, self.subscriptions, self.clock = db, subscriptions, clock
        self.marks = {}
        self.mark_times = {}
        self._lock = threading.RLock()

    def _plan(self, user_id: str) -> tuple[str, Decimal]:
        ent = self.subscriptions.entitlement(user_id)
        return ent["plan"], D(ent["paper_starting_balance"])

    def _ensure(self, c, user_id: str):
        row = c.execute("select * from public.why_paper_accounts where user_id=%s", (user_id,)).fetchone()
        if row is not None:
            return row
        plan, balance = self._plan(user_id)
        sid = c.execute("insert into public.why_paper_sessions(user_id,plan,starting_balance) values (%s,%s,%s) returning id",
                        (user_id, plan, balance)).fetchone()["id"]
        return c.execute("""insert into public.why_paper_accounts
            (user_id,session_id,starting_balance,cash_balance,equity,peak_equity)
            values (%s,%s,%s,%s,%s,%s) returning *""", (user_id, sid, balance, balance, balance, balance)).fetchone()

    def get(self, ident, limit=50) -> dict:
        with self.db.service() as c:
            self._ensure(c, ident.user_id)
        with self.db.as_user(ident.user_id, ident.email) as c:
            a = c.execute("select * from public.why_paper_accounts").fetchone()
            positions = c.execute("""select id,signal_id,market,direction,strategy,qty,entry_price,mark_price,notional,margin,
                                      leverage,unrealized_pnl,opened_at from public.why_paper_positions
                                      where state='OPEN' and session_id=%s order by opened_at desc""", (a["session_id"],)).fetchall()
            trades = c.execute("""select id,market,direction,strategy,qty,entry_price,exit_price,margin,notional,gross_pnl,
                                   fees,funding,slippage,net_pnl,reason,opened_at,closed_at
                                   from public.why_paper_trades where session_id=%s order by id desc limit %s""", (a["session_id"], max(1, min(100, int(limit))),)).fetchall()
            sessions = c.execute("""select id,plan,starting_balance,ending_equity,archived_reason,started_at,archived_at
                                     from public.why_paper_sessions order by started_at desc limit 20""").fetchall()
        return self._public(a, positions, trades, sessions)

    @staticmethod
    def _public(a, positions=(), trades=(), sessions=()) -> dict:
        available = D(a["equity"]) - D(a["used_margin"])
        ret = ((D(a["equity"]) / D(a["starting_balance"])) - 1) * 100 if D(a["starting_balance"]) else Decimal(0)
        return {
            "paper_enabled": bool(a["enabled"]), "running": bool(a["running"]), "paper_session_id": str(a["session_id"]),
            "paper_starting_balance": _n(a["starting_balance"]), "paper_cash_balance": _n(a["cash_balance"]),
            "paper_equity": _n(a["equity"]), "paper_available_balance": _n(max(available, Decimal(0))),
            "paper_used_margin": _n(a["used_margin"]), "paper_unrealized_pnl": _n(a["unrealized_pnl"]),
            "paper_realized_pnl": _n(a["realized_pnl"]), "paper_total_fees": _n(a["total_fees"]),
            "paper_total_funding": _n(a["total_funding"]), "paper_return_pct": _n(ret),
            "paper_max_drawdown": _n(a["max_drawdown"]), "paper_open_positions": [dict(r) | {"id": str(r["id"])} for r in positions],
            "paper_trade_history": [dict(r) for r in trades], "paper_sessions": [dict(r) | {"id": str(r["id"])} for r in sessions],
            "settings": {"strategy": a["strategy"], "allocation_pct": _n(a["allocation_pct"]),
                         "position_size_pct": _n(a["position_size_pct"]), "leverage": int(a["leverage"]),
                         "risk_preset": a["risk_preset"]},
        }

    @staticmethod
    def _settings(data) -> tuple[Decimal, Decimal, int, str, str]:
        try:
            alloc, pos, lev = D(data.get("allocation_pct")), D(data.get("position_size_pct")), int(data.get("leverage"))
        except Exception as e:
            raise AccountError("invalid_risk_settings", 422) from e
        preset = str(data.get("risk_preset") or "custom").lower()
        strategy = str(data.get("strategy") or "WHY Alpha")[:80]
        if alloc < 1 or alloc > 100 or int(pos) not in ALLOWED_POSITION_PCT or pos != int(pos) or lev not in ALLOWED_LEVERAGE:
            raise AccountError("invalid_risk_settings", 422)
        if preset not in ("conservative", "balanced", "aggressive", "custom"):
            raise AccountError("invalid_risk_preset", 422)
        return alloc, pos, lev, preset, strategy

    @serialized
    def settings(self, ident, data) -> dict:
        alloc, pos, lev, preset, strategy = self._settings(data)
        with self.db.service() as c:
            self._ensure(c, ident.user_id)
            c.execute("""update public.why_paper_accounts set allocation_pct=%s,position_size_pct=%s,leverage=%s,
                         risk_preset=%s,strategy=%s,updated_at=now() where user_id=%s""",
                      (alloc, pos, lev, preset, strategy, ident.user_id))
        return self.get(ident)

    def set_running(self, ident, running: bool) -> dict:
        with self.db.service() as c:
            a = self._ensure(c, ident.user_id)
            if not a["enabled"] and running:
                raise AccountError("paper_disabled", 403)
            c.execute("update public.why_paper_accounts set running=%s,updated_at=now() where user_id=%s", (running, ident.user_id))
        return self.get(ident)

    @serialized
    def reset(self, ident) -> dict:
        plan, balance = self._plan(ident.user_id)
        with self.db.service() as c:
            a = self._ensure(c, ident.user_id)
            c.execute("update public.why_paper_positions set state='RESET_CLOSED',closed_at=now() where session_id=%s and state='OPEN'", (a["session_id"],))
            c.execute("update public.why_paper_sessions set ending_equity=%s,archived_reason='user_reset',archived_at=now() where id=%s",
                      (a["equity"], a["session_id"]))
            sid = c.execute("insert into public.why_paper_sessions(user_id,plan,starting_balance) values (%s,%s,%s) returning id",
                            (ident.user_id, plan, balance)).fetchone()["id"]
            c.execute("""update public.why_paper_accounts set session_id=%s,running=false,starting_balance=%s,cash_balance=%s,
                         equity=%s,used_margin=0,unrealized_pnl=0,realized_pnl=0,total_fees=0,total_funding=0,
                         peak_equity=%s,max_drawdown=0,updated_at=now() where user_id=%s""",
                      (sid, balance, balance, balance, balance, ident.user_id))
        return self.get(ident)

    @serialized
    def grant(self, user_id: str, amount) -> None:
        amount = D(amount)
        if amount <= 0:
            raise AccountError("invalid_credit_grant", 422)
        with self.db.service() as c:
            a = self._ensure(c, user_id)
            c.execute("""update public.why_paper_accounts set cash_balance=cash_balance+%s,equity=equity+%s,
                         peak_equity=greatest(peak_equity,equity+%s),updated_at=now() where user_id=%s""",
                      (amount, amount, amount, user_id))

    # ---------------- canonical production-signal fan-out (never calls an exchange)
    @serialized
    def dispatch(self, sig) -> dict:
        out = {}
        with self.db.service() as c:
            rows = c.execute("select * from public.why_paper_accounts where enabled and running for update").fetchall()
            for a in rows:
                uid = str(a["user_id"])
                try:
                    # A failed user's write must not abort every other account.
                    with c.transaction():
                        out[uid] = self._open(c, a, sig)
                except Exception:
                    out[uid] = "ERROR"
        return out

    def _open(self, c, a, sig) -> str:
        ai = parse(sig.strategy)
        if not ai and str(a["strategy"]) not in PRODUCTION_STRATEGY_CHOICES and str(a["strategy"]) != str(sig.strategy):
            return "SKIPPED:strategy"
        positions = c.execute("select * from public.why_paper_positions where session_id=%s and state='OPEN'", (a["session_id"],)).fetchall()
        blocked, ai_budget = entry_budget(a, positions, sig, self.clock(), self.mark_times)
        if blocked:
            return blocked
        if c.execute("select 1 from public.why_paper_positions where session_id=%s and signal_id=%s", (a["session_id"], sig.signal_id)).fetchone():
            return "DUPLICATE"
        n = c.execute("select count(*) n from public.why_paper_positions where session_id=%s and state='OPEN'", (a["session_id"],)).fetchone()["n"]
        if n >= MAX_OPEN_POSITIONS:
            return "BLOCKED:max_open_positions"
        if c.execute("select 1 from public.why_paper_positions where session_id=%s and market=%s and direction=%s and state='OPEN'",
                     (a["session_id"], sig.market, sig.direction)).fetchone():
            return "BLOCKED:position_already_open"
        equity = D(a["equity"])
        allocated = equity * D(a["allocation_pct"]) / 100
        margin = (allocated * D(a["position_size_pct"]) / 100).quantize(Q, rounding=ROUND_DOWN)
        available = equity - D(a["used_margin"])
        leverage = min(20, int(a["leverage"]))
        if ai:
            margin = min(margin, ai_budget / leverage, max(D(0), available) / (1 + leverage * FEE_RATE)).quantize(Q, rounding=ROUND_DOWN)
        if margin <= 0 or margin > available:
            return "BLOCKED:insufficient_available_balance"
        notional = margin * leverage
        ref = D(sig.ref_price)
        entry = ref * (1 + SLIPPAGE_RATE if sig.direction == "long" else 1 - SLIPPAGE_RATE)
        qty = (notional / entry).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
        fee = (notional * FEE_RATE).quantize(Q)
        c.execute("""insert into public.why_paper_positions
                     (session_id,user_id,signal_id,market,direction,strategy,qty,entry_price,mark_price,notional,margin,leverage)
                     values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (a["session_id"], a["user_id"], sig.signal_id, sig.market, sig.direction, sig.strategy,
                   qty, entry, ref, notional, margin, leverage))
        c.execute("""update public.why_paper_accounts set cash_balance=cash_balance-%s,equity=equity-%s,
                     used_margin=used_margin+%s,total_fees=total_fees+%s,updated_at=now() where user_id=%s""",
                  (fee, fee, margin, fee, a["user_id"]))
        return "OPENED"

    def on_mark(self, market: str, price, ts=None) -> None:
        self.on_marks([(market, price, ts)])

    @serialized
    def on_marks(self, marks) -> None:
        """Process every tick in order, amortizing database round trips.

        Stop at the first exit, persist that prefix and use the ordinary close
        transaction before processing later ticks. Transient stop/target crosses
        and intermediate equity peaks therefore survive batching.
        """
        from types import SimpleNamespace
        pending = list(marks)
        while pending:
            markets = sorted({m for m, _, _ in pending})
            exits, changed, touched = [], {}, {}
            consumed = len(pending)
            with self.db.service() as c:
                positions = c.execute("""select * from public.why_paper_positions
                    where market=any(%s) and state='OPEN' order by id for update""", (markets,)).fetchall()
                users = list({p['user_id'] for p in positions})
                accounts = {a['user_id']: a for a in c.execute("""select * from public.why_paper_accounts
                    where user_id=any(%s::uuid[]) order by user_id for update""", (users,)).fetchall()} if users else {}
                # Reconcile totals from positions, including markets outside this batch.
                totals = {a['user_id']: D(a['u']) for a in c.execute("""select user_id,coalesce(sum(unrealized_pnl),0) u
                    from public.why_paper_positions where user_id=any(%s::uuid[]) and state='OPEN'
                    group by user_id""", (users,)).fetchall()} if users else {}
                by_market = {}
                for p in positions:
                    by_market.setdefault(p['market'], []).append(p)
                for index, (market, px, stamp) in enumerate(pending):
                    px = D(px)
                    now = self.clock()
                    stamp = now if stamp is None else float(stamp)
                    if not px.is_finite() or px <= 0 or not 0 <= now - stamp <= 30:
                        self.mark_times[market] = 0
                        continue
                    self.marks[market], self.mark_times[market] = px, stamp
                    affected = set()
                    for p in by_market.get(market, ()):
                        reason = exit_reason(p, px, self.clock())
                        if reason:
                            exits.append((p['user_id'], p['id'], p['direction'], market, px, reason))
                        pnl = (px - D(p['entry_price'])) * D(p['qty']) * (1 if p['direction'] == 'long' else -1)
                        # Match numeric(20,8) persistence between successive ticks.
                        pnl = pnl.quantize(Q, rounding=ROUND_HALF_UP)
                        uid = p['user_id']
                        totals[uid] += pnl - D(p['unrealized_pnl'])
                        p['mark_price'], p['unrealized_pnl'] = px, pnl
                        changed[p['id']] = p
                        affected.add(uid)
                    for uid in affected:
                        a = accounts[uid]
                        eq = D(a['cash_balance']) + totals[uid]
                        peak = max(D(a['peak_equity']), eq)
                        dd = ((peak - eq) / peak * 100) if peak > 0 else D(0)
                        a['peak_equity'] = peak
                        a['max_drawdown'] = max(D(a['max_drawdown']), dd).quantize(Q, rounding=ROUND_HALF_UP)
                        touched[uid] = (totals[uid], eq, peak, a['max_drawdown'], uid)
                    if exits:
                        consumed = index + 1
                        break
                if changed:
                    c.cursor().executemany("""update public.why_paper_positions set mark_price=%s,
                        unrealized_pnl=%s where id=%s""",
                        [(p['mark_price'], p['unrealized_pnl'], p['id']) for p in changed.values()])
                if touched:
                    c.cursor().executemany("""update public.why_paper_accounts set unrealized_pnl=%s,equity=%s,
                        peak_equity=%s,max_drawdown=%s,updated_at=now() where user_id=%s""", list(touched.values()))
            for uid, pid, direction, market, px, reason in exits:
                self._close(SimpleNamespace(market=market, direction=direction, reason=reason, ref_price=px), uid, pid)
            pending = pending[consumed:]

    def close_position(self, ident, position_id: str) -> str:
        from types import SimpleNamespace
        with self.db.as_user(ident.user_id, ident.email) as c:
            p = c.execute("select market,direction,state from public.why_paper_positions where id=%s and user_id=%s", (position_id, ident.user_id)).fetchone()
        if p is None:
            raise AccountError("position_not_found", 404)
        if p["state"] != "OPEN":
            return "ALREADY_CLOSED"
        age = self.clock() - self.mark_times.get(p["market"], 0)
        if not 0 <= age <= 30 or p["market"] not in self.marks:
            raise AccountError("market_price_unavailable", 503)
        cs = SimpleNamespace(market=p["market"], direction=p["direction"], reason="USER_MARKET_CLOSE", ref_price=self.marks[p["market"]])
        out = self._close(cs, ident.user_id, position_id)
        return out.get(str(ident.user_id), "ALREADY_CLOSED")

    def dispatch_close(self, cs) -> dict:
        return self._close(cs)

    @serialized
    def _close(self, cs, user_id=None, position_id=None) -> dict:
        out = {}
        with self.db.service() as c:
            rows = c.execute("""select p.*,a.cash_balance,a.peak_equity,a.max_drawdown from public.why_paper_positions p
                                join public.why_paper_accounts a on a.user_id=p.user_id
                                where p.market=%s and p.direction=%s and p.state='OPEN'
                                and (%s::uuid is null or p.user_id=%s::uuid)
                                and (%s::uuid is null or p.id=%s::uuid) for update""",
                             (cs.market, cs.direction, user_id, user_id, position_id, position_id)).fetchall()
            for p in rows:
                # Core canonical closes cannot take ownership of an AI trade.
                if position_id is None and parse(p['strategy']):
                    continue
                px = D(cs.ref_price) if getattr(cs, "ref_price", None) is not None else self.marks.get(cs.market, D(p["mark_price"]))
                exit_px = px * (1 - SLIPPAGE_RATE if p["direction"] == "long" else 1 + SLIPPAGE_RATE)
                gross = (exit_px - D(p["entry_price"])) * D(p["qty"]) * (1 if p["direction"] == "long" else -1)
                fee = (D(p["notional"]) * FEE_RATE).quantize(Q)
                slip = abs(exit_px - px) * D(p["qty"])
                ai = parse(p['strategy'])
                funding = (D(p['notional']) * D('.0003') * D(max(0, self.clock() - p['opened_at'].timestamp())) / 28800).quantize(Q) if ai else D(0)
                trade_fees = fee * 2 if ai else fee
                net = gross - trade_fees - funding
                c.execute("update public.why_paper_positions set state='CLOSED',mark_price=%s,unrealized_pnl=0,closed_at=now() where id=%s", (px, p["id"]))
                c.execute("""insert into public.why_paper_trades(session_id,user_id,position_id,signal_id,market,direction,strategy,qty,
                           entry_price,exit_price,margin,notional,gross_pnl,fees,funding,slippage,net_pnl,reason,opened_at)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (p["session_id"],p["user_id"],p["id"],p["signal_id"],p["market"],p["direction"],p["strategy"],p["qty"],
                           p["entry_price"],exit_px,p["margin"],p["notional"],gross,trade_fees,funding,slip,net,cs.reason,p["opened_at"]))
                remaining = c.execute("select coalesce(sum(unrealized_pnl),0) u from public.why_paper_positions where user_id=%s and state='OPEN'", (p["user_id"],)).fetchone()["u"]
                c.execute("""update public.why_paper_accounts set cash_balance=cash_balance+%s-%s,equity=cash_balance+%s-%s+%s,
                           used_margin=greatest(0,used_margin-%s),unrealized_pnl=%s,realized_pnl=realized_pnl+%s,
                           total_fees=total_fees+%s,total_funding=total_funding+%s,updated_at=now() where user_id=%s""",
                          (gross,fee+funding,gross,fee+funding,remaining,p["margin"],remaining,gross,fee,funding,p["user_id"]))
                out[str(p["user_id"])] = "CLOSED"
        return out

    def refresh(self):
        return {}

    def manage_all(self):
        return {}

    @serialized
    def on_disconnect(self, reason="feed_disconnect"):
        self.mark_times.clear()
