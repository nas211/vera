#!/usr/bin/python3
"""Public-data-only maker grid experiment. No private API or order transport."""
import json, time, math, os, datetime, signal
from pathlib import Path
import requests
import websocket

ROOT=Path(__file__).resolve().parent
MARKETS=['LINKUSDT-PERP','HYPEUSDT-PERP','SOLUSDT-PERP','XRPUSDT-PERP','DOGEUSDT-PERP','BTCUSDT-PERP','ETHUSDT-PERP','BNBUSDT-PERP','SUIUSDT-PERP','TAOUSDT-PERP']
CONFIG=dict(stop_after_completed_trades=1000,starting_equity=1000,leverage=10,levels_per_side=10,sizing="full_available_budget",
            entry_spacing_bps=0.5,target_bps=2,latency_ms=250,refresh_seconds=60,
            gross_notional_cap=10000,per_market_cap=1000,fee_tier="VIP5",maker_fee=0,taker_fee=0.00015,taker_orders=False)
PUBLIC=Path('/opt/why-yellow/webui/data/micro-paper.json')

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    p=path.with_suffix('.tmp');p.write_text(json.dumps(data,allow_nan=False));p.replace(path)
def iso_ts(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()

class Paper:
    def __init__(self,filters,clock=time.time,record=True):
        self.filters=filters;self.clock=clock;self.record=record
        self.started=clock();self.orders={};self.lots={};self.books={};self.seq={};self.next_id=0
        self.stats=dict(placed=0,cancelled=0,post_only_rejected=0,entry_fills=0,exit_fills=0,
                        closed_lots=0,public_trades=0,realized_pnl=0.,fees=0.,sequence_gaps=0)
        self.last_quote={};self.last_mid={};self.connected=False;self.last_message=0;self.error=None;self.halted=False;self.finished_at=None
        self.funding_note='P&L excludes funding; no claim of net profitability across funding settlements.'
    def event(self,kind,**kw):
        if self.record:
            with (ROOT/'state/events.jsonl').open('a') as f:f.write(json.dumps(dict(ts=self.clock(),event=kind,**kw))+'\n')
    def save(self):
        if self.record:
            atomic(ROOT/'state/checkpoint.json',dict(started=self.started,lots=self.lots,stats=self.stats,next_id=self.next_id,halted=self.halted,last_mid=self.last_mid,finished_at=self.finished_at))
            atomic(PUBLIC,self.snapshot())
    def restore(self):
        p=ROOT/'state/checkpoint.json'
        if p.exists():
            d=json.loads(p.read_text());self.started=d['started'];self.lots=d['lots'];self.stats=d['stats'];self.next_id=d['next_id'];self.halted=d.get('halted',False);self.last_mid=d.get('last_mid',{});self.finished_at=d.get('finished_at')
            for lot in self.lots.values():lot['exit_id']=None
            self.event('RESTART',note='Preserved inventory; simulated resting orders cancelled and queues reset.')
    def finish_if_limit(self):
        if self.finished_at is not None:return True
        limit=CONFIG.get('stop_after_completed_trades')
        if limit is None or self.stats['closed_lots'] < limit:return False
        self.finished_at=self.clock();self.connected=False
        self.stats['cancelled']+=len(self.orders);self.orders.clear()
        for lot in self.lots.values():lot['exit_id']=None
        self.event('EXPERIMENT_COMPLETE',completed_trades=self.stats['closed_lots'],
                   open_inventory_lots=len(self.lots),note='Remaining inventory frozen, not closed or excluded from equity.')
        self.save()
        if self.record:
            atomic(ROOT/'state/final-results.json',dict(snapshot=self.snapshot(),open_inventory=self.lots,last_mid=self.last_mid))
        return True
    def rounded(self,m,p,buy):
        t=self.filters[m]['tick'];return (math.floor(p/t+1e-9) if buy else math.ceil(p/t-1e-9))*t
    def top(self,m):
        b=self.books.get(m,{})
        if not b.get('bids') or not b.get('asks'):return None
        return max(b['bids']),min(b['asks'])
    def exposure(self,m=None):
        total=0
        for l in self.lots.values():
            if m and l['market']!=m:continue
            top=self.top(l['market']);px=sum(top)/2 if top else self.last_mid.get(l['market'],l['entry'])
            total+=l['qty']*max(px,l['entry'])
        total+=sum(o['qty']*o['price'] for o in self.orders.values() if not o['lot'] and (not m or o['market']==m))
        return total
    def pnl(self):
        out=0
        for l in self.lots.values():
            t=self.top(l['market']);px=sum(t)/2 if t else self.last_mid.get(l['market'],l['entry'])
            out+=(px-l['entry'])*l['qty']*(1 if l['buy'] else -1)
        return out
    def place(self,m,buy,p,q,lot=None):
        top=self.top(m)
        if not top or q<=0:return None
        p=self.rounded(m,p,buy)
        if (buy and p>=top[1]) or (not buy and p<=top[0]):
            self.stats['post_only_rejected']+=1;return None
        if not lot:
            f=self.filters[m];q=math.floor(q/f['step']+1e-9)*f['step']
            if q<f['min_qty'] or p*q<f['min_notional']:return None
            cap=min(CONFIG['gross_notional_cap'],max(0,1000+self.stats['realized_pnl']+self.pnl())*10)
            if self.exposure()+q*p>cap or self.exposure(m)+q*p>CONFIG['per_market_cap']:return None
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
        cap=min(CONFIG['gross_notional_cap'],max(0,1000+self.stats['realized_pnl']+self.pnl())*10)
        # Falling equity withdraws pending entry reservations before restricting
        # inventory. Never manufacture a market exit for a maker-only test.
        pending=sum(o['qty']*o['price'] for o in self.orders.values() if not o['lot'])
        if self.exposure()-pending>cap:self.halted=True
        projected=self.exposure()-sum(o['qty']*o['price'] for o in self.orders.values() if not o['lot'] and o['cancel_at'] is not None)
        for o in sorted(self.orders.values(),key=lambda o:o['qty']*o['price'],reverse=True):
            if not o['lot'] and o['cancel_at'] is None and (self.halted or projected>cap):
                o['cancel_at']=now+.25;projected-=o['qty']*o['price']
        for m in MARKETS:
            if not self.top(m) or not self.connected:continue
            if now-self.books[m]['received']>15:
                for o in self.orders.values():
                    if o['market']==m and not o['lot'] and o['cancel_at'] is None:o['cancel_at']=now+.25
                continue
            # Keep each partial fill as an inventory lot with a reduce-only exit.
            for lid,l in list(self.lots.items()):
                if l['market']!=m or l.get('exit_id') in self.orders:continue
                bid,ask=self.top(m);buy=not l['buy']
                target=l['entry']*(1+(-1 if buy else 1)*CONFIG['target_bps']/10000)
                price=min(target,bid) if buy else max(target,ask)
                l['exit_id']=self.place(m,buy,price,l['qty'],lid)
            if self.halted:continue
            if now-self.last_quote.get(m,-1e10)>=CONFIG['refresh_seconds']:
                old=[o for o in self.orders.values() if o['market']==m and not o['lot']]
                if old:
                    for o in old:
                        if o['cancel_at'] is None:o['cancel_at']=now+.25
                    continue
                bid,ask=self.top(m);mid=(bid+ask)/2;seen=set()
                tick=self.filters[m]['tick'];spacing=max(tick,mid*CONFIG['entry_spacing_bps']/10000)
                f=self.filters[m]
                available=min(CONFIG['per_market_cap']-self.exposure(m),cap-self.exposure())
                # Distribute the available market allocation over as many valid
                # two-sided levels as its lot and notional minimums allow.
                max_price=ask+(CONFIG['levels_per_side']-1)*spacing
                min_qty=max(f['min_qty'],math.ceil(f['min_notional']/(bid*f['step']))*f['step'])
                slots=min(2*CONFIG['levels_per_side'],max(0,int(available/(max_price*min_qty))))
                # Interleave bid/ask so restricted capacity remains balanced.
                candidates=[]
                for level in range(CONFIG['levels_per_side']):
                    for buy,base in [(True,bid),(False,ask)]:
                        price=self.rounded(m,base+(-1 if buy else 1)*level*spacing,buy)
                        if (buy,price) not in seen:
                            seen.add((buy,price));candidates.append((buy,price))
                for i,(buy,price) in enumerate(candidates[:slots]):
                    available=min(CONFIG['per_market_cap']-self.exposure(m),cap-self.exposure())
                    qty=available/(slots-i)/price
                    self.place(m,buy,price,qty)
                self.last_quote[m]=now
    def trade(self,m,t):
        if self.finish_if_limit():return
        self.stats['public_trades']+=1
        ts=iso_ts(t['executed_at']);price=float(t['price']);remaining=float(t['amount']);buy=t['direction']=='sell'
        orders=sorted([o for o in self.orders.values() if o['market']==m and o['buy']==buy and o['active']
                       and o['active_at']<=ts and (o['cancel_at'] is None or ts<o['cancel_at'])
                       and ((buy and price<=o['price']) or (not buy and price>=o['price']))],
                      key=lambda o:((-o['price'] if buy else o['price']),o['active_at']))
        for o in orders:
            if remaining<=1e-12:break
            # No queue advancement from cancellations. A trade THROUGH the price
            # establishes prior displayed queue exhaustion. Same-price trades
            # consume queue first. Observed volume is never reused across orders.
            through=price<o['price']-1e-10 if buy else price>o['price']+1e-10
            if through:o['queue']=0
            used=min(o['queue'],remaining);o['queue']-=used;remaining-=used
            q=min(o['qty'],remaining)
            if q<=1e-12:continue
            remaining-=q;o['qty']-=q
            if o['lot']:
                l=self.lots[o['lot']];pnl=(o['price']-l['entry'])*q*(1 if l['buy'] else -1)
                l['qty']-=q;self.stats['realized_pnl']+=pnl;self.stats['exit_fills']+=1
                if l['qty']<1e-10:del self.lots[o['lot']];self.stats['closed_lots']+=1
                self.event('MAKER_EXIT_FILL',market=m,price=o['price'],qty=q,pnl=pnl,fee=0,lot=o['lot'],entry_order=l.get('entry_order'),entry_price=l['entry'],side='long' if l['buy'] else 'short',holding_seconds=self.clock()-l['opened'],reason=o.get('exit_reason','unknown'),remaining_qty=max(0,l['qty']),closed=l['qty']<1e-10)
            else:
                self.next_id+=1;lid='lot-'+str(self.next_id)
                self.lots[lid]=dict(market=m,buy=buy,entry=o['price'],qty=q,opened=self.clock(),exit_id=None,entry_order=o['id'])
                self.stats['entry_fills']+=1;self.event('MAKER_ENTRY_FILL',market=m,price=o['price'],qty=q,fee=0,lot=lid,entry_order=o['id'],side='long' if buy else 'short')
            if o['qty']<1e-10:self.orders.pop(o['id'],None)
            if self.finish_if_limit():return
    def receive(self,d):
        if self.finish_if_limit():return
        typ=d.get('header',{}).get('type','');m=d.get('market')
        if m not in MARKETS:return
        kind='book' if typ.startswith('orderbook.') else 'trades'
        key=(m,kind);seq=d.get('sequence_num');snap=typ.endswith('snapshot')
        if not snap and self.seq.get(key) is not None:
            if seq<=self.seq[key]:return
            if seq!=self.seq[key]+1:self.stats['sequence_gaps']+=1;raise RuntimeError('sequence gap '+m+' '+kind)
        if not snap and key not in self.seq:return
        self.seq[key]=seq
        if kind=='book':
            if snap:self.books[m]=dict(bids={},asks={})
            for side in ['bids','asks']:
                for p,q in d.get(side,[]):
                    p=float(p);q=float(q)
                    if q:self.books[m][side][p]=q
                    else:self.books[m][side].pop(p,None)
            self.books[m]['received']=self.clock()
            top=self.top(m)
            if top:self.last_mid[m]=sum(top)/2
        elif not snap:
            for t in sorted(d.get('trades',[]),key=lambda t:t['executed_at']):self.trade(m,t)
        # Historical trade snapshots NEVER generate fills.
    def disconnect(self):
        self.connected=False;self.stats['cancelled']+=len(self.orders);self.orders.clear();self.seq.clear();self.books.clear();self.last_quote.clear()
        for l in self.lots.values():l['exit_id']=None
        self.event('FEED_RESET',note='Cancelled simulated orders; inventory preserved; offline fills not inferred.')
    def snapshot(self):
        u=self.pnl();equity=1000+self.stats['realized_pnl']+u
        by={m:dict(entry_orders=sum(o['market']==m and not o['lot'] for o in self.orders.values()),exit_orders=sum(o['market']==m and bool(o['lot']) for o in self.orders.values()),inventory_lots=sum(l['market']==m for l in self.lots.values()),book_age_s=round(self.clock()-self.books[m]['received'],1) if m in self.books else None) for m in MARKETS}
        return dict(mode='PAPER ONLY',strategy='Multi-level maker micro-grid',updated_at=self.clock(),started_at=self.started,config=CONFIG,
                    completed_at=self.finished_at,stop_after_completed_trades=CONFIG['stop_after_completed_trades'],connected=self.connected,halted=self.halted,error=self.error,stats=self.stats,equity_before_funding=equity,
                    unrealized_pnl=u,open_inventory_lots=len(self.lots),open_orders=len(self.orders),active_orders=sum(o['active'] for o in self.orders.values()),
                    reserved_and_inventory_notional=self.exposure(),estimated_margin=self.exposure()/10,markets=by,
                    limitations=[self.funding_note,'Queue and 250ms latency are model assumptions, not measured fills.','Public L2 overlay does not model market impact.','Strict maker exits can remain unfilled; unrealized losses are included.','Feed gaps cancel paper quotes; inventory remains. Liquidation is not simulated.'],
                    recent_orders=list(self.orders.values())[:12])

def metadata():
    r=requests.get('https://trade.api.yellow.pro/perpetual/exchangeInfo',timeout=20);r.raise_for_status();out={}
    for s in r.json()['symbols']:
        if s['symbol'] not in MARKETS:continue
        f={x['filter_type']:x['config'] for x in s['filters']}
        out[s['symbol']]=dict(tick=float(f['PRICE_FILTER']['tick_size']),step=float(f['LOT_SIZE']['step_size']),min_qty=float(f['LOT_SIZE']['min_qty']),min_notional=float(f['MIN_NOTIONAL']['min_notional']))
    if len(out)!=len(MARKETS):raise RuntimeError('missing market filters')
    return out

def main(paper_class=Paper):
    p=paper_class({});p.restore();stop=False
    if p.finish_if_limit():return
    p.filters=metadata()
    def shutdown(*args):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    while not stop and p.finished_at is None:
        w=None
        try:
            w=websocket.create_connection('wss://trade.api.yellow.pro/ws',timeout=10)
            w.send(json.dumps({'id':1,'connect':{}}));w.recv();i=2
            for m in MARKETS:
                for channel in ['orderbook','trades']:
                    w.send(json.dumps({'id':i,'subscribe':{'channel':f'public.{channel}.increment.{m}'}}));i+=1
            w.settimeout(.5);p.connected=True;p.error=None;last_save=0
            while not stop and p.finished_at is None:
                try:
                    raw=w.recv()
                    if not raw:raise RuntimeError('socket closed')
                    p.last_message=time.time()
                    for line in raw.splitlines():
                        msg=json.loads(line)
                        if msg=={}:w.send('{}');continue
                        if 'error' in msg:raise RuntimeError(str(msg['error']))
                        d=msg.get('subscribe',{}).get('data') or msg.get('push',{}).get('pub',{}).get('data')
                        if d:p.receive(d)
                except websocket.WebSocketTimeoutException:pass
                p.maintain()
                if time.time()-last_save>=2:p.save();last_save=time.time()
        except Exception as e:
            p.error=str(e);p.disconnect();p.save();print('feed retry:',str(e),flush=True)
            time.sleep(3)
        finally:
            if w:w.close()
    if p.finished_at is None:p.disconnect();p.save()
if __name__=='__main__':main()
