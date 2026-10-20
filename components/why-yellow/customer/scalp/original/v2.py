"""Paper-only revision: adverse-flow entry gates and passive inventory exits.
Parameter values are hypotheses for forward testing, not fitted profit promises.
"""
import collections,json,time,math
import runner
from runner import Paper,CONFIG,ROOT

EXCLUDED_ENTRIES={'TAOUSDT-PERP','HYPEUSDT-PERP','SUIUSDT-PERP'}
ACTIVE_MARKETS=[m for m in runner.MARKETS if m not in EXCLUDED_ENTRIES]

RULES=dict(warmup_seconds=30,trend_window_seconds=30,adverse_trend_bps=2,
           imbalance_threshold=.35,side_notional_limit=500,
           decay_target_after_seconds=30,max_hold_seconds=90,
           adverse_exit_bps=5,exit_refresh_seconds=5,entry_refresh_seconds=15)

class PaperV2(Paper):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.history=collections.defaultdict(collections.deque)
        self.blocked=collections.Counter()
        self.starting_review=None
        p=ROOT/'state/v2-baseline.json'
        if self.record and p.exists():self.starting_review=json.loads(p.read_text())
    def signal(self,m):
        top=self.top(m)
        if not top:return dict(ready=False,trend_bps=0,imbalance=0)
        now=self.clock();h=self.history[m]
        ready=bool(h and now-h[0][0]>=RULES['warmup_seconds'])
        previous=next((px for ts,px in reversed(h) if ts<=now-RULES['trend_window_seconds']),h[0][1] if h else sum(top)/2)
        trend=((sum(top)/2)/previous-1)*10000
        b=self.books[m]
        bids=sum(q for _,q in sorted(b['bids'].items(),reverse=True)[:5])
        asks=sum(q for _,q in sorted(b['asks'].items())[:5])
        imbalance=(bids-asks)/(bids+asks) if bids+asks else 0
        return dict(ready=ready,trend_bps=trend,imbalance=imbalance)
    def entry_reason(self,m,buy):
        if m in EXCLUDED_ENTRIES:return 'market_removed'
        s=self.signal(m)
        if not s['ready']:return 'warmup'
        if self.clock()-self.books[m]['received']>15:return 'stale_book'
        if (buy and s['trend_bps']<=-RULES['adverse_trend_bps']) or (not buy and s['trend_bps']>=RULES['adverse_trend_bps']):return 'adverse_trend'
        if (buy and s['imbalance']<=-RULES['imbalance_threshold']) or (not buy and s['imbalance']>=RULES['imbalance_threshold']):return 'adverse_orderbook'
        return None
    def side_committed(self,m,buy):
        top=self.top(m);mid=sum(top)/2
        filled=sum(l['qty']*max(l['entry'],mid) for l in self.lots.values() if l['market']==m and l['buy']==buy)
        pending=sum(o['qty']*o['price'] for o in self.orders.values() if o['market']==m and o['buy']==buy and not o['lot'])
        return filled+pending
    def exit_price(self,l):
        bid,ask=self.top(l['market']);mid=(bid+ask)/2
        signed=(mid/l['entry']-1)*10000*(1 if l['buy'] else -1)
        age=self.clock()-l['opened']
        urgent=age>=RULES['max_hold_seconds'] or signed<=-RULES['adverse_exit_bps']
        if urgent:return (ask if l['buy'] else bid),'passive_loss_or_time_exit'
        decay=RULES.get('decay_target_after_seconds')
        bps=0 if decay is not None and age>=decay else CONFIG['target_bps']
        target=l['entry']*(1+(1 if l['buy'] else -1)*bps/10000)
        return (max(target,ask) if l['buy'] else min(target,bid)),'target'
    def occupied_entry_slots(self):
        # A partially filled parent entry and its remaining quote count once.
        parents={l.get('entry_order',lid) for lid,l in self.lots.items()}
        parents.update(o['id'] for o in self.orders.values() if not o['lot'])
        return len(parents)
    def place(self,m,buy,p,q,lot=None):
        if lot:
            p,reason=self.exit_price(self.lots[lot])
        else:
            reason=self.entry_reason(m,buy)
            if reason:self.blocked[reason]+=1;return None
            room=RULES['side_notional_limit']-self.side_committed(m,buy)
            if CONFIG.get('entry_margin_usd') is not None:
                if self.occupied_entry_slots()>=CONFIG['max_entry_slots']:
                    self.blocked['entry_slot_limit']+=1;return None
                q=CONFIG['entry_margin_usd']*CONFIG['leverage']/p
                rounded_qty=math.floor(q/self.filters[m]['step']+1e-9)*self.filters[m]['step']
                if rounded_qty*p>room:
                    self.blocked['side_inventory_limit']+=1;return None
            else:q=min(q,max(0,room)/p)
            if q<=0:self.blocked['side_inventory_limit']+=1;return None
        oid=super().place(m,buy,p,q,lot)
        if oid and lot:self.orders[oid]['exit_reason']=reason
        return oid
    def maintain(self):
        if self.finish_if_limit():return
        now=self.clock()
        for o in list(self.orders.values()):
            if o['cancel_at'] is not None:continue
            if not o['lot']:
                reason=self.entry_reason(o['market'],o['buy'])
                if reason:o['cancel_at']=now+.25
            elif self.top(o['market']) and now-self.books[o['market']]['received']<=15:
                l=self.lots.get(o['lot'])
                if not l:continue
                price,reason=self.exit_price(l);price=self.rounded(o['market'],price,o['buy'])
                # Preserve a resting profit quote when the market moves through it.
                # Moving a long exit up (or short exit down) chases profit away
                # and discards queue priority. Loss/time repricing stays enabled.
                moving_away=(price>o['price'] if l['buy'] else price<o['price'])
                if reason=='target' and moving_away:continue
                if now-o['requested']>=RULES['exit_refresh_seconds'] and abs(price-o['price'])>=self.filters[o['market']]['tick']*.99:
                    o['cancel_at']=now+.25
        super().maintain()
    def receive(self,d):
        # Base logic validates sequence before the observation is used by gates.
        super().receive(d)
        if self.finished_at is not None:return
        m=d.get('market');typ=d.get('header',{}).get('type','')
        if typ.startswith('orderbook.') and m in self.books:
            top=self.top(m)
            if top:
                h=self.history[m];now=self.clock();mid=sum(top)/2
                if not h or now-h[-1][0]>=.1:h.append((now,mid))
                while len(h)>1 and h[1][0]<now-60:h.popleft()
                if self.record and (not hasattr(self,'last_mark_log') or now-self.last_mark_log.get(m,0)>=5):
                    if not hasattr(self,'last_mark_log'):self.last_mark_log={}
                    self.event('MARK_SAMPLE',market=m,mid=mid,signal=self.signal(m));self.last_mark_log[m]=now
        # Changes in flow immediately request simulated cancellation; fills can
        # still arrive during the modeled 250ms cancellation delay.
    def disconnect(self):
        super().disconnect();self.history.clear()
    def snapshot(self):
        d=super().snapshot();d['strategy']='V2: flow-filtered grid with passive inventory exits';d['revision']=CONFIG.get('strategy_version',2);d['rules']=RULES
        d['entry_filters']=dict(self.blocked)
        d['occupied_entry_slots']=self.occupied_entry_slots()
        d['active_entry_markets']=ACTIVE_MARKETS
        d['exit_only_markets']=sorted(EXCLUDED_ENTRIES)
        for m,row in d['markets'].items():row['exit_only']=m in EXCLUDED_ENTRIES
        if self.starting_review:
            b=self.starting_review
            d['revision_baseline']=b
            d['revision_completed_trades']=self.stats['closed_lots']-b['completed_trades']
            d['revision_total_pnl']=d['equity_before_funding']-b['equity_before_funding']
        d['limitations'].append('Completed trades count fully closed fill-lots, not statistically independent entry orders.')
        return d

if __name__=='__main__':
    baseline=json.loads((ROOT/'state/v2-baseline.json').read_text())
    # Subscribe only to active coins, plus excluded coins with carryover lots.
    checkpoint=ROOT/'state/checkpoint.json'
    carry=json.loads(checkpoint.read_text()).get('lots',{}) if checkpoint.exists() else {}
    exit_markets={lot['market'] for lot in carry.values() if lot['market'] in EXCLUDED_ENTRIES}
    runner.MARKETS=ACTIVE_MARKETS+sorted(exit_markets)
    CONFIG.update(strategy_version=2,levels_per_side=4,entry_margin_usd=20,max_entry_slots=50,sizing="fixed_margin",stop_after_completed_trades=baseline['completed_trades']+1000,
                  refresh_seconds=RULES['entry_refresh_seconds'],per_market_cap=1600)
    RULES['side_notional_limit']=CONFIG['per_market_cap']/2
    runner.main(PaperV2)
