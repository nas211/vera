"""Persistent, isolated paper ledger. Only this module creates simulated orders.

Every strategy version is frozen. One experiment per market/account prevents
double allocation of public trade volume to competing experimental orders.
"""
import copy
import datetime
import math
import json
import threading
import time
import uuid
from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from . import gate, rules, learning, bulk_bridge
from .market import Books

LATENCY=.25
INACTIVE_TEST_SECONDS=7200
TRADING_STATES=('TESTING','APPROVED_PAPER')
ACTIVE_VERSION_LIMIT=250
PAPER_TRIAL_LIMIT=128
# Observed public-feed outages: 76% recover within 30s, all within ~103s.
FEED_GRACE_SECONDS=120
# Observed stop exits overshoot by up to ~18bp; reserve that in the risk budget.
SLIP_RESERVE_BPS=20
from .fees import FEE_RATE, MODEL_VERSION, effective_net

RISK_PER_TRADE=2
MAX_NOTIONAL=500
TAKER_MAX_SLIP_BPS=3
MIN_ENTRY_FILL=.5
ACTIVE_STATES=('TESTING','APPROVED_PAPER','REVIEW_PENDING','REVIEW_DELAYED')
def uid():return uuid.uuid4().hex
def feed_interrupted(trade):
    reason=trade.get('invalid_reason') or trade.get('reason','')
    return trade.get('valid') is False and (reason in ('STALE_MARKET_DATA','EMPTY_ORDER_BOOK','RESTART_GAP') or reason.startswith('PUBLIC_'))
def round_to(value,step,up=False):
    return float((Decimal(str(value))/Decimal(str(step))).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*Decimal(str(step)))
def plan_entry(spec,bid,ask,limits):
    """Order price, stop and risk-budgeted size for one entry. Shared by engine and reviewer."""
    long=spec['side']=='long';spread_bps=(ask-bid)/((ask+bid)/2)*10000
    if rules.execution(spec)['entry']=='taker':
        reference=ask if long else bid
        price=round_to(reference*(1+TAKER_MAX_SLIP_BPS/10000*(1 if long else -1)),limits['tick'],up=long)
    else:reference=price=bid if long else ask
    stop=round_to(reference*(1-spec['stop_bps']/10000*(1 if long else -1)),limits['tick'],up=long)
    per_unit=abs(price-stop)+price*max(SLIP_RESERVE_BPS,3*spread_bps)/10000+(price+stop)*FEE_RATE
    qty=round_to(min(RISK_PER_TRADE/per_unit,MAX_NOTIONAL/price),limits['step'])
    return dict(price=price,stop=stop,qty=qty,planned_loss=qty*per_unit,spread_bps=spread_bps,entry_style=rules.execution(spec)['entry'])

