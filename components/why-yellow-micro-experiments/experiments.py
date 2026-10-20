"""Isolated public-feed paper comparisons. No private API or live order path."""
import contextlib, copy, json, signal, time
from pathlib import Path
import websocket
import runner, v2, maker_only

ROOT=Path(__file__).resolve().parent
PUBLIC=Path('/opt/why-yellow/webui/data/micro-experiments.json')
VARIANTS=[('baseline',10,90),('hold180',10,180),('target5',5,90),('target15',15,90)]
maker_only.configure()
BASE_CONFIG=copy.deepcopy(runner.CONFIG)
BASE_RULES=copy.deepcopy(v2.RULES)

class Experiment(maker_only.MakerOnlyPaper):
    def __init__(self, name, target, hold, filters, clock=time.time, root=ROOT):
        super().__init__(filters,clock=clock,record=False)
        self.name=name;self.target=target;self.hold=hold
        self.directory=root/'state'/name;self.directory.mkdir(parents=True,exist_ok=True)
        self.metrics=dict(by_market={},by_exit_reason={},entry_notional=0.,exit_notional=0.,
                          equity_peak=1000.,max_drawdown_usd=0.,max_drawdown_pct=0.,
                          max_inventory_notional=0.,max_lot_age_seconds=0.,exit_holding_qty_seconds=0.,exit_qty=0.)
        self.validation_baseline=None
    @contextlib.contextmanager
    def settings(self):
        old_config=runner.CONFIG.copy();old_rules=v2.RULES.copy()
        runner.CONFIG.update(BASE_CONFIG,target_bps=self.target)
        v2.RULES.update(BASE_RULES,max_hold_seconds=self.hold)
        try:yield
        finally:
            runner.CONFIG.clear();runner.CONFIG.update(old_config)
            v2.RULES.clear();v2.RULES.update(old_rules)
    def exit_price(self, lot):
        price,reason=super().exit_price(lot)
        if reason=='passive_loss_or_time_exit':
            bid,ask=self.top(lot['market'])
            adverse=(((bid+ask)/2)/lot['entry']-1)*10000*(1 if lot['buy'] else -1)<=v2.RULES['adverse_exit_bps']*-1
            timed=self.clock()-lot['opened']>=v2.RULES['max_hold_seconds']
            reason='loss_and_timeout' if adverse and timed else 'loss' if adverse else 'timeout'
        return price,reason
    def event(self,kind,**kw):
        # Quote traffic is intentionally omitted; fills retain the parent and lot IDs.
        if kind in ('MAKER_ENTRY_FILL','MAKER_EXIT_FILL'):
            market=self.metrics['by_market'].setdefault(kw['market'],dict(realized_pnl=0.,entry_notional=0.,exit_notional=0.,exit_fills=0,closed_lots=0))
            notional=kw['price']*kw['qty']
            if kind=='MAKER_ENTRY_FILL':
                self.metrics['entry_notional']+=notional;market['entry_notional']+=notional
            else:
                self.metrics['exit_notional']+=notional;market['exit_notional']+=notional
                market['realized_pnl']+=kw['pnl'];market['exit_fills']+=1;market['closed_lots']+=int(kw['closed'])
                group=self.metrics['by_exit_reason'].setdefault(kw['reason'],dict(realized_pnl=0.,exit_fills=0,closed_lots=0,exit_notional=0.,holding_seconds_sum=0.))
                group['realized_pnl']+=kw['pnl'];group['exit_fills']+=1;group['closed_lots']+=int(kw['closed']);group['exit_notional']+=notional;group['holding_seconds_sum']+=kw['holding_seconds']
        if kind!='POST_ONLY_SUBMIT':
            with (self.directory/'events.jsonl').open('a') as f:
                f.write(json.dumps(dict(ts=self.clock(),variant=self.name,event=kind,**kw))+'\n')
    def observe(self):
        equity=1000+self.stats['realized_pnl']+self.pnl()
        self.metrics['equity_peak']=max(self.metrics['equity_peak'],equity)
        dd=self.metrics['equity_peak']-equity
        self.metrics['max_drawdown_usd']=max(self.metrics['max_drawdown_usd'],dd)
        self.metrics['max_drawdown_pct']=max(self.metrics['max_drawdown_pct'],dd/self.metrics['equity_peak']*100)
        inventory=sum(l['entry']*l['qty'] for l in self.lots.values())
        oldest=max((self.clock()-l['opened'] for l in self.lots.values()),default=0.)
        self.metrics['max_inventory_notional']=max(self.metrics['max_inventory_notional'],inventory)
        self.metrics['max_lot_age_seconds']=max(self.metrics['max_lot_age_seconds'],oldest)
        # Prespecified chronological confirmation window; no parameter retuning.
        if self.clock()-self.started>=12*3600 and self.validation_baseline is None:
            self.validation_baseline=dict(ts=self.clock(),equity=equity,realized_pnl=self.stats['realized_pnl'],closed_lots=self.stats['closed_lots'])
    def snapshot(self):
        d=copy.deepcopy(super().snapshot())
        d.update(variant=self.name,metrics=copy.deepcopy(self.metrics),validation_baseline=self.validation_baseline,
                 phase='confirmation' if self.validation_baseline else 'exploration',
                 oldest_open_lot_seconds=max((self.clock()-l['opened'] for l in self.lots.values()),default=0.),
                 overdue_open_lots=sum(self.clock()-l['opened']>=self.hold for l in self.lots.values()))
        for m,row in d['metrics']['by_market'].items():
            row['unrealized_pnl']=sum((self.last_mid.get(m,l['entry'])-l['entry'])*l['qty']*(1 if l['buy'] else -1) for l in self.lots.values() if l['market']==m)
            row['total_pnl']=row['realized_pnl']+row['unrealized_pnl']
        if self.validation_baseline:d['confirmation_pnl']=d['equity_before_funding']-self.validation_baseline['equity']
        return d
    def save(self):
        self.observe()
        runner.atomic(self.directory/'checkpoint.json',dict(started=self.started,lots=self.lots,stats=self.stats,next_id=self.next_id,halted=self.halted,last_mid=self.last_mid,metrics=self.metrics,validation_baseline=self.validation_baseline,target=self.target,hold=self.hold))
    def restore(self):
        path=self.directory/'checkpoint.json'
        if not path.exists():return
        d=json.loads(path.read_text())
        if (d['target'],d['hold'])!=(self.target,self.hold):raise RuntimeError('Do not mix parameter versions in one account')
        for key in ('started','lots','stats','next_id','halted','last_mid','metrics','validation_baseline'):setattr(self,key,d[key])
        for lot in self.lots.values():lot['exit_id']=None
        self.event('RESTART',note='Inventory retained; resting quotes and queues reset')

