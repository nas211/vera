"""Read-only view of one customer's WHY trading results for the profile page.

Reuses the affiliate dashboard's ledger reader (affiliate.pnl): same tenant SQLite ledger,
same realized-P&L arithmetic, opened read-only. Optional exchange history is cached
separately; execution records are never modified."""
from __future__ import annotations
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from affiliate.pnl import PnlCache, empty, position_results, summarize_positions, to_public


def today_results(rows, now=None, available=True):
    """Closed-position results for the labeled Berlin calendar day, before list limits."""
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, ZoneInfo('Europe/Berlin'))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    closed = [p for p in rows if p['state'] == 'CLOSED'
              and p.get('closed_ts') is not None
              and start.timestamp() <= float(p['closed_ts']) < min(end.timestamp(), now + .001)]
    known = [p for p in closed if p['pnl'] is not None]
    values = [Decimal(str(p['pnl'])) for p in known]
    won = sum((v for v in values if v > 0), Decimal(0))
    lost = -sum((v for v in values if v < 0), Decimal(0))
    return dict(date=start.date().isoformat(), timezone='Europe/Berlin', available=available,
                start_at=start.timestamp(), end_at=end.timestamp(), closed_trades=len(closed),
                wins=sum(v > 0 for v in values), losses=sum(v < 0 for v in values),
                breakeven=sum(v == 0 for v in values), pending_pnl=len(closed)-len(known),
                funding_pending=sum(p.get('pnl_source') != 'yellow_history' for p in known),
                won_usd=float(won) if available else None,
                lost_usd=float(lost) if available else None,
                net_pnl=float(won-lost) if available else None)


class ResultsReader:
    def __init__(self, tenants_dir: str, history_client=None, clock=time.time):
        self.dir = tenants_dir
        self.clock = clock
        self.cache = PnlCache(tenants_dir)
        from .result_history import HistorySync
        self.history = HistorySync(history_client) if history_client else None

    def for_account(self, account_id: str, limit: int = 20) -> dict:
        aid = str(account_id)
        if self.history:
            self.history.schedule(aid, self.cache.path(aid))
        try:
            rows = position_results(self.cache.path(aid))
            summary = to_public(summarize_positions(rows))
            summary['pending_pnl'] = sum(p['state'] == 'CLOSED' and p['pnl'] is None for p in rows)
            if summary['pending_pnl']:
                summary['realized_pnl'] = None
            today = today_results(rows, self.clock())
            trades = sorted((p for p in rows if p['state'] == 'CLOSED'),
                            key=lambda p: p['closed_ts'] or 0, reverse=True)[:max(0, int(limit))]
        except Exception:
            summary = to_public(empty())
            summary['realized_pnl'] = None
            today = today_results([], self.clock(), available=False)
            trades = []
        return dict(summary=summary, today=today, trades=trades)
