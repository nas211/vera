"""PAPER ONLY: retain maker exits, capture executable net targets with limit IOC.
No private API, credentials, or live order transport. Fees and latency are modeled.
"""
import runner, maker_only, v2
from runner import CONFIG

class ProfitCapturePaper(maker_only.MakerOnlyPaper):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.profit_requests={};self.used_depth={}
    def place(self,m,buy,p,q,lot=None):
        if lot in self.profit_requests:return None
        return super().place(m,buy,p,q,lot)
    def profitable(self,lot,price):
        net_per_unit=(price-lot['entry'])*(1 if lot['buy'] else -1)-price*CONFIG['taker_fee']
        return net_per_unit/lot['entry']+1e-12>=CONFIG['target_bps']/10000
    def available_levels(self,lot):
        m=lot['market'];side='bids' if lot['buy'] else 'asks'
        for price,displayed in sorted(self.books[m][side].items(),reverse=lot['buy']):
            if not self.profitable(lot,price):break
            available=max(0.,displayed-self.used_depth.get((m,side,price),0.))
            if available>1e-12:yield price,available,(m,side,price)
    def request_profit(self,lid):
        lot=self.lots[lid]
        ready=self.clock()+CONFIG['latency_ms']/1000
        # Existing maker fills remain valid until cancellation takes effect.
        for o in self.orders.values():
            if o['lot']==lid:o['cancel_at']=min(o['cancel_at'],ready) if o['cancel_at'] is not None else ready
        self.profit_requests[lid]=ready
        self.event('NET_TARGET_REQUEST',market=lot['market'],lot=lid,ready_at=ready,target_bps=CONFIG['target_bps'])
    def execute_profit(self,lid):
        lot=self.lots.get(lid)
        if not lot:return
        qty=0.;value=0.;gross=0.;fills=[]
        # This is a profit-protected limit IOC, never an unbounded market order.
        # Recheck every level after latency and only consume observed liquidity.
        for price,available,key in self.available_levels(lot):
            take=min(lot['qty'],available)
            if take<=1e-12:break
            self.used_depth[key]=self.used_depth.get(key,0.)+take
            lot['qty']-=take;qty+=take;value+=take*price
            gross+=(price-lot['entry'])*take*(1 if lot['buy'] else -1)
            fills.append(dict(price=price,qty=take))
        if qty:
            fee=value*CONFIG['taker_fee'];net=gross-fee;closed=lot['qty']<1e-10
            self.stats['realized_pnl']+=net;self.stats['fees']+=fee;self.stats['exit_fills']+=1
            self.stats['taker_exit_fills']=self.stats.get('taker_exit_fills',0)+1
            self.stats['net_target_ioc_fills']=self.stats.get('net_target_ioc_fills',0)+1
            self.event('TAKER_EXIT_FILL',market=lot['market'],lot=lid,entry_order=lot.get('entry_order'),
                       entry_price=lot['entry'],side='long' if lot['buy'] else 'short',
                       price=value/qty,qty=qty,gross_pnl=gross,fee=fee,pnl=net,
                       reason='NET_TARGET_IOC',holding_seconds=self.clock()-lot['opened'],
                       depth_fills=fills,remaining_qty=max(0,lot['qty']),closed=closed,
                       net_return_bps=net/(qty*lot['entry'])*10000)
            if closed:self.lots.pop(lid);self.stats['closed_lots']+=1
        else:self.event('NET_TARGET_UNFILLED',market=lot['market'],lot=lid,reason='Profit-protected limit no longer executable after latency')
    def enforce_profit_capture(self,market=None):
        if self.finish_if_limit():return
        now=self.clock()
        for lid,ready in list(self.profit_requests.items()):
            if market is not None and self.lots.get(lid,{}).get("market") not in (None,market):continue
            if now<ready:continue
            self.profit_requests.pop(lid,None)
            # Reconcile cancellation before any IOC to prevent duplicate exits.
            for oid,o in list(self.orders.items()):
                if o['lot']==lid:
                    self.orders.pop(oid);self.stats['cancelled']+=1
            lot=self.lots.get(lid)
            if not lot:continue
            lot['exit_id']=None;m=lot['market']
            if self.connected and self.top(m) and now-self.books[m]['received']<=15:self.execute_profit(lid)
        if self.finish_if_limit():return
        # Prioritize an executable net target before timer-based passive repricing.
        if self.connected:
            for lid,lot in list(self.lots.items()):
                m=lot['market']
                if market is not None and m!=market:continue
                if lid in self.profit_requests or not self.top(m) or now-self.books[m]['received']>15:continue
                if next(self.available_levels(lot),None) is not None:self.request_profit(lid)
    def maintain(self):
        self.enforce_profit_capture()
        super().maintain()
    def receive(self,d):
        m=d.get('market');typ=d.get('header',{}).get('type','');previous=self.seq.get((m,'book'))
        super().receive(d)
        if typ.startswith('orderbook.') and self.seq.get((m,'book'))!=previous:
            if typ.endswith('snapshot'):self.used_depth={k:v for k,v in self.used_depth.items() if k[0]!=m}
            else:
                for side in ('bids','asks'):
                    for price,_ in d.get(side,[]):self.used_depth.pop((m,side,float(price)),None)
            # Enforce on EACH accepted book message, including messages batched
            # into one websocket read. Replenished depth retries partial exits.
            self.enforce_profit_capture(m)
    def disconnect(self):
        super().disconnect();self.profit_requests.clear();self.used_depth.clear()
    def snapshot(self):
        d=super().snapshot();d['strategy']='Maker entries with fee-aware 0.10% profit capture — paper'
        d['pending_market_exits']=len(self.profit_requests)
        for m,s in d['markets'].items():s['pending_market_exits']=sum(self.lots.get(lid,{}).get('market')==m for lid in self.profit_requests)
        d['limitations']=[x for x in d['limitations'] if not x.startswith('Strict maker exits')]
        d['limitations'].append('Maker exits require public trade fills. Profit-protected IOC exits use observed L2 depth after 250ms and require at least the target after modeled taker fees. Funding excluded; a missed price or insufficient depth can prevent a fill.')
        return d

def configure():
    maker_only.configure()
    CONFIG.update(strategy_version=6,exit_mode='maker_with_net_target_ioc',taker_orders=True,
                  profit_target_net_of_exit_fee=True,profit_capture_every_book_update=True)
    # SUI entries paused (owner, 2026-10-08): largest runaway losses. Still
    # subscribed so carried SUI lots can exit. Everything else is the original
    # 0.10% target / 90s hold config; 0.05% + 10 min hold lost money that day.
    # Max hold 90s -> 120s (owner, 2026-10-08). 180s was tried briefly; in the
    # original run trades open past 180s were net negative.
    v2.RULES.update(max_hold_seconds=120)
    v2.EXCLUDED_ENTRIES.add("SUIUSDT-PERP")
    v2.ACTIVE_MARKETS[:]=[m for m in runner.MARKETS if m not in v2.EXCLUDED_ENTRIES]

if __name__=='__main__':
    configure();runner.main(ProfitCapturePaper)
