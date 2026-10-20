"""Per-coin loss cooldown; overlapping entries remain strategy-owned."""
import json
from decimal import Decimal as D

ACCOUNTS = frozenset({'yellow:8f3efa87-f7c0-4c21-ba1e-46b207613f96'})
TERMINAL = {'filled','cancelled','rejected'}
LOSS_COOLDOWN_SECONDS = 15


class CoinEntryPolicy:
    def __init__(self, runtime):
        self.rt=runtime
        self.enabled=runtime.ctx.namespace in ACCOUNTS
        runtime.model.coin_entry_policy=self.enabled
        self.refresh()

    def refresh(self):
        rt=self.rt
        if not self.enabled:return
        parents={r['payload']['id']:r for r in rt.store.orders() if not r['payload'].get('lot')}
        totals={}
        # Replay immutable private fills so partial exits, fees and restarts all
        # use the same completed-parent result. No public/simulated fills count.
        for row in rt.store.db.execute('select payload from fills'):
            f=json.loads(row[0]);ctx=f.get('context',{});order=ctx.get('order',{});lot=ctx.get('lot')
            parent=lot.get('entry_order') if lot else order.get('id')
            if parent not in parents:continue
            t=totals.setdefault(parent,dict(entry=D(0),exit=D(0),net=D(0),closed=0.))
            qty=D(f['qty']);t['net']-=D(f['fee'])
            if lot:
                t['exit']+=qty
                t['net']+=(D(f['price'])-D(lot['entry']))*qty*(1 if lot['buy'] else -1)
                stamp=float(f['ts']);stamp=stamp/1000 if stamp>1e11 else stamp
                t['closed']=max(t['closed'],stamp)
            else:t['entry']+=qty
        cooldowns={}
        for parent,t in totals.items():
            row=parents[parent]
            if row['state'] in TERMINAL and t['entry']>0 and t['entry']==t['exit'] and t['net']<0:
                market=row['payload']['market'];until=t['closed']+LOSS_COOLDOWN_SECONDS
                if until>rt.clock():cooldowns[market]=max(cooldowns.get(market,0),until)
        rt.model.entry_cooldowns=cooldowns

    def block_reason(self, market):
        if not self.enabled:return None
        rt=self.rt
        if rt.clock()<rt.model.entry_cooldowns.get(market,0):return 'coin_loss_cooldown'
        return None

    def excess_entries(self):
        if not self.enabled:return set()
        rt=self.rt;excess=set()
        for row in rt.store.active_orders():
            p=row['payload'];market=p['market']
            if row['state'] in TERMINAL or p.get('lot'):continue
            if rt.clock()<rt.model.entry_cooldowns.get(market,0):
                excess.add(p['id'])
        return excess
