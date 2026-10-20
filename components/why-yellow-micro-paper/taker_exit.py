"""PAPER ONLY: post-only entries, depth-walking IOC exits with VIP5 fees."""
import json
import runner,overnight
from runner import CONFIG,ROOT
from v2 import PaperV2,RULES

class MakerTakerPaper(PaperV2):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.market_exits={};self.used_depth={};self.phase_baseline=None
        p=ROOT/'state/taker-exit-baseline.json'
        if self.record and p.exists():self.phase_baseline=json.loads(p.read_text())
    def place(self,m,buy,p,q,lot=None):
        if lot:return None # exits go exclusively through the IOC simulator
        return super().place(m,buy,p,q,lot)
    def trigger(self,lot):
        bid,ask=self.top(lot['market']);exit_px=bid if lot['buy'] else ask
        move=(exit_px/lot['entry']-1)*10000*(1 if lot['buy'] else -1)
        if move>=CONFIG['target_bps']:return 'TARGET'
        if move<=-RULES['adverse_exit_bps']:return 'STOP'
        if self.clock()-lot['opened']>=RULES['max_hold_seconds']:return 'TIMEOUT'
        return None
    def execute_exit(self,lid,request):
        lot=self.lots.get(lid)
        if not lot:return
        m=lot['market'];side='bids' if lot['buy'] else 'asks'
        qty=0.;value=0.;gross=0.;fills=[]
        for price,displayed in sorted(self.books[m][side].items(),reverse=lot['buy']):
            key=(m,side,price)
            available=max(0,displayed-self.used_depth.get(key,0))
            take=min(lot['qty'],available)
            if take<=1e-12:continue
            self.used_depth[key]=self.used_depth.get(key,0)+take
            lot['qty']-=take;qty+=take;value+=take*price
            gross+=(price-lot['entry'])*take*(1 if lot['buy'] else -1)
            fills.append(dict(price=price,qty=take))
            if lot['qty']<=1e-10:break
        self.market_exits.pop(lid,None)
        if qty>0:
            fee=value*CONFIG['taker_fee'];net=gross-fee
            self.stats['realized_pnl']+=net;self.stats['fees']+=fee;self.stats['exit_fills']+=1
            self.stats['taker_exit_fills']=self.stats.get('taker_exit_fills',0)+1
            self.event('TAKER_EXIT_FILL',market=m,lot=lid,qty=qty,price=value/qty,
                       gross_pnl=gross,fee=fee,pnl=net,reason=request['reason'],depth_fills=fills,
                       remaining_qty=max(0,lot['qty']))
        if lot['qty']<=1e-10:
            self.lots.pop(lid,None);self.stats['closed_lots']+=1
            self.finish_if_limit()
        else:
            # Only displayed volume fills. Remaining inventory remains valued
            # and retries after a delay, never at a fabricated price or depth.
            self.market_exits[lid]=dict(ready_at=self.clock()+1,reason=request['reason'])
    def maintain(self):
        if self.finish_if_limit():return
        super().maintain()
        if not self.connected or self.finished_at is not None:return
        for lid,lot in list(self.lots.items()):
            m=lot['market']
            if not self.top(m) or self.clock()-self.books[m]['received']>15:continue
            req=self.market_exits.get(lid)
            if req is None:
                reason=self.trigger(lot)
                if reason:
                    self.market_exits[lid]=dict(ready_at=self.clock()+CONFIG['latency_ms']/1000,reason=reason)
                    self.event('TAKER_EXIT_REQUEST',market=m,lot=lid,reason=reason)
            elif self.clock()>=req['ready_at']:
                self.execute_exit(lid,req)
                if self.finished_at is not None:return
    def receive(self,d):
        if self.finished_at is not None:return
        m=d.get('market');typ=d.get('header',{}).get('type','')
        previous=self.seq.get((m,'book'))
        super().receive(d)
        # New authoritative L2 quantities refresh the consumed shadow depth.
        # No same-price depth can be reused between these venue updates.
        if typ.startswith('orderbook.') and self.seq.get((m,'book'))!=previous:
            if typ.endswith('snapshot'):
                self.used_depth={k:v for k,v in self.used_depth.items() if k[0]!=m}
            else:
                for side in ['bids','asks']:
                    for price,_ in d.get(side,[]):self.used_depth.pop((m,side,float(price)),None)
    def disconnect(self):
        super().disconnect();self.market_exits.clear();self.used_depth.clear()
    def snapshot(self):
        d=super().snapshot();d['strategy']='Maker entries / taker exits — paper';d['pending_market_exits']=len(self.market_exits)
        for m,s in d['markets'].items():s['pending_market_exits']=sum(self.lots.get(lid,{}).get('market')==m for lid in self.market_exits)
        if self.phase_baseline:
            d['maker_taker_phase_pnl']=d['equity_before_funding']-self.phase_baseline['equity_before_funding']
            d['maker_taker_phase_completed']=self.stats['closed_lots']-self.phase_baseline['completed_trades']
        d['limitations']=[x for x in d['limitations'] if 'Strict maker exits' not in x]
        d['limitations'].append('Taker exits walk displayed L2 depth after 250ms; actual market impact and hidden liquidity are not modeled. Funding excluded.')
        return d

def configure():
    overnight.configure()
    CONFIG.update(strategy_version=4,exit_mode='taker_ioc',taker_orders=True,taker_fee=.00015)

if __name__=='__main__':
    configure();runner.main(MakerTakerPaper)
