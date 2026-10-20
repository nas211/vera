"""Per-customer trading results, read from each tenant's execution ledger
(state/customer/tenants/<account_id>/ledger.sqlite, opened read-only). Nothing here writes.

Per account: closed trades, wins, realized P&L (net of fees), fees paid, volume traded, open
positions. Realized P&L of a CLOSED position = direction * (exit_avg - entry_avg) * exit_qty
minus every fee booked on that position's orders. WHY's revenue = fees * rev_share_pct / 100
(Yellow deal 2026-09-30: 25 % of the trading fees our users generate)."""
from __future__ import annotations
import os
import sqlite3
import threading
import time
from decimal import Decimal
from typing import Dict, Optional

ZERO = Decimal("0")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or "0"))
    except Exception:
        return ZERO


def empty() -> dict:
    return dict(closed=0, wins=0, losses=0, realized_pnl=ZERO, fees=ZERO, volume=ZERO, open_positions=0, last_trade_ts=None)


def position_results(path: str) -> list:
    """Join entries through entry_coid; a closed position's remaining qty is zero."""
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    con.row_factory = sqlite3.Row
    try:
        positions = [dict(r) for r in con.execute("select * from positions")]
        orders = con.execute("select p.position_id as pid, o.* from positions p join orders o "
                             "on o.position_id=p.position_id or (o.purpose='ENTRY' and o.client_order_id=p.entry_coid)").fetchall()
        agg = {}
        for o in orders:
            a = agg.setdefault(o['pid'], dict(entry_qty=ZERO, entry_val=ZERO, exit_qty=ZERO, exit_val=ZERO, fees=ZERO, volume=ZERO))
            fills = con.execute("select qty, price, fee from fills where client_order_id=?", (o['client_order_id'],)).fetchall()
            fq, ap = _d(o['filled_qty']), _d(o['avg_price'])
            a['fees'] += max(_d(o['fees']), sum((_d(f['fee']) for f in fills), ZERO))
            a['volume'] += max(fq * ap, sum((_d(f['qty']) * _d(f['price']) for f in fills), ZERO))
            if fq > 0:
                kind = 'entry' if o['purpose'] == 'ENTRY' else 'exit'
                a[kind+'_qty'] += fq
                a[kind+'_val'] += fq * ap
        import json
        try:
            with open(path + '.history.json') as f:
                history = json.load(f)
        except (OSError, ValueError):
            history = {}
        out = []
        for p in positions:
            a = agg.get(p['position_id'], {})
            eq, xq = a.get('entry_qty', ZERO), a.get('exit_qty', ZERO)
            fees, volume = a.get('fees', ZERO), a.get('volume', ZERO)
            pnl = exit_avg = None
            funding = ZERO
            source = 'fills'
            reason = p['reason']
            closed_ts = p['state_ts']
            if eq > 0 and xq > 0 and p['state'] == 'CLOSED':
                exit_avg = a['exit_val'] / xq
                sign = Decimal(1) if p['direction'].lower() in ('long', 'buy') else Decimal(-1)
                pnl = sign * (exit_avg - a['entry_val'] / eq) * xq - fees
            h = history.get(p['position_id'])
            if h and p['state'] == 'CLOSED':
                fees = _d(h['total_trading_fee'])
                funding = _d(h['net_funding_fee'])
                pnl = _d(h['realized_pnl']) - fees + funding
                exit_avg = _d(h['exit_price']) or None
                source = 'yellow_history'
                reason = h.get('close_reason') or reason
                if h.get('closed_at'):
                    from datetime import datetime
                    closed_ts = datetime.fromisoformat(h['closed_at'].replace('Z', '+00:00')).timestamp()
            out.append(dict(position_id=p['position_id'], market=p['market'], direction=p['direction'], strategy=p['strategy'],
                            state=p['state'], qty=str(eq or _d(p['qty'])), entry_price=p['entry_price'],
                            exit_price=str(exit_avg) if exit_avg is not None else None,
                            pnl=float(pnl) if pnl is not None else None, fees=float(fees), funding=float(funding),
                            pnl_source=source, pnl_pending=p['state']=='CLOSED' and pnl is None,
                            reason=reason, opened_ts=p['opened_ts'], closed_ts=closed_ts, volume=float(volume)))
        return out
    finally:
        con.close()


def read_ledger(path: str) -> dict:
    return summarize_positions(position_results(path))


def summarize_positions(rows: list) -> dict:
    """Aggregate one consistent results snapshot for every reporting surface."""
    out = empty()
    for p in rows:
        out['fees'] += _d(p['fees'])
        out['volume'] += _d(p['volume'])
        if p['state'] != 'CLOSED':
            out['open_positions'] += 1
            continue
        out['closed'] += 1
        ts = p['closed_ts']
        if ts and (out['last_trade_ts'] is None or ts > out['last_trade_ts']):
            out['last_trade_ts'] = ts
        if p['pnl'] is not None:
            pnl = _d(p['pnl'])
            out['realized_pnl'] += pnl
            if pnl != 0:
                out['wins' if pnl > 0 else 'losses'] += 1
    return out


class PnlCache:
    """account_id -> results, refreshed when the ledger file changes (mtime) or every ttl seconds."""

    def __init__(self, tenants_dir: str, ttl_s: float = 30.0):
        self.dir, self.ttl = tenants_dir, ttl_s
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def path(self, account_id: str) -> str:
        return os.path.join(self.dir, str(account_id), "ledger.sqlite")

    def get(self, account_id: str) -> dict:
        p = self.path(account_id)
        try:
            mt = tuple(os.stat(f).st_mtime_ns if os.path.exists(f) else 0
                       for f in (p, p + "-wal", p + ".history.json"))
        except FileNotFoundError:
            return empty()
        now = time.time()
        with self._lock:
            hit = self._cache.get(account_id)
            if hit and hit[0] == mt and now - hit[1] < self.ttl:
                return hit[2]
        try:
            res = read_ledger(p)
        except Exception:
            res = empty()
        with self._lock:
            self._cache[account_id] = (mt, now, res)
        return res

    def all(self) -> Dict[str, dict]:
        out = {}
        if not os.path.isdir(self.dir):
            return out
        for name in os.listdir(self.dir):
            if os.path.exists(self.path(name)):
                out[name] = self.get(name)
        return out


def to_public(r: dict) -> dict:
    f = lambda v: float(v) if isinstance(v, Decimal) else v
    return dict(closed_trades=r["closed"], wins=r["wins"], losses=r["losses"], realized_pnl=f(r["realized_pnl"]), fees=f(r["fees"]),
                volume=f(r["volume"]), open_positions=r["open_positions"], last_trade_at=r["last_trade_ts"])


def recent_closed(path: str, limit: int = 20) -> list:
    rows = [p for p in position_results(path) if p['state'] == 'CLOSED']
    return sorted(rows, key=lambda p: p['closed_ts'] or 0, reverse=True)[:max(0, int(limit))]
