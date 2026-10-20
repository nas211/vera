"""Live account sizing variant of the original runner's order/grid methods.

Price levels, cadence, cancellation and exit creation are retained. Only capital
limits use the current private account snapshot instead of the $1,000 paper seed.
Original vendored strategy files remain hash-verified and unchanged.
"""
import math


class AccountSizedRunner:
    def place(self,m,buy,p,q,lot=None):
        top=self.top(m)
        if not top or q<=0:return None
        p=self.rounded(m,p,buy)
        if (buy and p>=top[1]) or (not buy and p<=top[0]):
            self.stats['post_only_rejected']+=1;return None
        if not lot:
            f=self.filters[m];q=math.floor(q/f['step']+1e-9)*f['step']
            if q<f['min_qty'] or p*q<f['min_notional']:
                self.blocked['below_exchange_minimum']+=1
                return None
            # Propose across every market before the runtime fairly allocates
            # actual available funds. Never reserve money for unsent proposals.
            if self.exposure(m)+q*p>float(self.budget['per_market_notional_cap_usd']):return None
        self.next_id+=1;oid=str(self.next_id)
        self.orders[oid]=dict(id=oid,market=m,buy=buy,price=p,qty=q,lot=lot,
                             requested=self.clock(),active_at=self.clock()+.25,active=False,queue=0.,cancel_at=None)
        self.stats['placed']+=1
        self.event('POST_ONLY_SUBMIT',order=self.orders[oid].copy())
        return oid

    def maintain(self):
        if self.finish_if_limit():return
        now=self.clock()
        for oid,o in list(self.orders.items()):
            if o['cancel_at'] is not None and now>=o['cancel_at']:
                self.orders.pop(oid,None);self.stats['cancelled']+=1;continue
            if not o['active'] and now>=o['active_at']:
                t=self.top(o['market'])
                if not t:continue
                if (o['buy'] and o['price']>=t[1]) or (not o['buy'] and o['price']<=t[0]):
                    self.orders.pop(oid,None);self.stats['post_only_rejected']+=1;continue
                side='bids' if o['buy'] else 'asks'
                o['queue']=self.books[o['market']][side].get(o['price'],0.)
                o['active']=True
        cap=float(self.budget['gross_notional_cap_usd'])
        # Falling equity withdraws pending entry reservations before restricting
        # inventory. Never manufacture a market exit for a maker-only test.
        pending=sum(o['qty']*o['price'] for o in self.orders.values() if not o['lot'])
        capital_exhausted=self.exposure()-pending>cap
        projected=self.exposure()-sum(o['qty']*o['price'] for o in self.orders.values() if not o['lot'] and o['cancel_at'] is not None)
        for o in sorted(self.orders.values(),key=lambda o:o['qty']*o['price'],reverse=True):
            if not o['lot'] and o['cancel_at'] is None and (self.halted or capital_exhausted or projected>cap):
                o['cancel_at']=now+.25;projected-=o['qty']*o['price']
        for m in self.strategy_markets:
            if not self.top(m) or not self.connected:continue
            if now-self.books[m]['received']>15:
                for o in self.orders.values():
                    if o['market']==m and not o['lot'] and o['cancel_at'] is None:o['cancel_at']=now+.25
                continue
            # Keep each partial fill as an inventory lot with a reduce-only exit.
            for lid,l in list(self.lots.items()):
                if l['market']!=m or l.get('exit_id') in self.orders:continue
                bid,ask=self.top(m);buy=not l['buy']
                target=l['entry']*(1+(-1 if buy else 1)*self.strategy_config['target_bps']/10000)
                price=min(target,bid) if buy else max(target,ask)
                l['exit_id']=self.place(m,buy,price,l['qty'],lid)
            if self.halted or capital_exhausted:continue
            if now-self.last_quote.get(m,-1e10)>=self.strategy_config['refresh_seconds']:
                old=[o for o in self.orders.values() if o['market']==m and not o['lot']]
                if old:
                    for o in old:
                        if o['cancel_at'] is None:o['cancel_at']=now+.25
                    continue
                bid,ask=self.top(m);mid=(bid+ask)/2;seen=set()
                tick=self.filters[m]['tick'];spacing=max(tick,mid*self.strategy_config['entry_spacing_bps']/10000)
                f=self.filters[m]
                available=float(self.budget['per_market_notional_cap_usd'])-self.exposure(m)
                # Distribute the available market allocation over as many valid
                # two-sided levels as its lot and notional minimums allow.
                max_price=ask+(self.strategy_config['levels_per_side']-1)*spacing
                min_qty=max(f['min_qty'],math.ceil(f['min_notional']/(bid*f['step']))*f['step'])
                slots=min(2*self.strategy_config['levels_per_side'],max(0,int(available/(max_price*min_qty))))
                # Interleave bid/ask so restricted capacity remains balanced.
                candidates=[]
                for level in range(self.strategy_config['levels_per_side']):
                    for buy,base in [(True,bid),(False,ask)]:
                        price=self.rounded(m,base+(-1 if buy else 1)*level*spacing,buy)
                        if (buy,price) not in seen:
                            seen.add((buy,price));candidates.append((buy,price))
                for i,(buy,price) in enumerate(candidates[:slots]):
                    available=float(self.budget['per_market_notional_cap_usd'])-self.exposure(m)
                    qty=available/(slots-i)/price
                    self.place(m,buy,price,qty)
                self.last_quote[m]=now
