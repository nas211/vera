"""Deterministic PAPER exchange overlay. No sockets, credentials or order API."""
from collections import Counter, defaultdict, deque
import copy, math
from .feed import Feed, FeedError
from .legacy import BASE, MODULES
EPS=1e-10

class Engine:
    def __init__(self,config,scenario,filters,strategy='candidate'):
        if config.get('mode')!='PAPER_ONLY':raise ValueError('paper only')
        if strategy not in ('candidate','existing_paper','existing_live_rules'):raise ValueError(strategy)
        self.c=copy.deepcopy(config);self.s=copy.deepcopy(scenario);self.filters=filters;self.strategy=strategy
        self.feed=Feed(config['coins']);self.now=0.;self.start=None;self.connected=False
        self.orders={};self.lots={};self.parents={};self.closed=[];self.next_id=0
        self.history=defaultdict(deque);self.last_quote={};self.cooldown={};self.depth={};self.last_mark={}
        self.realized=0.;self.fees=0.;self.halted=False;self.count=Counter();self.events=[]
        self.max_exposure=0.;self.exposure_area=0.;self.last_metric=None;self.peak=0.;self.drawdown=0.
        self.unknown_orders=[];self.slippages=[];self.exit_delays=[];self.source_time_missing=0;self.coin_peaks=defaultdict(float);self.coin_drawdowns=defaultdict(float);self.coin_exposure_max=defaultdict(float)
        self.legacy=BASE(filters,clock=lambda:self.now,record=False)
    def log(self,kind,**kw):self.events.append(dict(ts=self.now,kind=kind,**kw));self.count[kind]+=1
    def top(self,m):
        b=self.feed.books.get(m)
        return (max(b['bids']),min(b['asks'])) if b else None
    def fresh(self,m):
        b=self.feed.books.get(m)
        limit=self.c['max_book_age'] if self.strategy=='candidate' else 15
        return bool(self.connected and b and 0<=self.now-b['received']<=limit and
                    (b['source_time'] is None or -1<=self.now-b['source_time']<=limit))
    def price_round(self,m,p,buy):
        t=self.filters[m]['tick'];return (math.floor(p/t+1e-9) if buy else math.ceil(p/t-1e-9))*t
    def exposure(self):
        return sum(l['qty']*max(l['entry'],self.last_mark.get(l['market'],l['entry'])) for l in self.lots.values())+sum(o['qty']*o['price'] for o in self.orders.values() if not o['lot'])
    def marked_pnl(self):
        # Executable touch minus exit fees, without pretending all inventory fits at touch.
        value=self.realized
        for l in self.lots.values():
            t=self.top(l['market']);p=(t[0] if l['buy'] else t[1]) if t else self.last_mark.get(l['market'],l['entry'])
            value+=(p-l['entry'])*l['qty']*(1 if l['buy'] else -1)-p*l['qty']*self.c['taker_fee']
        return value
    def metric(self):
        ex=self.exposure();net=self.marked_pnl()
        if self.last_metric is not None:self.exposure_area+=self.last_metric[1]*max(0,self.now-self.last_metric[0])
        self.last_metric=(self.now,ex);self.max_exposure=max(self.max_exposure,ex)
        self.peak=max(self.peak,net);self.drawdown=max(self.drawdown,self.peak-net)
        for m in self.c['coins']:
            pnl=sum(p['net'] for p in self.parents.values() if p['market']==m)+sum(p['net'] for p in self.closed if p['market']==m)
            for l in self.lots.values():
                if l['market']!=m:continue
                t=self.top(m);px=(t[0] if l['buy'] else t[1]) if t else self.last_mark.get(m,l['entry'])
                pnl+=(px-l['entry'])*l['qty']*(1 if l['buy'] else -1)-px*l['qty']*self.c['taker_fee']
            ex=sum(l['qty']*max(l['entry'],self.last_mark.get(m,l['entry'])) for l in self.lots.values() if l['market']==m)+sum(o['qty']*o['price'] for o in self.orders.values() if o['market']==m and not o['lot'])
            self.coin_peaks[m]=max(self.coin_peaks[m],pnl);self.coin_drawdowns[m]=max(self.coin_drawdowns[m],self.coin_peaks[m]-pnl);self.coin_exposure_max[m]=max(self.coin_exposure_max[m],ex)
        if net<=-self.c['session_loss'] and not self.halted:
            self.halted=True;self.log('session_loss_halt',marked_pnl=net)
    def signal(self,m):
        self.legacy.books=self.feed.books;self.legacy.history=self.history
        return self.legacy.signal(m)
    def entry_reason(self,m,buy,ignore_occupancy=False):
        if self.halted:return 'session_halt'
        if self.unknown_orders:return 'unresolved_offline_fills'
        if not self.fresh(m):return 'stale_or_disconnected'
        if self.strategy!='candidate':
            self.legacy.books=self.feed.books;self.legacy.history=self.history
            return self.legacy.entry_reason(m,buy)
        if not ignore_occupancy and (any(l['market']==m for l in self.lots.values()) or any(o['market']==m and not o['lot'] for o in self.orders.values())):return 'coin_occupied'
        if self.now<self.cooldown.get(m,0):return 'loss_cooldown'
        b,a=self.top(m)
        if (a-b)/((a+b)/2)*10000>self.c['max_spread_bps']:return 'spread'
        h=self.history[m]
        if not h or self.now-h[0][0]<self.c['trend_seconds']:return 'warmup'
        old=next((p for ts,p in reversed(h) if ts<=self.now-self.c['trend_seconds']),None)
        if old is None:return 'warmup'
        sign=1 if buy else -1;trend=(((a+b)/2)/old-1)*10000
        book=self.feed.books[m];bid=sum(q for _,q in sorted(book['bids'].items(),reverse=True)[:5]);ask=sum(q for _,q in sorted(book['asks'].items())[:5])
        pressure=(bid-ask)/(bid+ask)
        if trend*sign<self.c['momentum_bps']:return 'momentum'
        if pressure*sign<self.c['pressure']:return 'pressure'
        return None
    def place(self,m,buy,p,q,lot=None,kind='maker',reason=None):
        if not self.fresh(m) or q<=EPS:return None
        if lot and (lot not in self.lots or any(o['lot']==lot for o in self.orders.values())):return None
        p=self.price_round(m,p,buy if kind=='maker' else not buy)
        if not lot:
            reason_block=self.entry_reason(m,buy)
            if reason_block:self.count['blocked_'+reason_block]+=1;return None
            f=self.filters[m];q=math.floor(q/f['step']+1e-9)*f['step']
            if q<f['min_qty'] or p*q<f['min_notional']:self.count['blocked_minimum']+=1;return None
            if self.exposure()+p*q>self.c['max_exposure']+EPS:self.count['blocked_exposure']+=1;return None
        else:q=min(q,self.lots[lot]['qty'])
        self.next_id+=1;oid=str(self.next_id)
        self.orders[oid]=dict(id=oid,market=m,buy=buy,price=p,qty=q,lot=lot,kind=kind,reason=reason,
                              requested=self.now,ready=self.now+self.s['order_delay'],active=False,active_at=None,queue=0.,cancel_at=None)
        if not lot:self.parents[oid]=dict(market=m,buy=buy,net=0.,filled_qty=0.,entry_value=0.,exit_value=0.,opened=None)
        self.log('submit',order=dict(self.orders[oid]));return oid
    def cancel(self,o):
        # Repeated requests cannot postpone cancellation. Offline requests await reconnect.
        if o['cancel_at'] is None and self.connected:
            o['cancel_at']=self.now+self.s['cancel_delay'];self.log('cancel_request',id=o['id'])
    def parent_done(self,pid):
        if pid not in self.parents:return
        p=self.parents[pid]
        if pid in self.orders or any(l['parent']==pid for l in self.lots.values()):return
        if p['filled_qty']>EPS:
            row=dict(p,parent=pid,closed=self.now,hold=self.now-p['opened']);self.closed.append(row)
            if p['net']<0:self.cooldown[p['market']]=self.now+self.c['cooldown_seconds']
            self.log('parent_closed',**row)
        del self.parents[pid]
    def fill(self,o,q,p,taker=False):
        q=min(q,o['qty'])
        if o['lot']:
            l=self.lots.get(o['lot'])
            if not l:return
            q=min(q,l['qty'])
        if q<=EPS:return
        fee=q*p*self.c['taker_fee' if taker else 'maker_fee'];self.fees+=fee
        o['qty']-=q
        if not o['lot']:
            pid=o['id'];parent=self.parents[pid];parent['filled_qty']+=q;parent['entry_value']+=q*p;parent['net']-=fee
            if parent['opened'] is None:parent['opened']=self.now
            self.realized-=fee
            lid='lot-'+pid
            if lid not in self.lots:self.lots[lid]=dict(market=o['market'],buy=o['buy'],entry=p,qty=0.,opened=self.now,parent=pid,trigger=None,exit_reason=None,trigger_mid=None)
            l=self.lots[lid];l['entry']=(l['entry']*l['qty']+q*p)/(l['qty']+q);l['qty']+=q
            self.log('entry_fill',parent=pid,market=o['market'],qty=q,price=p,fee=fee)
        else:
            l=self.lots[o['lot']];pid=l['parent'];gross=(p-l['entry'])*q*(1 if l['buy'] else -1);net=gross-fee
            self.realized+=net;self.parents[pid]['net']+=net;self.parents[pid]['exit_value']+=q*p;l['qty']-=q
            delay=None if l['trigger'] is None else self.now-l['trigger']
            if delay is not None:self.exit_delays.append(delay)
            self.log('exit_fill',parent=pid,market=l['market'],qty=q,price=p,gross=gross,fee=fee,net=net,reason=o['reason'],exit_delay=delay)
            if net<0:
                self.cooldown[l['market']]=self.now+self.c['cooldown_seconds']
                if self.strategy=='candidate' and pid in self.orders:self.cancel(self.orders[pid])
            if l['qty']<=EPS:del self.lots[o['lot']]
        if o['qty']<=EPS:self.orders.pop(o['id'],None)
        self.parent_done(pid)
    def take(self,o):
        l=self.lots.get(o['lot'])
        if not l:return
        side='asks' if o['buy'] else 'bids';m=o['market'];top=self.top(m);touch=top[1] if o['buy'] else top[0]
        for p in sorted(self.feed.books[m][side],reverse=not o['buy']):
            if (o['buy'] and p>o['price']+EPS) or (not o['buy'] and p<o['price']-EPS):break
            # Extra adverse price movement is a stress assumption, bounded by IOC limit.
            px=p*(1+(1 if o['buy'] else -1)*self.s['extra_slippage_bps']/10000)
            if (o['buy'] and px>o['price']) or (not o['buy'] and px<o['price']):break
            if o['reason']=='target' and ((px-l['entry'])*(1 if l['buy'] else -1)-px*self.c['taker_fee']-l['entry']*self.c['maker_fee'])/l['entry']*10000<self.c['target_bps']:break
            key=(m,side,p);q=min(o['qty'],self.depth.get(key,0))
            if q<=EPS:continue
            self.depth[key]-=q;self.slippages.append((px/touch-1)*10000*(1 if o['buy'] else -1))
            self.fill(o,q,px,True)
            if o['qty']<=EPS or o['lot'] not in self.lots:break
        self.orders.pop(o['id'],None) # IOC remainder expires; next fresh frame may retry.
    def trade(self,m,t):
        if not self.fresh(m):return
        remaining=t['amount'];buy=t['direction']=='sell';price=t['price']
        if t['ts']>self.now+1:self.count['future_trade']+=1;return
        eligible=sorted((o for o in self.orders.values() if o['market']==m and o['kind']=='maker' and o['buy']==buy and o['active'] and not o.get('uncertain') and o['active_at']<=t['ts']<=self.now+1 and (o['cancel_at'] is None or t['ts']<o['cancel_at']) and (price<=o['price'] if buy else price>=o['price'])),key=lambda o:((-o['price'] if buy else o['price']),o['active_at'],int(o['id'])))
        for o in eligible:
            if remaining<=EPS:break
            # Trade-through proves displayed queue exhaustion. Same-price trades consume
            # queue first. Cancellations NEVER advance queue; trade volume is not reused.
            if price<o['price']-EPS if buy else price>o['price']+EPS:o['queue']=0
            used=min(o['queue'],remaining);o['queue']-=used;remaining-=used
            qty=min(o['qty'],remaining)
            if o['lot'] and o['lot'] not in self.lots:continue
            if qty>EPS:self.fill(o,qty,o['price']);remaining-=qty
    def disconnect(self,reason='feed_disconnect'):
        if self.connected:
            for o in self.orders.values():
                self.unknown_orders.append(dict(at=self.now,reason=reason,order=dict(o)))
            self.log('disconnect',reason=reason,orders=len(self.orders),inventory=len(self.lots))
        self.connected=False;self.feed.reset();self.history.clear();self.depth.clear()
        # Exchange resting orders are not magically cancelled by a public-feed outage.
        for o in self.orders.values():o['cancel_at']=None;o['uncertain']=True
    def connect(self):
        self.connected=True;self.log('connect')
        for o in self.orders.values():self.cancel(o)
    def receive(self,d,now):
        self.now=now
        try:out=self.feed.receive(d,now)
        except (FeedError,KeyError,TypeError,ValueError) as exc:self.disconnect(str(exc));self.count['data_errors']+=1;return
        if not out:return
        kind,m,data=out
        if kind=='book':
            b,a=self.top(m);mid=(b+a)/2;self.last_mark[m]=mid;h=self.history[m]
            if not h or now-h[-1][0]>=.1:h.append((now,mid))
            while len(h)>1 and h[1][0]<now-max(60,self.c['trend_seconds']+1):h.popleft()
            if self.feed.books[m]['source_time'] is None:self.source_time_missing+=1
            if data['snapshot']:
                self.depth={k:v for k,v in self.depth.items() if k[0]!=m}
                for side in ('bids','asks'):
                    for p,q in self.feed.books[m][side].items():self.depth[m,side,p]=q*self.s['depth_fraction']
            else:
                for side,p,old,q in data['changed']:
                    key=(m,side,p)
                    self.depth[key]=min(q*self.s['depth_fraction'],max(0,self.depth.get(key,0)+(q-old)*self.s['depth_fraction']))
        else:
            for t in data:self.trade(m,t)
    def exit_decision(self,l):
        m=l['market'];t=self.top(m)
        if not t:return None
        b,a=t;sign=1 if l['buy'] else -1;mid=(a+b)/2
        # Time trigger is latched even while disconnected; execution awaits fresh data.
        reason='session_loss' if self.halted else 'time' if self.now-l['opened']>=self.c['max_hold_seconds'] else 'stop' if (mid/l['entry']-1)*10000*sign<=-self.c['stop_bps'] else None
        if reason and l['trigger'] is None:l['trigger']=self.now;l['exit_reason']=reason;l['trigger_mid']=mid
        touch=b if l['buy'] else a
        target=((touch-l['entry'])*sign-touch*self.c['taker_fee']-l['entry']*self.c['maker_fee'])/l['entry']*10000>=self.c['target_bps']
        urgent=l['trigger'] is not None
        if urgent:
            if self.strategy=='existing_paper' and not self.halted:return ('maker',a if l['buy'] else b,l['exit_reason'])
            if self.strategy=='existing_live_rules' and not self.halted and self.now-l['trigger']<5 and (mid/l['trigger_mid']-1)*10000*sign>-3:return ('maker',a if l['buy'] else b,l['exit_reason'])
            return ('ioc',touch*(1-sign*self.c['slippage_limit_bps']/10000),l['exit_reason'])
        if target:return ('ioc',touch,'target')
        price=l['entry']*(1+sign*self.c['target_bps']/10000)
        return ('maker',max(price,a) if l['buy'] else min(price,b),'target')
    def step(self,now):
        self.now=now
        if self.start is None:self.start=now
        self.metric()
        for lid,l in self.lots.items():
            if l['trigger'] is None and (self.halted or now-l['opened']>=self.c['max_hold_seconds']):
                l['trigger']=now;l['exit_reason']='session_loss' if self.halted else 'time';l['trigger_mid']=self.last_mark.get(l['market'],l['entry'])
        for oid,o in list(self.orders.items()):
            if o['cancel_at'] is not None and now>=o['cancel_at'] and self.connected:
                self.orders.pop(oid,None);self.log('cancel_ack',id=oid)
                if not o['lot']:self.parent_done(oid)
                continue
            if not self.fresh(o['market']):
                if not o['lot']:self.cancel(o)
                continue
            if not o['active'] and now>=o['ready']:
                b,a=self.top(o['market'])
                if o['kind']=='maker' and (o['price']>=a if o['buy'] else o['price']<=b):
                    self.orders.pop(oid,None);self.log('post_only_reject',id=oid)
                    if not o['lot']:self.parent_done(oid)
                    continue
                o['active']=True;o['active_at']=now
                side='bids' if o['buy'] else 'asks'
                o['queue']=self.feed.books[o['market']][side].get(o['price'],0)*self.s['queue_multiplier']
                # Earlier own orders already share the single trade-volume budget.
                self.log('ack',id=oid,queue=o['queue'])
                if o['kind']=='ioc':self.take(o);continue
            if not o['lot']:
                ttl=self.c['entry_ttl'] if self.strategy=='candidate' else 35
                # For a pending candidate, evaluate directional filters without occupancy.
                if self.halted or now-o['requested']>=ttl:self.cancel(o)
                elif self.entry_reason(o['market'],o['buy'],ignore_occupancy=True):self.cancel(o)
        # Exit decisions always precede entries, and replacement waits for cancel ack.
        for lid,l in list(self.lots.items()):
            decision=self.exit_decision(l)
            if not decision or not self.fresh(l['market']):continue
            kind,p,reason=decision
            if l['trigger'] is not None and l['parent'] in self.orders:self.cancel(self.orders[l['parent']])
            existing=[o for o in self.orders.values() if o['lot']==lid]
            if existing:
                o=existing[0]
                away=p>o['price'] if l['buy'] else p<o['price']
                refresh=1 if self.strategy=='existing_live_rules' and reason!='target' else 5
                if kind!=o['kind'] or (kind=='maker' and now-o['requested']>=refresh and abs(p-o['price'])>=self.filters[l['market']]['tick']*.99 and not(reason=='target' and away)):self.cancel(o)
                continue
            self.place(l['market'],not l['buy'],p,l['qty'],lid,kind,reason)
        if not self.halted:
            for m in self.c['coins']:
                if not self.fresh(m):continue
                b,a=self.top(m)
                if self.strategy=='candidate':
                    for buy,p in ((True,b),(False,a)):
                        if self.entry_reason(m,buy) is None:self.place(m,buy,p,self.c['entry_notional']/p)
                elif now-self.last_quote.get(m,-1e20)>=35 and not any(o['market']==m and not o['lot'] for o in self.orders.values()):
                    spacing=max(self.filters[m]['tick'],(b+a)/2*.5/10000)
                    for level in range(5):
                        for buy,p in ((True,b-level*spacing),(False,a+level*spacing)):
                            self.place(m,buy,p,self.c['entry_notional']/p)
                    self.last_quote[m]=now
        self.metric()
    def report(self):
        self.metric()
        def stats(rows):
            vals=[r['net'] for r in rows];wins=[v for v in vals if v>0];losses=[v for v in vals if v<0]
            return dict(closed_parents=len(vals),net=sum(vals),expectancy=sum(vals)/len(vals) if vals else None,win_rate=len(wins)/len(vals) if vals else None,average_win=sum(wins)/len(wins) if wins else None,average_loss=sum(losses)/len(losses) if losses else None)
        def delay(values):
            v=sorted(values)
            return dict(n=len(v),mean=sum(v)/len(v) if v else None,p95=v[min(len(v)-1,math.ceil(.95*len(v))-1)] if v else None,max=max(v) if v else None)
        by={}
        for m in self.c['coins']:
            ev=[e for e in self.events if e.get('market')==m]
            by[m]=dict(stats([r for r in self.closed if r['market']==m]),drawdown=self.coin_drawdowns[m],max_exposure=self.coin_exposure_max[m],fees=sum(e.get('fee',0) for e in ev),exit_delays=delay([e['exit_delay'] for e in ev if e.get('exit_delay') is not None]),open_qty=sum(l['qty'] for l in self.lots.values() if l['market']==m),open_notional=sum(l['qty']*l['entry'] for l in self.lots.values() if l['market']==m))
        return dict(strategy=self.strategy,closed=stats(self.closed),realized_net=self.realized,fees=self.fees,marked_net_before_funding=self.marked_pnl(),drawdown=self.drawdown,max_exposure=self.max_exposure,mean_exposure=self.exposure_area/(self.now-self.start) if self.start is not None and self.now>self.start else 0,exit_delays=delay(self.exit_delays),slippage_bps=delay(self.slippages),by_coin=by,unresolved_inventory=copy.deepcopy(self.lots),pending_orders=copy.deepcopy(self.orders),offline_fill_uncertainty=self.unknown_orders,book_messages_without_source_time=self.source_time_missing,halted=self.halted,counts=dict(self.count),unresolved_possible_notional=sum(o['order']['qty']*o['order']['price'] for o in {v['order']['id']:v for v in self.unknown_orders if not v['order']['lot']}.values()),statistically_sufficient=False,valid_for_inference=not self.unknown_orders,limitations=['PAPER counterfactual; unit tests do not establish profitability.','Funding and liquidation excluded; fee tier must be verified for each account.','Public L2 cannot identify true queue position, hidden liquidity or market impact.','No offline fills invented: outstanding orders at gaps have unresolved fill uncertainty.','Open inventory valued at last executable touch less fees; depth may not support full liquidation.','Baseline signals are frozen original methods; shared sizing, portfolio brake and execution overlay are deliberate comparison overrides.','Seven days and 200 closed parents are review gates, not statistical proof; coin/time observations are correlated.'])