def publish(accounts):
    rows=[]
    for p in accounts:
        with p.settings():p.save();rows.append(p.snapshot())
    runner.atomic(PUBLIC,dict(updated_at=time.time(),mode='PAPER ONLY',variants=rows,
        design='Four separate $1,000 accounts receive the same public feed. Compare each candidate with the fresh baseline, not with the old overnight account.',
        decision_rule='First 12 hours exploratory; subsequent data confirmation with unchanged parameters. No automatic promotion. Compare equity P&L, drawdown, filled notional and overdue inventory. Short samples are provisional.',
        limitations=['Funding excluded; zero maker fees assumed at VIP5.','250ms latency and queue fills are modeled; market impact and liquidation are not simulated.','Trade count is partial-fill lots, not independent observations.','Exit reason is the rule used when the filled quote was placed.','Drawdown is sampled at feed-loop maintenance; intraloop extremes may be missed.']))

def main():
    filters=runner.metadata()
    accounts=[Experiment(*variant,filters) for variant in VARIANTS]
    now=time.time()
    for p in accounts:p.started=now;p.restore()
    stop=False
    def shutdown(*args):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    while not stop:
        w=None
        try:
            w=websocket.create_connection('wss://trade.api.yellow.pro/ws',timeout=10)
            w.send(json.dumps({'id':1,'connect':{}}));w.recv();i=2
            for m in runner.MARKETS:
                for channel in ('orderbook','trades'):
                    w.send(json.dumps({'id':i,'subscribe':{'channel':f'public.{channel}.increment.{m}'}}));i+=1
            w.settimeout(.5);last_save=0;last_message=time.time()
            for p in accounts:p.connected=True;p.error=None
            while not stop:
                try:
                    raw=w.recv()
                    if not raw:raise RuntimeError('socket closed')
                    last_message=time.time()
                    for line in raw.splitlines():
                        msg=json.loads(line)
                        if msg=={}:w.send('{}');continue
                        if 'error' in msg:raise RuntimeError(str(msg['error']))
                        d=msg.get('subscribe',{}).get('data') or msg.get('push',{}).get('pub',{}).get('data')
                        if d:
                            for p in accounts:
                                with p.settings():p.receive(d)
                except websocket.WebSocketTimeoutException:pass
                if time.time()-last_message>30:raise RuntimeError('Public feed silent for 30 seconds')
                for p in accounts:
                    with p.settings():p.maintain();p.observe()
                if time.time()-last_save>=5:publish(accounts);last_save=time.time()
        except Exception as e:
            for p in accounts:
                with p.settings():p.error=str(e);p.disconnect()
            publish(accounts);print('feed retry:',str(e),flush=True)
            time.sleep(3)
        finally:
            if w:w.close()
    for p in accounts:
        with p.settings():p.disconnect()
    publish(accounts)

if __name__=='__main__':main()