class Engine:
    def __init__(self,store,clock=time.time):
        self.store=store;self.clock=clock;self.lock=threading.RLock();self.accounts=store.load();self.pending_archives=[]
        self.books=Books();self.markets={};self.events=[];self.error='WAITING_FOR_PUBLIC_FEED';self.ai_error='';self.ai_ready=False
        self.last_scan=0;self.scanned=set();self.dirty=False;self.rejects={};self.liquidity={};self.ai_model='gpt-5.6-luna'
        self.feed_events=0;self.last_feed_at=0.;self.last_feed_gap=None
        self.confirmed_snapshots=0;self.market_recoveries=0
        self.transport_status={}
        self.context_status=dict(ready=False,reason='WAITING_FOR_CONTEXT_CHECK')
        # Process restarts lose continuous market observation: never count those trades.
        for u,a in self.accounts.items():
            if a.get('archive') and 'risk_realized' not in a['archive']:
                archived=[json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM paper_archives WHERE user_id=?',(u,))]
                a['archive']['risk_realized']=sum(effective_net(t) for c in archived for t in c['trades'])
                a['archive']['risk_daily']={}
                for c in archived:
                    for t in c['trades']:
                        day=self.day(t['closed']);daily=a['archive']['risk_daily'];daily[day]=daily.get(day,0)+effective_net(t)
                self.dirty=True
            if a['kill'] and not a.get('risk_lock'):
                event=self.store.last_kill(u)
                if event and event.get('reason')=='DAILY_LOSS_LIMIT':
                    a['risk_lock']=dict(day=self.day(event['at']),reason='DAILY_LOSS_LIMIT');self.dirty=True
            for c in a['candidates'].values():
                c['entry']=None
                # Recheck legacy approvals under the positive-net screen before
                # enabling forward paper entries. Original trials stay intact.
                if c['state'] in ('REVIEW_PENDING','REVIEW_DELAYED','APPROVED_PAPER'):
                    score=rules.metrics(c['trades'])
                    if score['complete'] and not score['qualified']:
                        if c['position']:c['state']='SCREEN_RECHECK_PENDING'
                        else:self.retire(u,c,score)
                if c['state']=='SCREEN_FAILED' and not c['position']:
                    score=rules.metrics(c['trades'])
                    if score['complete'] and not score['qualified']:self.retire(u,c,score)
                if c['position']:
                    p=c['position']
                    if p.get('fee_model_version')!=MODEL_VERSION:
                        # Preserve completed history; estimate costs only for the
                        # still-open legacy position before its restart-gap exit.
                        fees=[dict(at=p['opened'],fee=p['initial_qty']*p['entry']*FEE_RATE,side='entry')]
                        if p.get('exit_value',0):
                            fees.append(dict(at=max([p['opened']]+[f['at'] for f in p.get('exit_fills',[])]),fee=p['exit_value']*FEE_RATE,side='legacy_exits_estimated'))
                        p.update(fee_events=fees,fees_paid=sum(f['fee'] for f in fees),fee_model_version=MODEL_VERSION,fee_migration='estimated_legacy_open_fills')
                        self.log(u,c['chain'],'fee_migration',dict(fee_model_version=MODEL_VERSION,fees_paid=p['fees_paid']))
                    c['position']['valid']=False;c['position']['invalid_reason']='RESTART_GAP'
                    c['position']['target']=None;c['position']['exit_reason']='RESTART_GAP';c['position']['cancel_at']=self.clock()+LATENCY
                    self.log(u,c['chain'],'recovery',dict(reason='RESTART_GAP',mode='paper'))
        self.commit()
    def log(self,user,chain,stage,data):
        self.events.append(dict(user=user,chain=chain,ts=self.clock(),stage=stage,data=data));self.dirty=True
    def commit(self):
        if self.dirty or self.events:
            self.store.save(self.accounts,self.events,self.pending_archives);self.events=[];self.pending_archives=[];self.dirty=False
    def account(self,u):
        if u not in self.accounts:
            self.accounts[u]=dict(running=False,kill=False,candidates={},orders=[],created=self.clock())
            self.dirty=True
        return self.accounts[u]
    def action(self,u,action,candidate=None):
        with self.lock:
            a=self.account(u)
            if action=='start':
                if a['kill']:raise ValueError('PAPER_DAILY_LOSS_LOCKED')
                a['running']=True
            elif action=='pause':
                a['running']=False
                for c in a['candidates'].values():self.cancel_entry(u,c,'PAUSED')
            elif action=='close':
                c=a['candidates'].get(candidate)
                if not c or not c['position']:raise ValueError('POSITION_NOT_FOUND')
                self.begin_exit(u,c,'OWNER_CLOSE',self.clock())
            else:raise ValueError('UNKNOWN_ACTION')
            self.log(u,uid(),'owner',dict(action=action,mode='paper'));self.commit()
            return self.state(u)
    def propose(self,u,spec,evidence,parent=None):
        with self.lock:
            spec=rules.validate(spec,self.markets);a=self.account(u);cid=rules.identity(spec)
            fingerprint=rules.executable_identity(spec)
            if self.known_spec(u,spec):return None
            previous=a['candidates'].get(parent) if parent else None
            if parent:
                if not previous or previous['state']!='RETIRED' or previous.get('revision',{}).get('child'):raise ValueError('INVALID_REVISION_PARENT')
                if rules.same_settings(spec,previous['spec']):raise ValueError('REVISION_SETTINGS_UNCHANGED')
                if (spec['market'],spec['mode'])!=(previous['spec']['market'],previous['spec']['mode']):raise ValueError('REVISION_MARKET_OR_MODE_CHANGED')
            # Budget active experiments; completed immutable records remain visible.
            if sum(c['state']=='TESTING' for c in a['candidates'].values())>=PAPER_TRIAL_LIMIT:return None
            if len(a['candidates'])>=ACTIVE_VERSION_LIMIT:self.archive_completed(u,exclude=parent)
            if len(a['candidates'])>=ACTIVE_VERSION_LIMIT:return None
            c=dict(id=cid,version=cid[:12],spec=spec,created=self.clock(),state='TESTING',chain=uid(),entry=None,position=None,trades=[],review={},last_entry=0)
            c['lineage']=dict(root=previous.get('lineage',{}).get('root',parent) if previous else cid,parent=parent,generation=previous.get('lineage',{}).get('generation',1)+1 if previous else 1)
            a['candidates'][cid]=c
            if previous:previous['revision'].update(status='CREATED',child=cid,completed_at=self.clock())
            self.log(u,c['chain'],'strategy',dict(version=c['version'],spec=spec,evidence=evidence,origin='AI_REVISED' if parent else 'AI_GENERATED',parent_version=previous['version'] if previous else None,mode='paper'))
            self.commit();return cid
    def known_spec(self,u,spec):
        fingerprint=rules.executable_identity(spec)
        return any(rules.executable_identity(c['spec'])==fingerprint for c in self.accounts[u]['candidates'].values()) or self.store.archived_duplicate(u,rules.identity(spec),fingerprint)
    def retire(self,u,c,score,reason='PAPER_SCREEN_FAILED',phase='PAPER_SCREEN'):
        c['state']='RETIRED'
        c['retirement']=dict(at=self.clock(),reason=reason,phase=phase,score=copy.deepcopy(score))
        c['revision']=dict(status='PENDING',last_attempt=0,reason='Awaiting failure analysis and changed settings.')
        self.log(u,c['chain'],'retired',dict(version=c['version'],**c['retirement']))
    @staticmethod
    def day(now):return datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date().isoformat()
    def archive_completed(self,u,exclude=None):
        a=self.accounts[u]
        terminal=lambda c:c['state'] in ('EXPIRED','REVIEW_REJECTED') or (c['state']=='RETIRED' and c.get('revision',{}).get('status') in ('CREATED','NO_CHANGE','NO_NEW_VERSION'))
        eligible=sorted([c for c in a['candidates'].values() if c['id']!=exclude and terminal(c) and not c['entry'] and not c['position']],key=lambda c:(c['created'],c['id']))
        totals=a.setdefault('archive',dict(count=0,realized=0.,display_realized=0.,feed_excluded=0,daily={}))
        totals.setdefault('risk_realized',totals['realized']);totals.setdefault('risk_daily',dict(totals['daily']))
        for c in eligible[:max(0,len(a['candidates'])-ACTIVE_VERSION_LIMIT+25)]:
            totals['count']+=1;totals['realized']+=sum(t['net'] for t in c['trades'])
            totals['risk_realized']+=sum(effective_net(t) for t in c['trades'])
            totals['display_realized']+=sum(t['net'] for t in c['trades'] if not feed_interrupted(t))
            totals['feed_excluded']+=sum(feed_interrupted(t) for t in c['trades'])
            for t in c['trades']:
                day=self.day(t['closed']);totals['daily'][day]=totals['daily'].get(day,0)+t['net']
                totals['risk_daily'][day]=totals['risk_daily'].get(day,0)+effective_net(t)
            self.pending_archives.append((u,copy.deepcopy(c),learning.summary(c),self.clock()))
            del a['candidates'][c['id']]
            self.log(u,c['chain'],'archived',dict(version=c['version'],reason='ACTIVE_MEMORY_CAPACITY',history_preserved=True))
    def research_memory(self,u,markets):
        return learning.memory(self.accounts[u]['candidates'].values(),self.store.archived_summaries(u,100),markets)
    def archives(self,u,offset=0):
        with self.lock:
            self.commit();rows=self.store.archives(u,offset,25)
            return dict(mode='paper',live_enabled=False,items=rows,offset=offset,next_offset=offset+len(rows) if len(rows)==25 else None)
    def expire_inactive(self,u,now):
        a=self.accounts[u]
        if not a['running'] or a['kill']:return
        for c in a['candidates'].values():
            if c['state']!='TESTING' or c['position'] or not self.books.fresh(c['spec']['market'],now):continue
            # Offered/unfilled orders do not count as experimental progress.
            progress=max([c['created']]+[t['closed'] for t in c['trades'] if t.get('valid',True) and t.get('reason')!='OWNER_CLOSE'])
            deadline=max(INACTIVE_TEST_SECONDS,2*c['spec']['max_hold_seconds'])
            if now-progress<deadline:continue
            self.cancel_entry(u,c,'INACTIVE_EXPERIMENT')
            c['state']='EXPIRED'
            c['expiry']=dict(at=now,reason='NO_VALID_TRIAL_PROGRESS',inactive_seconds=now-progress,limit_seconds=deadline)
            self.log(u,c['chain'],'expired',dict(version=c['version'],**c['expiry']))
    def retire_bad_geometry(self,u):
        # Versions created before the reward/risk rule retire once flat; the
        # revision agent may then propose a compliant child version.
        for c in self.accounts[u]['candidates'].values():
            if c['state'] in ACTIVE_STATES and not c['position'] and not c['entry'] and not rules.geometry_ok(c['spec']):
                self.retire(u,c,rules.metrics(c['trades']),'REWARD_RISK_RULE')
    def gap(self,reason):
        self.error=reason;self.books.reset();self.liquidity.clear()
        for u,a in self.accounts.items():
            for c in a['candidates'].values():
                self.cancel_entry(u,c,reason)
                if c['position']:self.feed_gap(u,c,reason,self.clock())
        self.commit()
    def feed_gap(self,u,c,reason,now):
        """Hold through short outages; the exit only executes on recovered data anyway."""
        p=c['position']
        if p['exit_reason']:return
        gap=p.get('feed_gap')
        if not gap:
            p['feed_gap']=dict(since=now,reason=reason);self.dirty=True
            self.log(u,c['chain'],'feed_gap_hold',dict(reason=reason,grace_seconds=FEED_GRACE_SECONDS,mode='paper'))
        elif now-gap['since']>=FEED_GRACE_SECONDS:
            p['valid']=False;p['invalid_reason']=gap['reason'];self.begin_exit(u,c,gap['reason'],now)
    def feed_recovered(self,u,c,now):
        p=c['position'];gap=p.pop('feed_gap',None)
        if not gap:return
        p['feed_gap_seconds']=p.get('feed_gap_seconds',0)+now-gap['since'];self.dirty=True
        self.log(u,c['chain'],'feed_gap_recovered',dict(reason=gap['reason'],seconds=now-gap['since'],mode='paper'))
    def market_gap(self,m,reason):
        self.books.forget(m);self.market_recoveries+=1
        self.last_feed_gap=dict(at=self.clock(),market=m,error=reason,scope='market')
        for u,a in self.accounts.items():
            for c in a['candidates'].values():
                if c['spec']['market']!=m:continue
                self.cancel_entry(u,c,reason)
                if c['position']:self.feed_gap(u,c,reason,self.clock())
        self.commit()
    def receive(self,kind,data,now):
        with self.lock:
            received_at=now
            now=self.clock() # Processing lag never makes stale data fresh.
            if kind=='universe':self.markets=data
            elif kind=='transport_status':self.transport_status=data
            elif kind=='bars':self.books.seed(data[0],data[1],now)
            elif kind=='confirmation':
                try:
                    if self.books.confirm(data,received_at):self.confirmed_snapshots+=1
                except (ValueError,KeyError,TypeError,OverflowError):pass
            elif kind=='market_gap':self.market_gap(data['market'],data['reason'])
            elif kind=='drop':
                for m in data:
                    self.books.books.pop(m,None);self.books.marks.pop(m,None)
                    self.books.seq.pop((m,'book'),None);self.books.seq.pop((m,'trades'),None)
            elif kind=='gap':
                self.last_feed_gap=dict(at=now,error=data)
                self.gap(data)
            elif kind=='data':
                self.feed_events+=1;self.last_feed_at=received_at
                try:trades=self.books.receive(data,received_at)
                except (ValueError,KeyError,TypeError,OverflowError) as ex:
                    self.last_feed_gap=dict(at=now,market=data.get('market'),type=data.get('header',{}).get('type'),error=type(ex).__name__)
                    self.market_gap(data.get('market'),'PUBLIC_MARKET_SEQUENCE_OR_SCHEMA_GAP');return False
                m=data.get('market')
                if self.books.fresh(m,now):
                    self.error=''
                    for t in trades:
                        if not 0<=now-t['ts']<=5:continue
                        for u,a in self.accounts.items():
                            for c in a['candidates'].values():
                                if c['spec']['market']==m:self.maker_trade(u,c,t,now)
            self.tick(now);return True
    def daily(self,a,now):
        day=datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date()
        completed=sum(effective_net(t) for c in a['candidates'].values() for t in c['trades'] if datetime.datetime.fromtimestamp(t['closed'],datetime.timezone.utc).date()==day)
        partial=sum(t['gross'] for c in a['candidates'].values() if c['position'] for t in c['position'].get('exit_fills',[]) if datetime.datetime.fromtimestamp(t['at'],datetime.timezone.utc).date()==day)
        fees=sum(f['fee'] for c in a['candidates'].values() if c['position'] for f in c['position'].get('fee_events',[]) if datetime.datetime.fromtimestamp(f['at'],datetime.timezone.utc).date()==day)
        return completed+partial-fees+a.get('archive',{}).get('risk_daily',a.get('archive',{}).get('daily',{})).get(day.isoformat(),0)
    def account_risk(self,a,m,side,now):
        total=coin=side_total=0.;orders=0;position_qty=0.;realized=a.get('archive',{}).get('risk_realized',a.get('archive',{}).get('realized',0.));unrealized=0.
        for c in a['candidates'].values():
            s=c['spec'];p=c['position'];e=c['entry'];n=0.
            realized+=sum(effective_net(t) for t in c['trades'])
            if p:
                realized+=p['gross']-p.get('fees_paid',0)
                n+=p['qty']*p['entry'];orders+=bool(p['target'])
                if s['market']==m:position_qty+=p['qty']
                b=self.books.books.get(s['market'])
                if b and b['bids'] and b['asks']:unrealized+=(max(b['bids'])-p['entry'])*p['qty'] if s['side']=='long' else (p['entry']-min(b['asks']))*p['qty']
            if e:n+=e['remaining']*e['price'];orders+=1
            total+=n
            if s['market']==m:coin+=n
            if s['side']==side:side_total+=n
        a['orders']=[t for t in a['orders'] if now-t<60]
        return gate.Account(equity=25000+realized+unrealized,total=total,coin=coin,side=side_total,open_orders=orders,
            daily_net=self.daily(a,now)+unrealized,kill=a['kill'],recent_orders=len(a['orders']),position_qty=position_qty)
    def check(self,u,c,intent,now):
        m=intent.market;b=self.books.books.get(m);limits=self.markets[m]
        risk=self.account_risk(self.accounts[u],m,intent.side,now)
        decision=gate.decide(intent,risk,markets=set(self.markets),mid=(max(b['bids'])+min(b['asks']))/2 if b and b['bids'] and b['asks'] else 0,
            age=max(now-b['received'],now-b['source']) if b else 1e9,**{k:limits[k] for k in ('min_qty','step','min_notional')})
        if decision.trip_kill:self.accounts[u]['kill']=True;self.dirty=True
        key=(u,c['id'],decision.reject_reason)
        if decision.approved or now-self.rejects.get(key,0)>30:
            self.log(u,intent.chain_id,'intent',asdict(intent))
            self.log(u,intent.chain_id,'gate',asdict(decision)) # Shadow receives rejected AND approved.
            self.rejects[key]=now
        return decision
    def paper_order(self,u,c,i,d,now,kind):
        if not gate.accepts(i,d) or i.mode!='paper':raise ValueError('UNAPPROVED_INTENT')
        b=self.books.books[i.market];side='buy' if i.side=='long' else 'sell'
        if kind not in ('IOC','ENTRY_IOC') and ((side=='buy' and i.price>=min(b['asks'])) or (side=='sell' and i.price<=max(b['bids']))):
            self.log(u,i.chain_id,'post_only_reject',dict(intent=i.id));return None
        levels=b['bids'] if side=='buy' else b['asks']
        order=dict(id=i.id,chain=i.chain_id,side=side,price=i.price,remaining=i.qty,qty=i.qty,ready=now+LATENCY,created=now,
            ahead=levels.get(i.price,0),kind=kind,reduce_only=i.reduce_only,stop=i.stop)
        self.accounts[u]['orders'].append(now)
        self.log(u,i.chain_id,'order',dict(**order,mode='paper'));return order
    def cancel_entry(self,u,c,reason):
        if c['entry']:
            self.log(u,c['chain'],'cancel',dict(id=c['entry']['id'],reason=reason,mode='paper'));c['entry']=None
    def tick(self,now=None):
        now=self.clock() if now is None else now
        with self.lock:
            for u,a in self.accounts.items():
                lock=a.get('risk_lock',{})
                if a['kill'] and lock.get('reason')=='DAILY_LOSS_LIMIT' and lock.get('day')!=self.day(now):
                    a['kill']=False;a.pop('risk_lock',None);self.log(u,uid(),'daily_lock_expired',dict(previous_day=lock['day'],day=self.day(now)))
                if self.account_risk(a,'','long',now).daily_net<=-20 and not a['kill']:
                    a['kill']=True;a['risk_lock']=dict(day=self.day(now),reason='DAILY_LOSS_LIMIT');self.log(u,uid(),'kill',dict(reason='DAILY_LOSS_LIMIT'))
                self.expire_inactive(u,now)
                self.retire_bad_geometry(u)
                # Clear expired reservations before selecting the next contender.
                for c in a['candidates'].values():
                    if c['entry'] and (now-c['entry']['created']>=15 or not a['running'] or a['kill']):
                        o=c['entry'];self.cancel_entry(u,c,'ENTRY_EXPIRED_OR_PAUSED');self.finish_entry(u,c,o,now)
                # Least recently offered an entry goes first; expired/unfilled
                # orders also yield the next turn to another eligible strategy.
                for c in sorted(a['candidates'].values(),key=lambda c:(c['last_entry'],c['created'],c['id'])):
                    s=c['spec'];m=s['market'];p=c['position']
                    issue=self.books.issue(m,now)
                    if p and issue:self.feed_gap(u,c,issue,now)
                    if issue or m not in self.markets:
                        self.cancel_entry(u,c,issue or 'MARKET_UNAVAILABLE');continue
                    b=self.books.books[m];bid=max(b['bids']);ask=min(b['asks'])
                    if c['entry'] and c['entry']['kind']=='ENTRY_IOC':
                        if now>=c['entry']['ready']:self.entry_ioc(u,c,now)
                        continue
                    if p:
                        self.feed_recovered(u,c,now)
                        self.observe_position(c,now)
                        if not p['exit_reason']:self.manage_stop(u,c,bid,ask)
                        hit=(bid<=p['stop']) if s['side']=='long' else (ask>=p['stop'])
                        if hit:self.begin_exit(u,c,'STOP',now)
                        elif now-p['opened']>=s['max_hold_seconds']:self.begin_exit(u,c,'MAX_HOLD',now)
                        elif now>=p['funding_deadline']-10:self.begin_exit(u,c,'BEFORE_FUNDING',now)
                        elif a['kill']:self.begin_exit(u,c,'DAILY_LOSS_LIMIT',now)
                        if now>=p['funding_deadline']:
                            p['valid']=False;p['invalid_reason']='FUNDING_BOUNDARY_CROSSED';self.dirty=True
                        if p['exit_reason']:
                            if now>=p['cancel_at']:self.ioc(u,c,now)
                        elif not c['entry'] and not p['target']:
                            target=round_to(p['entry']*(1+s['target_bps']/10000*(1 if s['side']=='long' else -1)),self.markets[m]['tick'],up=s['side']=='long')
                            i=gate.Intent(uid(),c['chain'],m,'short' if s['side']=='long' else 'long',p['qty'],target,p['stop'],reduce_only=True)
                            d=self.check(u,c,i,now)
                            if d.approved:p['target']=self.paper_order(u,c,i,d,now,'TARGET')
                        continue
                    if c['entry'] or not a['running'] or a['kill'] or c['state'] not in TRADING_STATES or now-c['last_entry']<10:continue
                    if any(x['id']!=c['id'] and x['spec']['market']==m and (x['entry'] or x['position']) for x in a['candidates'].values()):continue
                    f=self.books.features(m,now)
                    if not f or not rules.triggered(s,f):continue
                    funding=self.books.marks.get(m)
                    if not funding or not 0<=now-funding['source']<=30 or funding['next']<=now+s['max_hold_seconds']+30:continue
                    # Leave idle subscription capacity for continued discovery.
                    occupied={x['spec']['market'] for account in self.accounts.values() for x in account['candidates'].values() if x['entry'] or x['position']}
                    if len(occupied)>=24 and m not in occupied:continue
                    c['chain']=uid();c['last_entry']=now
                    plan=plan_entry(s,bid,ask,self.markets[m])
                    self.log(u,c['chain'],'signal',dict(version=c['version'],features=f,experimental_side=s['side'],phase='FORWARD_PAPER' if c['state']=='APPROVED_PAPER' else 'PAPER_SCREEN'))
                    self.log(u,c['chain'],'size',plan)
                    i=gate.Intent(uid(),c['chain'],m,s['side'],plan['qty'],plan['price'],plan['stop']);d=self.check(u,c,i,now)
                    if d.approved:
                        c['entry']=self.paper_order(u,c,i,d,now,'ENTRY_IOC' if plan['entry_style']=='taker' else 'ENTRY')
                        if c['entry']:c['entry']['diagnostics']=dict(signal_at=now,features=copy.deepcopy(f),signal_bid=bid,signal_ask=ask,entry_style=plan['entry_style'])
            self.commit()
    def observe_position(self,c,now,price=None):
        p=c['position'];d=p.get('diagnostics') if p else None
        if d is None:return
        b=self.books.books.get(c['spec']['market'])
        if price is None:
            if not self.books.fresh(c['spec']['market'],now):return
            price=max(b['bids']) if c['spec']['side']=='long' else min(b['asks'])
        observation=[now,price,p['entry']]
        if d.get('last_observation')==observation:return
        d['last_observation']=observation
        move=(price/p['entry']-1)*10000*(1 if c['spec']['side']=='long' else -1)
        d['mfe_bps']=max(d['mfe_bps'],move);d['mae_bps']=min(d['mae_bps'],move)
        d['observations']+=1;d['max_observation_gap_seconds']=max(d['max_observation_gap_seconds'],now-d['last_observed_at']);d['last_observed_at']=now
        if not d['path'] or now-d['path'][-1]['at']>=d['sample_interval_seconds']:
            if len(d['path'])>=60:d['path']=d['path'][::2];d['sample_interval_seconds']*=2
            d['path'].append(dict(at=now,exit_side_price=price,move_bps=move));self.dirty=True
    def maker_trade(self,u,c,t,now):
        p=c['position'];e=c['entry'] if c['entry'] and c['entry']['kind']=='ENTRY' else None
        o=e or (p['target'] if p and not p['exit_reason'] else None)
        if not o or now<o['ready'] or t['ts']<o['ready']:return
        crosses=(t['side']=='sell' and t['price']<=o['price']) if o['side']=='buy' else (t['side']=='buy' and t['price']>=o['price'])
        if not crosses:return
        available=t['qty'];consumed=min(o['ahead'],available);o['ahead']-=consumed;available-=consumed
        q=round_to(min(o['remaining'],available),self.markets[c['spec']['market']]['step'])
        self.dirty=True
        if q<=0:return
        o['remaining']=round_to(max(0,o['remaining']-q),self.markets[c['spec']['market']]['step'])
        if o['kind']=='ENTRY':
            self.entry_fill(c,o,q,o['price'],now)
            if o['remaining']<=0:c['entry']=None;self.finish_entry(u,c,o,now)
        else:self.exit_fill(u,c,q,o['price'],now,'TARGET')
        self.log(u,o['chain'],'fill',dict(order_id=o['id'],qty=q,price=o['price'],liquidity='maker',public_trade=t['id'],mode='paper'))
    def entry_fill(self,c,o,q,price,now):
        p=c['position']
        if p:
            p['entry']=(p['entry']*p['qty']+price*q)/(p['qty']+q);p['qty']+=q;p['initial_qty']+=q
        else:
            c['position']=dict(qty=q,initial_qty=q,ordered_qty=o['qty'],entry=price,stop=o['stop'],opened=now,target=None,exit_reason='',cancel_at=0,
                exit_order=None,valid=True,invalid_reason='',gross=0.,exit_value=0.,closed_qty=0.,triggered_at=None,sent_at=None,
                funding_deadline=self.books.marks[c['spec']['market']]['next'],exit_fills=[],phase='FORWARD_PAPER' if c['state']=='APPROVED_PAPER' else 'PAPER_SCREEN')
            c['position']['diagnostics']=dict(o.get('diagnostics',{}),first_fill_at=now,
                entry_wait_seconds=now-o['created'],entry_spread_bps=(self.books.features(c['spec']['market'],now) or {}).get('spread_bps'),
                mfe_bps=0.,mae_bps=0.,observations=0,last_observed_at=now,max_observation_gap_seconds=0.,path=[],sample_interval_seconds=5.,
                interpretation='Observed liquidation-side prices relative to weighted entry at each observation. Sparse path, not continuous market coverage.')
        p=c['position']; fee=q*price*FEE_RATE
        p['fees_paid']=p.get('fees_paid',0)+fee
        p.setdefault('fee_events',[]).append(dict(at=now,fee=fee,side='entry'))
        p['fee_model_version']=MODEL_VERSION
        self.cap_stop(c)
        self.observe_position(c,now)
    def cap_stop(self,c):
        p=c['position'];long=c['spec']['side']=='long';q=p['qty']
        if q<=0:return
        available=2+p['gross']-p.get('fees_paid',0)
        bound=(p['entry']-available/q)/(1-FEE_RATE) if long else (p['entry']+available/q)/(1+FEE_RATE)
        stop=max(p['stop'],bound) if long else min(p['stop'],bound)
        p['stop']=round_to(stop,self.markets[c['spec']['market']]['tick'],up=long)
    def finish_entry(self,u,c,o,now):
        # A fragment of the planned size is not a fair trial of the strategy; flatten it uncounted.
        p=c['position']
        if not p or p['exit_reason'] or o['kind'] not in ('ENTRY','ENTRY_IOC'):return
        p['diagnostics']['entry_fill_ratio']=p['initial_qty']/o['qty']
        if p['initial_qty']<MIN_ENTRY_FILL*o['qty']-1e-12:
            p['valid']=False;p['invalid_reason']='PARTIAL_ENTRY';self.begin_exit(u,c,'PARTIAL_ENTRY',now)
    def entry_ioc(self,u,c,now):
        o=c['entry'];m=c['spec']['market'];b=self.books.books[m];buy=o['side']=='buy';step=self.markets[m]['step']
        used=self.liquidity.setdefault((u,m,b['seq']),{})
        for price,depth in sorted((b['asks'] if buy else b['bids']).items(),reverse=not buy):
            if (buy and price>o['price']) or (not buy and price<o['price']):break
            q=round_to(min(o['remaining'],max(0,depth-used.get(price,0))),step)
            if q<=0:continue
            used[price]=used.get(price,0)+q;o['remaining']=round_to(max(0,o['remaining']-q),step)
            self.entry_fill(c,o,q,price,now)
            self.log(u,o['chain'],'fill',dict(order_id=o['id'],qty=q,price=price,liquidity='taker',mode='paper'))
            if o['remaining']<=0:break
        c['entry']=None;self.dirty=True
        if not c['position']:self.log(u,o['chain'],'cancel',dict(id=o['id'],reason='IOC_UNFILLED',mode='paper'))
        else:self.finish_entry(u,c,o,now)
        self.liquidity={k:v for k,v in self.liquidity.items() if k[1]!=m or k[2]==b['seq']}
    def manage_stop(self,u,c,bid,ask):
        """Per-strategy breakeven and trailing stops; the stop only ever tightens."""
        x=rules.execution(c['spec']);p=c['position'];long=c['spec']['side']=='long'
        if not (x['breakeven_bps'] or x['trail_bps']):return
        price=bid if long else ask;tick=self.markets[c['spec']['market']]['tick']
        move=(price/p['entry']-1)*10000*(1 if long else -1);best=p['best_price']=max(p.get('best_price',price),price) if long else min(p.get('best_price',price),price)
        stops=[]
        if x['breakeven_bps'] and move>=x['breakeven_bps']:
            gain=(p.get('fees_paid',0)-p['gross'])/p['qty']
            breakeven=(p['entry']+gain)/(1-FEE_RATE) if long else (p['entry']-gain)/(1+FEE_RATE)
            stops.append(round_to(breakeven,tick,up=long))
        if x['trail_bps'] and (best/p['entry']-1)*10000*(1 if long else -1)>=x['trail_bps']:
            stops.append(round_to(best*(1-x['trail_bps']/10000*(1 if long else -1)),tick,up=long))
        for stop in stops:
            if (long and stop>p['stop']) or (not long and stop<p['stop']):
                self.log(u,c['chain'],'stop_moved',dict(previous=p['stop'],stop=stop,reason='BREAKEVEN' if stop==p['entry'] else 'TRAIL',mode='paper'))
                p['stop']=stop;self.dirty=True
    def begin_exit(self,u,c,reason,now):
        p=c['position']
        if not p:return
        if reason=='OWNER_CLOSE':p['valid']=False;p['invalid_reason']='OWNER_CLOSE'
        if p['exit_reason']:
            if reason=='OWNER_CLOSE':p['exit_reason']=reason
            return
        self.cancel_entry(u,c,reason)
        self.log(u,c['chain'],'exit_trigger',dict(reason=reason,triggered_at=now,target_order=p['target']['id'] if p['target'] else None,
            market_data=self.feed_status(now)['books'].get(c['spec']['market'])))
        p['exit_reason']=reason;p['triggered_at']=now;p['cancel_at']=now+LATENCY;p['target']=None
        self.dirty=True
    def ioc(self,u,c,now):
        p=c['position'];m=c['spec']['market'];b=self.books.books[m];long=c['spec']['side']=='long'
        if not p['exit_order']:
            # Cancel acknowledgement is simulated before submitting the replacement IOC.
            self.log(u,c['chain'],'cancel_confirmed',dict(at=now,mode='paper'))
            price=max(b['bids']) if long else min(b['asks'])
            i=gate.Intent(uid(),c['chain'],m,'short' if long else 'long',p['qty'],price,p['stop'],reduce_only=True)
            d=self.check(u,c,i,now)
            if not d.approved:return
            p['exit_order']=self.paper_order(u,c,i,d,now,'IOC');p['sent_at']=now
            self.log(u,c['chain'],'exit_send',dict(at=now,triggered_at=p['triggered_at']))
            return
        o=p['exit_order']
        if now<o['ready']:return
        side='bids' if long else 'asks';levels=sorted(b[side].items(),reverse=long)
        liquidity_key=(u,m,b['seq'])
        used=self.liquidity.setdefault(liquidity_key,{})
        for price,depth in levels:
            # A reduce-only IOC is price bounded; retry the remaining size at the next book.
            if (long and price<o['price']) or (not long and price>o['price']):break
            qty=round_to(min(p['qty'],max(0,depth-used.get(price,0))),self.markets[m]['step'])
            if qty<=0:continue
            used[price]=used.get(price,0)+qty
            self.log(u,c['chain'],'fill',dict(order_id=o['id'],qty=qty,price=price,liquidity='taker',mode='paper',triggered_at=p['triggered_at'],sent_at=p['sent_at'],filled_at=now))
            self.exit_fill(u,c,qty,price,now,p['exit_reason'])
            if not c['position']:break
        if c['position']:p['exit_order']=None;p['cancel_at']=now+LATENCY;self.dirty=True
        self.liquidity={k:v for k,v in self.liquidity.items() if k[1]!=m or k[2]==b['seq']}
    def exit_fill(self,u,c,qty,price,now,reason):
        p=c['position'];sign=1 if c['spec']['side']=='long' else -1
        self.observe_position(c,now,price)
        fee=price*qty*FEE_RATE
        p['fees_paid']=p.get('fees_paid',0)+fee
        p.setdefault('fee_events',[]).append(dict(at=now,fee=fee,side='exit'))
        p['gross']+=(price-p['entry'])*qty*sign;p['exit_value']+=price*qty;p['closed_qty']+=qty
        p.setdefault('exit_fills',[]).append(dict(at=now,gross=(price-p['entry'])*qty*sign))
        p['qty']=round_to(max(0,p['qty']-qty),self.markets[c['spec']['market']]['step']);self.dirty=True
        if p['qty']>0:
            self.cap_stop(c)
            return
        t=dict(id=uid(),chain_id=c['chain'],opened=p['opened'],closed=now,qty=p['closed_qty'],entry=p['entry'],exit=p['exit_value']/p['closed_qty'],
            net=p['gross']-p.get('fees_paid',0),fees=p.get('fees_paid',0),fee_model_version=MODEL_VERSION,funding=0. if now<p['funding_deadline'] else None,reason=reason,valid=p['valid'],invalid_reason=p['invalid_reason'],mode='paper',
            triggered_at=p['triggered_at'],sent_at=p['sent_at'],filled_at=now,latency_seconds=now-p['triggered_at'] if p['triggered_at'] is not None else None)
        t['phase']=p.get('phase','PAPER_SCREEN');t['feed_gap_seconds']=p.get('feed_gap_seconds',0)
        if p.get('diagnostics'):
            t['diagnostics']=copy.deepcopy(p['diagnostics']);t['diagnostics'].update(hold_seconds=now-p['opened'],exit_fill_at=now,exit_reason=reason)
        c['trades'].append(t);c['position']=None;c['entry']=None
        score=rules.metrics(c['trades'])
        if t['phase']=='FORWARD_PAPER':
            eligible=[x for x in c['trades'] if x.get('phase')=='FORWARD_PAPER' and x.get('valid',True) and x.get('reason')!='OWNER_CLOSE']
            forward=c.setdefault('forward',dict(evaluated=0,batches=[]))
            if len(eligible)-forward['evaluated']>=10:
                batch=eligible[forward['evaluated']:forward['evaluated']+10]
                result=rules.metrics(batch);forward['evaluated']+=10
                forward['batches'].append(dict(at=now,score=result,trade_ids=[x['id'] for x in batch]))
                self.log(u,c['chain'],'forward_review',dict(version=c['version'],score=result))
                if not result['qualified']:self.retire(u,c,result,'FORWARD_PAPER_FAILED','FORWARD_PAPER')
            c['validation']=learning.validation(c['trades'])
            if c['validation']['max_drawdown']>learning.FORWARD_MAX_DRAWDOWN and c['state']=='APPROVED_PAPER':
                self.retire(u,c,c['validation'],'FORWARD_DRAWDOWN_LIMIT','FORWARD_PAPER')
            elif c['validation']['status']=='NOT_QUALIFIED' and c['state']=='APPROVED_PAPER':
                self.retire(u,c,c['validation'],'FORWARD_VALIDATION_FAILED','FORWARD_PAPER')
        elif score['complete']:
            if score['qualified']:c['state']='REVIEW_PENDING'
            else:self.retire(u,c,score)
        self.log(u,c['chain'],'closed',dict(trade=t,score=score,state=c['state']))
    def feed_status(self,now=None):
        with self.lock:
            now=self.clock() if now is None else now
            books={m:dict(fresh=self.books.fresh(m,now),issue=self.books.issue(m,now),source_age=round(now-b['source'],3),
                received_age=round(now-b['received'],3),bids=len(b['bids']),asks=len(b['asks']),sequence=b['seq'])
                for m,b in self.books.books.items()}
            return dict(events=self.feed_events,last_event_age=round(now-self.last_feed_at,3) if self.last_feed_at else None,
                fresh_markets=sum(b['fresh'] for b in books.values()),markets=len(self.markets),books=books,last_gap=self.last_feed_gap,
                candles={m:dict(count=len(rows),last_closed_age=round(now-rows[-1][0]-300,3) if rows else None)
                         for m,rows in self.books.bars.items()},confirmed_snapshots=self.confirmed_snapshots,market_recoveries=self.market_recoveries,transport=self.transport_status)
    def state(self,u):
        with self.lock:
            a=self.account(u);now=self.clock();self.commit();candidates=[];net=a.get('archive',{}).get('display_realized',0.);unrealized=0.;archived=a.get('archive',{}).get('feed_excluded',0)
            for c in a['candidates'].values():
                visible=[t for t in c['trades'] if not feed_interrupted(t)]
                archived+=len(c['trades'])-len(visible)
                item=copy.deepcopy(c);item['score']=rules.metrics([t for t in visible if t.get('phase')!='FORWARD_PAPER']);item['trades']=visible[-25:]
                forward=[t for t in visible if t.get('phase')=='FORWARD_PAPER' and t.get('valid',True) and t.get('reason')!='OWNER_CLOSE']
                item['forward_score']=dict(closed=len(forward),wins=sum(t['net']>0 for t in forward),net=sum(t['net'] for t in forward))
                item['validation']=learning.validation(visible)
                p=item['position'];b=self.books.books.get(c['spec']['market'])
                item['unrealized']=None
                if p and not feed_interrupted(p) and self.books.fresh(c['spec']['market'],now):
                    item['unrealized']=((max(b['bids'])-p['entry']) if c['spec']['side']=='long' else (p['entry']-min(b['asks'])))*p['qty']
                    unrealized+=item['unrealized']
                net+=sum(t['net'] for t in visible);candidates.append(item)
                if p and not feed_interrupted(p):net+=p['gross']-p.get('fees_paid',0)
            return dict(bulk_research=bulk_bridge.status(),detailed_trial_capacity=PAPER_TRIAL_LIMIT,mode='paper',strategy_id='why-discovery-paper',running=a['running'],kill=a['kill'],live_enabled=False,
                fees=dict(maker=FEE_RATE,taker=FEE_RATE,source='Conservative 4bp per fill; maker assumed at taker rate',model_version=MODEL_VERSION,legacy_zero_fee_trades=sum(t.get('fee_model_version')!=MODEL_VERSION for c in a['candidates'].values() for t in c['trades'])),funding_included=False,
                realized=net,unrealized=unrealized,equity=25000+net+unrealized,markets=len(self.markets),feed_results_archived=archived,
                fresh_markets=sum(self.books.fresh(m,now) for m in self.markets),scanned_markets=len(self.scanned),
                last_scan=self.last_scan,feed_error=self.error,ai_ready=self.ai_ready,ai_error=self.ai_error,ai_model=self.ai_model,
                review_context=self.context_status,archived_versions=a.get('archive',{}).get('count',0),risk_lock=a.get('risk_lock'),
                candidates=sorted(candidates,key=lambda c:c['created'],reverse=True),events=self.store.events(u),
                assumptions=['New trades charge 0.04% per fill on both entries and exits, including maker fills conservatively. Older recorded trades retain their original zero-fee P&L; screening subtracts estimated fees. P&L excludes funding; review delays without funding evidence.',
                    'Feed-interrupted results are cleared from displayed performance; original records remain in the audit ledger.',
                    '250ms simulated order and cancel latency; public queue ahead is consumed before maker fills.',
                    'Entries require fresh funding times and close before settlement. Unknown funding invalidates the test.',
                    'Positions hold through feed outages up to 120s; longer outages invalidate the trial. Feed-gap and owner closes are excluded from the 10-trade screen.',
                    'Each strategy chooses maker or taker entry and optional breakeven/trailing stops. Entries filling under 50% of planned size are flattened and not counted.',
                    'Size risks 2 USDT including entry/exit trading fees and a stop-slippage reserve of max(20bp, 3x spread), capped at 500 USDT notional.',
                    'Target must be at least 1.5x the stop.',
                    '6/10 and positive net qualify for review; approved versions continue in a separate forward paper phase.',
                    'Every ten valid forward trials must pass the same screen; failed versions retire.',
                    'Paper qualification requires at least 50 valid forward trials spanning 24 hours, positive net, profit factor >=1.2 and closed-trade drawdown <=10 USDT; this does not establish profitability.',
                    'Daily loss locks expire at the next UTC day and the new day is checked before further entries.',
                    'Completed versions are archived at capacity with original trials and P&L preserved.',
                    'Inactive experiments expire after two hours without valid trial progress, or twice their maximum hold if longer.',
                    'Live trading is disabled.'])
