"""Read-only exchange evidence for results; never modifies execution records."""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

from affiliate.pnl import position_results


def same_entry_price(left, right):
    """Allow only serialization precision loss, not a different execution price."""
    left, right = Decimal(str(left)), Decimal(str(right))
    return (left.is_finite() and right.is_finite() and min(left, right) > 0
            and abs(left-right) <= max(left, right) * Decimal('1e-16'))


def owned_entries(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {r['position_id']: dict(r) for r in db.execute(
            "select p.position_id,o.client_order_id,o.venue_order_id,o.created_ts "
            "from positions p join orders o on o.client_order_id=p.entry_coid "
            "and o.account_id=p.account_id and o.purpose='ENTRY'")}


def with_exchange_entry_times(rows, entries, trades):
    """Use timestamps only from fills belonging to the position's owned entry."""
    corrected = []
    for row in rows:
        entry = entries.get(row['position_id'])
        times = []
        if entry:
            for trade in trades.get(row['market'], []):
                if (trade.get('market') != row['market']
                        or trade.get('client_order_id') != entry['client_order_id']
                        or (trade.get('order_uuid') and entry.get('venue_order_id')
                            and trade['order_uuid'] != entry['venue_order_id'])):
                    continue
                try:
                    times.append(datetime.fromisoformat(trade['executed_at'].replace('Z', '+00:00')).timestamp())
                except (KeyError, ValueError, TypeError):
                    continue
        corrected.append(dict(row, opened_ts=min(times)) if times else row)
    return corrected


def match_history(rows, history):
    """Require a unique entry identity; never guess from market alone."""
    matches = {}
    for h in history:
        try:
            opened = datetime.fromisoformat(h['opened_at'].replace('Z', '+00:00')).timestamp()
            if not h.get('closed_at') or not h.get('id'):
                continue
            for key in ('realized_pnl', 'total_trading_fee', 'net_funding_fee', 'exit_price'):
                if not Decimal(h[key]).is_finite():
                    raise ValueError('nonfinite history')
            candidates = [p for p in rows if p['state'] == 'CLOSED' and p['market'] == h['market']
                          and p['direction'].lower() == h['direction'].lower()
                          and same_entry_price(p['entry_price'], h['entry_price'])
                          and Decimal(p['qty']) == Decimal(h['max_held'])
                          and abs(p['opened_ts'] - opened) <= 30]
            if len(candidates) == 1:
                matches.setdefault(candidates[0]['position_id'], []).append(h)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            continue
    return {pid: items[0] for pid, items in matches.items() if len(items) == 1}


class HistorySync:
    def __init__(self, client_for_account):
        self.client_for_account = client_for_account
        self.lock = threading.Lock()
        self.running = set()
        self.last = {}

    def schedule(self, aid, path):
        with self.lock:
            if aid in self.running or time.monotonic() - self.last.get(aid, -1000) < 60:
                return
            self.running.add(aid)
            self.last[aid] = time.monotonic()
        threading.Thread(target=self._run, args=(aid, path), daemon=True, name='result-history').start()

    def _run(self, aid, path):
        try:
            self.sync(aid, path)
        except Exception:
            # Keep confirmed fill results/cached evidence when exchange is unavailable.
            pass
        finally:
            with self.lock:
                self.running.discard(aid)

    def sync(self, aid, path):
        rows = position_results(path)
        missing = [p for p in rows if p['state'] == 'CLOSED' and p['pnl_source'] != 'yellow_history']
        if not missing:
            return
        client = self.client_for_account(aid)
        if client is None:
            return
        entries = owned_entries(path)
        evidence = []
        for market in sorted({p['market'] for p in missing}):
            # The ledger position can be created late during reconciliation.
            # Start at the owned entry intent, before the exchange execution.
            since = min(min(p['opened_ts'], entries.get(p['position_id'], {}).get('created_ts', p['opened_ts']))
                        for p in rows if p['market'] == market) - 30
            params = dict(app_session_id=client.asid, market=market, use_cursor='true', page_size=1,
                          opened_from=datetime.fromtimestamp(since, timezone.utc).isoformat())
            seen = set()
            for _ in range(100):
                data = client._req('GET', '/perpetual/position-history', params, tries=1)
                evidence.extend(data.get('positions', []))
                if not data.get('has_more'):
                    break
                cursor = data.get('next_cursor')
                if not cursor or cursor in seen:
                    raise ValueError('invalid history pagination')
                seen.add(cursor)
                params['cursor'] = cursor
            else:
                raise ValueError('history pagination limit')
        matched = match_history(rows, evidence)
        unmatched_markets = {p['market'] for p in missing if p['position_id'] not in matched}
        if unmatched_markets:
            trades = {market: client.trades(market) for market in sorted(unmatched_markets)}
            corrected = with_exchange_entry_times(rows, entries, trades)
            # Re-match against every local position to retain one-to-one identity.
            matched = match_history(corrected, evidence)
        if not matched:
            return
        try:
            with open(path + '.history.json') as f:
                saved = json.load(f)
        except FileNotFoundError:
            saved = {}
        saved.update(matched)
        tmp = path + '.history.json.tmp'
        with open(tmp, 'w') as f:
            json.dump(saved, f)
        os.replace(tmp, path + '.history.json')
