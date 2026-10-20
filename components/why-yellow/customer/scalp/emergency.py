"""Universal $2 emergency close, retaining an existing stricter $1 rule.

This is a software guard, NOT verified exchange-held protection. Its use in
place of that requirement is restricted to explicitly authorized accounts.
"""
from .policy import software_emergency_authorized
from execution.types import D
from execution.loss_stop import MAX_LOSS_USD
from .strategy import CONFIG

EMERGENCY_LOSS_USD = D('1')
# A live perp book unchanged this long suggests a dead subscription.
QUIET_BOOK_SECONDS = 120
TERMINAL = {'filled', 'cancelled', 'rejected'}


class EmergencyStop:
    def __init__(self, runtime):
        self.rt = runtime
        # Cover existing lots for every account, even while live scalp entry is
        # disabled. Preserve the original account's stricter $1 close threshold.
        self.enabled = True
        self.loss_limit = min(EMERGENCY_LOSS_USD, MAX_LOSS_USD) if software_emergency_authorized(runtime.ctx.namespace) else MAX_LOSS_USD
        db = runtime.store.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            initialized = db.execute("select 1 from sqlite_master where type='table' and name='emergency_pnl'").fetchone()
            db.execute('create table if not exists emergency_pnl(entry_id text primary key, net text not null)')
            db.execute('create table if not exists emergency_stops(entry_id text primary key, triggered_at real not null, loss_usd text not null)')
            if not initialized:
                # Existing confirmed history is backfilled once; subsequent
                # updates share the transaction that deduplicates each fill.
                from types import SimpleNamespace
                import json
                for row in db.execute('select payload from fills order by rowid'):
                    fill = json.loads(row[0])
                    self.record_fill(SimpleNamespace(**fill), fill.get('context', {}))

    def record_fill(self, trade, context):
        if not self.enabled:
            return
        order = context.get('order', {})
        lot = context.get('lot')
        parent = lot.get('entry_order') if lot else order.get('id')
        if not parent:
            raise ValueError('emergency_entry_identity_missing')
        net = -D(trade.fee)
        if lot:
            net += (D(trade.price)-D(lot['entry']))*D(trade.qty)*(1 if lot['buy'] else -1)
        old = self.rt.store.db.execute('select net from emergency_pnl where entry_id=?', (parent,)).fetchone()
        net += D(old[0]) if old else D(0)
        self.rt.store.db.execute('insert into emergency_pnl values(?,?) on conflict(entry_id) do update set net=excluded.net', (parent,str(net)))

    def triggered(self):
        if not self.enabled:
            return set()
        return {r[0] for r in self.rt.store.db.execute('select entry_id from emergency_stops')}

    def parent(self, row):
        p = row['payload']
        return (p.get('entry_order') or self.rt.model.lots.get(p.get('lot'), {}).get('entry_order')) if p.get('lot') else p['id']

    def blocks_lot(self, lid):
        return self.rt.model.lots.get(lid, {}).get('entry_order') in self.triggered()

    @property
    def engaged(self):
        triggered = self.triggered()
        return (any(l.get('entry_order') in triggered for l in self.rt.model.lots.values())
                or any(r['state'] not in TERMINAL and self.parent(r) in triggered for r in self.rt.store.active_orders()))

    def check(self):
        if not self.enabled:
            return
        rt = self.rt
        groups = {}
        for lot in rt.model.lots.values():
            groups.setdefault(lot['entry_order'], []).append(lot)
        already = self.triggered()
        for parent, lots in groups.items():
            if parent in already:
                continue
            row = rt.store.db.execute('select net from emergency_pnl where entry_id=?', (parent,)).fetchone()
            net = D(row[0]) if row else D(0)
            valid = True
            # Yellow sends book increments only on change, so a quiet coin can
            # go 15-20s without one. Sequence gaps reset the feed, so a quiet
            # book is still current while the socket keeps delivering updates.
            now = rt.clock()
            feed = max((b.get('received', 0) for b in rt.model.books.values()), default=0)
            for lot in lots:
                book = rt.model.books.get(lot['market'], {})
                top = rt.model.top(lot['market'])
                if not top or not rt.model.connected or now-feed>15 or now-book.get('received', 0)>QUIET_BOOK_SECONDS:
                    valid = False
                    rt.last_error = 'scalp_emergency_market_data_unavailable'
                    break
                px = D(top[0] if lot['buy'] else top[1])
                qty = D(lot['qty'])
                fee = max(D(CONFIG['taker_fee']), rt.metadata.filters(lot['market']).taker_fee)
                net += (px-D(lot['entry']))*qty*(1 if lot['buy'] else -1)-px*qty*fee
            if valid and net <= -self.loss_limit:
                with rt.store.db:
                    rt.store.db.execute('insert or ignore into emergency_stops values(?,?,?)', (parent,rt.clock(),str(-net)))

    def process(self):
        if not self.enabled or not self.rt.ready:
            return
        rt = self.rt
        for parent in self.triggered():
            # Release parent entry and all normal exit reservations first.
            # Cancellation re-reads private fills; use only the remaining lots.
            for row in rt.store.active_orders():
                if row['state'] not in TERMINAL and self.parent(row)==parent and row['payload'].get('reason')!='EMERGENCY_STOP':
                    rt.cancel(row)
            if not rt.ready:
                return
            if any(r['state'] not in TERMINAL and self.parent(r)==parent and r['payload'].get('reason')!='EMERGENCY_STOP' for r in rt.store.active_orders()):
                rt.last_error = 'scalp_emergency_cancellation_pending'
                continue
            for lid in list(rt.model.lots):
                lot = rt.model.lots.get(lid)
                if not lot or lot.get('entry_order')!=parent:
                    continue
                attempts = [r for r in rt.store.orders() if r['payload'].get('lot')==lid and r['payload'].get('reason')=='EMERGENCY_STOP']
                if any(r['state'] not in TERMINAL for r in attempts):
                    continue  # includes unknown submissions; never blindly retry
                if len(attempts)>=3:
                    rt.last_error = 'scalp_emergency_close_requires_attention'
                    continue
                rt.model.next_id += 1
                top = rt.model.top(lot['market'])
                price = (top[0] if lot['buy'] else top[1]) if top else lot['entry']
                payload = dict(kind='market',id=str(rt.model.next_id),market=lot['market'],buy=not lot['buy'],
                               price=price,qty=lot['qty'],lot=lid,requested=rt.clock(),reason='EMERGENCY_STOP')
                try:
                    rt.submit(payload)
                except (ValueError, ArithmeticError):
                    rt.last_error = 'scalp_emergency_close_validation_failed'
                if not rt.ready:
                    return
