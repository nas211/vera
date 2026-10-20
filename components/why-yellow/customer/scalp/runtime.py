"""Durable real-order boundary for the unchanged micro-paper decision engine.

No public trade can create a fill. Each real intent is committed before its single
POST; ambiguous submissions block entries until confirmed by private history.
"""
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from execution.transport import all_pages, iter_pages, TransportAmbiguous, VenueRejected, VenueUnavailable
from execution.types import OrderRequest, D
from execution.validation import validate_order
from execution.sizing import size_order, entry_loss_limit, ExposureLimits
from .strategy import LiveStrategy, CONFIG, MODULES, RULES
from .store import Store
from .emergency import EmergencyStop
from .entry_policy import CoinEntryPolicy
from .policy import software_emergency_authorized
from .sizing import budget_for_equity, minimum_quantity

TERMINAL = {'filled', 'cancelled', 'rejected'}
STRATEGY = 'WHY-MICROSCALP'


class Runtime:
    def __init__(self, ctx, transport, metadata, state_root, clock=time.time):
        self.ctx, self.transport, self.metadata, self.clock = ctx, transport, metadata, clock
        self.lock = threading.RLock()
        self.store = Store(str(Path(state_root)/'tenants'/ctx.account_id/'scalp.sqlite'))
        self.model = LiveStrategy({}, clock)
        state = self.store.state()
        if state: self.model.restore_checkpoint(state)
        self.ready = False; self.last_error = None; self.last_sync = 0.; self.entries = False
        self.started = False
        known={r['payload']['id'] for r in self.store.orders()}
        self.model.orders={k:v for k,v in self.model.orders.items() if k in known}
        self.emergency = EmergencyStop(self)
        self.entry_policy = CoinEntryPolicy(self)
        self._set_pending_exits()

    def _set_pending_exits(self):
        self.model.pending_exits = {o['payload']['lot'] for o in self.store.active_orders()
                                    if o['state'] not in TERMINAL and o['payload'].get('lot') and o['payload']['kind'] in ('ioc','market')}
        self.model.pending_exits.update(lid for lid in self.model.lots if self.emergency.blocks_lot(lid))

    def _number(self, value, step):
        value = D(value); n = (value / step).to_integral_value() * step
        if not value.is_finite() or abs(n-value) > step * D('0.000001'):
            raise ValueError('strategy_order_off_venue_step')
        return n

    def _request(self, oid, payload):
        f = self.metadata.filters(payload['market'])
        lot = self.model.lots.get(payload.get('lot'))
        buy = payload['buy']
        direction = ('long' if lot['buy'] else 'short') if lot else ('short' if buy else 'long') if payload.get('lot') else ('long' if buy else 'short')
        req = OrderRequest(self.ctx.namespace, payload['market'], 'buy' if buy else 'sell', direction,
                           'market' if payload['kind']=='market' else 'limit' if payload['kind']=='ioc' else 'post_only',
                           self._number(payload['qty'],f.step), int(CONFIG['leverage']), oid,
                           None if payload['kind']=='market' else self._number(payload['price'],f.tick), bool(payload.get('lot')),
                           'ioc' if payload['kind'] in ('ioc','market') else 'gtc')
        # A partial fill can leave less than the opening minimum notional.
        # Submit only its owned, reduce-only quantity; never enlarge an exit
        # to satisfy an entry-size filter. The venue still validates the close.
        exit_filters=replace(f,min_notional=D(0)) if req.reduce_only else f
        validate_order(req, exit_filters, ref_price=D(payload['price']), max_leverage_policy=self.ctx.leverage)
        return req

    def _entry_conditions(self, market):
        from .availability import SCALP_LIVE_AVAILABLE
        if not SCALP_LIVE_AVAILABLE:
            raise ValueError('scalp_coming_soon')
        if self.emergency.engaged:
            raise ValueError('scalp_emergency_close_pending')
        if not self.ready or not self.ctx.entries_wanted or self.ctx.trading_style != 'scalp':
            raise ValueError('entries_disabled_or_reconciliation_required')
        if not self.model.connected or self.clock()-self.model.books.get(market,{}).get('received',0)>15:
            raise ValueError('stale_market_data')

    def _guard_entry(self, req):
        self._entry_conditions(req.market)
        active=[o for o in self.store.active_orders() if o['state'] not in TERMINAL]
        if any(o['state'] in ('submitting','unknown','cancel_pending') for o in active):
            raise ValueError('unresolved_order')
        reason=self.entry_policy.block_reason(req.market)
        if reason:raise ValueError(reason)
        # Only the owner-approved Scalp exception may use software protection.
        # Fail closed if its emergency guard is not initialized; never resize.
        software_guard = software_emergency_authorized(self.ctx.namespace)
        if ((entry_loss_limit(self.ctx.namespace) is not None and not software_guard)
                or (software_guard and not self.emergency.enabled)):
            raise ValueError('account_hard_loss_policy_requires_protected_execution')
        account=self.transport.get_account(self.ctx.namespace)
        budget=budget_for_equity(account.equity)
        self.model.set_equity(account.equity)
        if budget['equity_usd'] <= 0:
            raise ValueError('invalid_scalp_equity')
        # Yellow's available balance already excludes margin reserved by open
        # orders and positions. Subtracting local orders again blocks affordable
        # entries. Every submission reads the venue balance; ambiguous orders
        # are rejected above before this admission check.
        parents={lot.get('entry_order',lid) for lid,lot in self.model.lots.items()}
        parents.update(o['payload']['id'] for o in active if not o['payload'].get('lot'))
        notional=req.qty*req.price
        commitments=[(l['market'], bool(l['buy']), D(l['qty'])*max(D(l['entry']),D(self.model.last_mid.get(l['market'],l['entry'])))) for l in self.model.lots.values()]
        commitments.extend((o['payload']['market'],bool(o['payload']['buy']),
                            (D(o['payload']['qty'])-D(o['payload'].get('filled_qty','0')))*D(o['payload']['price']))
                           for o in active if not o['payload'].get('lot'))
        limits=ExposureLimits(D('Infinity'),budget['gross_notional_cap_usd'],CONFIG['max_entry_slots'])
        size_order(notional,req.price,req.leverage,self.metadata.filters(req.market),account,limits,
                   open_notional=sum((v for m,b,v in commitments),D(0)),open_positions=len(parents))
        if sum((v for m,b,v in commitments if m==req.market),D(0))+notional > budget['per_market_notional_cap_usd']:
            raise ValueError('scalp_market_exposure_limit')
        if sum((v for m,b,v in commitments if m==req.market and b==(req.side=='buy')),D(0))+notional > budget['per_side_notional_cap_usd']:
            raise ValueError('scalp_side_exposure_limit')
        # Account REST can take seconds. Pause state and book freshness must
        # still hold when the order is actually about to be persisted/sent.
        self._entry_conditions(req.market)

    def submit(self, payload):
        oid='wysc'+hashlib.sha256((self.ctx.namespace+':'+payload['id']).encode()).hexdigest()[:28]
        if self.store.has_order(oid): return
        req=self._request(oid,payload)
        if req.reduce_only:
            if not self.ready: raise ValueError('reconciliation_required')
            lot=self.model.lots.get(payload['lot'])
            if not lot: return
            if self.emergency.blocks_lot(payload['lot']) and payload.get('reason')!='EMERGENCY_STOP':
                raise ValueError('scalp_emergency_close_pending')
            payload=dict(payload,entry_order=lot['entry_order'])
            if req.qty>D(str(lot['qty']))+self.metadata.filters(req.market).step*D('0.000001'):
                raise ValueError('exit_exceeds_owned_lot')
            if any(o['state'] not in TERMINAL and o['payload'].get('lot')==payload['lot'] for o in self.store.active_orders()):
                raise ValueError('previous_exit_not_cancelled')
        else:self._guard_entry(req)
        payload=dict(payload,filled_qty='0',direction=req.direction,qty=str(req.qty),price=str(req.price if req.price is not None else payload['price']))
        with self.store.db:
            self.store.put(oid,'submitting',payload);self.store.save(self.model)
        try:
            ack=self.transport.place_order(req)
        except (VenueRejected,VenueUnavailable) as exc:
            with self.store.db:self.store.put(oid,'rejected',payload)
            self._remove_model_order(payload)
            self.last_error=type(exc).__name__
            return
        except Exception:
            with self.store.db:self.store.put(oid,'unknown',payload)
            self.ready=False;self.last_error='submission_reconciliation_required'
            return
        with self.store.db:self.store.put(oid,'open',payload,str(ack.venue_order_id))
        if payload['id'] in self.model.orders:
            self.model.orders[payload['id']]['active']=True

    def _remove_model_order(self,payload):
        self.model.orders.pop(payload['id'],None)
        lid=payload.get('lot')
        if lid and lid in self.model.lots:self.model.lots[lid]['exit_id']=None

    def _fill(self, trade, row):
        if self.store.seen(trade.trade_id):return
        p=row['payload'];q=float(trade.qty);price=float(trade.price);fee=float(trade.fee)
        fill_context=dict(order=p,lot=dict(self.model.lots.get(p.get('lot'),{})))
        if (not all(D(v).is_finite() for v in (trade.qty,trade.price,trade.fee)) or q<=0 or price<=0
                or trade.market!=p['market'] or trade.fee_currency!='USDT'
                or D(p.get('filled_qty','0'))+trade.qty>D(p['qty'])):
            raise ValueError('invalid_private_fill')
        if not p.get('lot'):
            lid='scalp-'+hashlib.sha256(trade.trade_id.encode()).hexdigest()[:24]
            self.model.lots[lid]=dict(market=p['market'],buy=p['buy'],entry=price,qty=q,
                 opened=(trade.ts/1000 if trade.ts>1e11 else trade.ts) or self.clock(),exit_id=None,entry_order=p['id'])
            self.model.stats['entry_fills']+=1
            self.model.stats['realized_pnl']-=fee
        else:
            lid=p['lot'];lot=self.model.lots.get(lid)
            if not lot or q>lot['qty']+1e-9:raise ValueError('unreconciled_exit_fill')
            self.model.stats['realized_pnl']+=(price-lot['entry'])*q*(1 if lot['buy'] else -1)-fee
            lot['qty']-=q;self.model.stats['exit_fills']+=1
            if lot['qty']<1e-10:
                self.model.lots.pop(lid);self.model.stats['closed_lots']+=1
        self.model.stats['fees']+=fee
        p['filled_qty']=str(D(p.get('filled_qty','0'))+trade.qty)
        self.store.fill(trade,fill_context);self.store.put(row['id'],row['state'],p,row['venue_id'])
        self.emergency.record_fill(trade,fill_context)
        self.store.save(self.model)

    def reconcile(self):
        with self.lock:
            self.ready=False
            # The immutable private-fill journal is authoritative. Older cancel
            # paths could overwrite a newer filled_qty with a stale row while
            # the fill and its inventory effect remained durably committed.
            self.store.repair_fill_counters()
            rows=self.store.orders();by_coid={o['id']:o for o in rows};by_vid={o['venue_id']:o for o in rows if o['venue_id']}
            active=[r for r in rows if r['state'] not in TERMINAL]
            since=min((float(r['payload']['requested']) for r in active),default=self.clock())-60
            # Independent private reads go out together: Yellow answers each in
            # ~0.4-2s, and sequential reads kept exits unmanaged for many seconds.
            # Positions are still read after fills are applied, as before.
            with ThreadPoolExecutor(3,thread_name_prefix='scalp-read') as pool:
                pending_account=pool.submit(self.transport.get_account,self.ctx.namespace)
                pending_open=pool.submit(all_pages,self.transport.get_open_orders,self.ctx.namespace)
                pending_trades=pool.submit(all_pages,self.transport.get_trades,self.ctx.namespace,since=since) if active else None
                account=pending_account.result()
                opened=pending_open.result()
                trades=pending_trades.result() if pending_trades else []
            self.model.set_equity(account.equity)
            snapshot={o.client_order_id:o for o in opened if o.client_order_id}
            missing={r['id'] for r in active}-set(snapshot)
            # Walk each missing order's own market history; the account-wide walk
            # spent ~1s per page and hit the 200-page bound on busy accounts.
            for market in sorted({by_coid[oid]['payload']['market'] for oid in missing}):
                wanted={oid for oid in missing if by_coid[oid]['payload']['market']==market}
                for order in iter_pages(self.transport.get_orders,self.ctx.namespace,market=market):
                    if order.client_order_id in wanted:
                        snapshot[order.client_order_id]=order;wanted.remove(order.client_order_id)
                        missing.discard(order.client_order_id)
                    if not wanted:break
            # Recover venue IDs before reading fills after a lost POST response.
            for oid,order in snapshot.items():
                if oid in by_coid:by_vid[order.venue_order_id]=by_coid[oid]
            # History may be newest first. Establish entry lots before applying
            # their exits, including all fills recovered after a restart.
            def fill_order(t):
                r=by_coid.get(t.client_order_id) or by_vid.get(t.venue_order_id)
                return (bool(r and r['payload'].get('lot')), t.ts, t.trade_id)
            trades.sort(key=fill_order)
            for trade in trades:
                row=by_coid.get(trade.client_order_id) or by_vid.get(trade.venue_order_id)
                if row:
                    before=self.model.checkpoint()
                    try:
                        with self.store.db:self._fill(trade,row)
                    except Exception:
                        self.model.restore_checkpoint(before);raise
            unresolved=False
            for row in self.store.orders():
                if row['state'] in TERMINAL:
                    self._remove_model_order(row['payload']);continue
                vo=snapshot.get(row['id'])
                if not vo:
                    unresolved=True;continue
                p=row['payload']
                if vo.filled_qty != D(p.get('filled_qty','0')):
                    unresolved=True;continue
                with self.store.db:self.store.put(row['id'],'cancel_pending' if p.get('cancel_requested') and vo.state=='open' else vo.state,p,vo.venue_order_id)
                if vo.state in TERMINAL:self._remove_model_order(p)
                elif p['kind']=='maker':
                    remaining=D(p['qty'])-vo.filled_qty
                    cancel_at=self.model.orders.get(p['id'],{}).get('cancel_at',p.get('cancel_at'))
                    self.model.orders[p['id']]=dict(p,qty=float(remaining),price=float(p['price']),active=True,
                        active_at=p['requested'],queue=0.,cancel_at=self.clock() if p.get('cancel_requested') else cancel_at)
                    if p.get('lot') in self.model.lots:self.model.lots[p['lot']]['exit_id']=p['id']
            expected={}
            for lot in self.model.lots.values():
                key=(lot['market'],'long' if lot['buy'] else 'short')
                expected[key]=expected.get(key,D(0))+D(lot['qty'])
            actual={}
            for position in self.transport.get_positions(self.ctx.namespace):
                if position.qty>0:
                    key=(position.market,position.direction)
                    actual[key]=actual.get(key,D(0))+position.qty
            for key in set(expected)|set(actual):
                tolerance=self.metadata.filters(key[0]).step*D('0.000001')
                if abs(expected.get(key,D(0))-actual.get(key,D(0)))>tolerance:
                    unresolved=True
            own_ids={o['id'] for o in self.store.orders()}
            if any(o.client_order_id not in own_ids for o in opened):unresolved=True
            self.ready=not unresolved;self.last_sync=self.clock()
            self.last_error=None if self.ready else 'venue_state_reconciliation_required'
            self._set_pending_exits()
            self.entry_policy.refresh()
            with self.store.db:self.store.save(self.model)
            return self.ready

    def cancel(self,row,reconcile=True):
        # A previous cancellation may have reconciled fills for this order.
        # Re-read before writing so a frame's old row cannot erase those fills.
        row=self.store.get_order(row['id'])
        if row is None:return
        if row['state'] in TERMINAL:return
        if not row['venue_id']:
            self.ready=False;return
        row['payload']['cancel_requested']=True
        with self.store.db:self.store.put(row['id'],'cancel_pending',row['payload'],row['venue_id'])
        try:self.transport.cancel_order(self.ctx.namespace,row['payload']['market'],row['venue_id'])
        except (VenueRejected,VenueUnavailable,TransportAmbiguous):pass
        # A successful DELETE is not proof of cancellation; private history is.
        # Frame batches still reconcile before submitting any replacement.
        if reconcile:self.reconcile()

    def frame(self,data=None):
        with self.lock:
            if not self.ready or self.clock()-self.last_sync>=1:
                self.reconcile()
            previous=set(self.model.orders)
            self.model.connected=bool(self.ready)
            from .availability import SCALP_LIVE_AVAILABLE
            self.model.allow_entries=SCALP_LIVE_AVAILABLE and self.ctx.entries_wanted
            for message in data or []:
                received=message.get('_received_at',self.clock())
                model_clock,connected=self.model.clock,self.model.connected
                # Keep original arrival times while applying queued increments.
                # Old data rebuilds the book but cannot trigger profit execution
                # or masquerade as a freshly received quote after slow REST.
                self.model.clock=lambda:received
                if self.clock()-received>15:self.model.connected=False
                try:self.model.receive(message)
                finally:self.model.clock,self.model.connected=model_clock,connected
                self.emergency.check()
                self._set_pending_exits()
            self.emergency.check()
            self._set_pending_exits()
            self.model.maintain()
            removed=previous-set(self.model.orders)
            excess=self.entry_policy.excess_entries()
            exit_cancels=[];entry_cancels=[]
            for row in self.store.active_orders():
                p=row['payload']
                if row['state'] not in TERMINAL and (p['id'] in removed or p['id'] in excess or not p.get('lot') and (not self.model.allow_entries or not self.ready)):
                    (exit_cancels if p.get('lot') else entry_cancels).append(row)
            # Confirm cancellation of owned exits before replacing them. Do
            # not make a stop IOC wait behind unrelated entry-grid DELETEs.
            for row in exit_cancels:self.cancel(row,reconcile=False)
            if exit_cancels:self.reconcile()
            self.emergency.process()
            intents,self.model.intents=self.model.intents,[]
            with self.store.db:self.store.save(self.model)
            self._submit_intents([p for p in intents if p.get('lot')])
            self._set_pending_exits()
            for row in entry_cancels:self.cancel(row,reconcile=False)
            if entry_cancels:self.reconcile()
            self._submit_intents([p for p in intents if not p.get('lot')])
            self._set_pending_exits()
            with self.store.db:self.store.save(self.model)

    def _submit_intents(self, intents):
        # Manage every existing lot first, regardless of entry affordability.
        entries={}
        for p in intents:
            if not p.get('lot'):
                entries.setdefault(p['market'],[]).append(p)
                continue
            try:self.submit(p)
            except (ValueError,ArithmeticError) as exc:
                self._remove_model_order(p);self.last_error=str(exc)
        markets=list(MODULES['v2'].ACTIVE_MARKETS)
        waiting=self.model.waiting_entry_market
        if waiting and (not self.model.top(waiting)
                        or self.clock()-self.model.books.get(waiting,{}).get('received',0)>15
                        or all(self.model.entry_reason(waiting,buy) for buy in (True,False))):
            self.model.waiting_entry_market=None
        while entries:
            # A long series of entry balance reads/POSTs must yield when an
            # existing lot needs its stop pass or an IOC needs reconciliation.
            now=self.clock()
            if (self.model.pending_exits or any('stop_at' in lot or now-lot['opened']>=RULES['max_hold_seconds']
                                                for lot in self.model.lots.values())):
                self.model.stats['entry_batches_yielded_for_exit']=self.model.stats.get('entry_batches_yielded_for_exit',0)+1
                break
            waiting=self.model.waiting_entry_market
            if waiting:
                if waiting not in entries:break
                market=waiting
            else:
                order=markets[self.model.entry_cursor:]+markets[:self.model.entry_cursor]
                market=next((m for m in order if m in entries),None)
                if market is None:break
            p=entries[market].pop(0)
            if not entries[market]:del entries[market]
            try:
                self.submit(p)
            except (ValueError,ArithmeticError) as exc:
                self._remove_model_order(p)
                if getattr(exc,'code',None) in ('no_available_balance','insufficient_available_balance','above_max_total_notional'):
                    # Keep this market's turn until funds are released, instead
                    # of continually spending them on cheaper coins.
                    self.model.waiting_entry_market=market
                    self.last_error=None
                    break
                self.last_error=str(exc)
            else:
                self.model.waiting_entry_market=None
            self.model.entry_cursor=(markets.index(market)+1)%len(markets)
        for remaining in entries.values():
            for p in remaining:self._remove_model_order(p)

    def minimum_entries(self):
        quotes=[]
        for market in MODULES['v2'].ACTIVE_MARKETS:
            top=self.model.top(market)
            if not top or market not in self.model.filters:continue
            price=D(str(sum(top)/2))
            qty=minimum_quantity(price,self.model.filters[market])
            quotes.append(dict(market=market,notional_usd=float(qty*price),
                               margin_usd=float(qty*price/CONFIG['leverage'])))
        return quotes

    def disconnected(self):
        with self.lock:
            self.model.disconnect();self.ready=False
            # Send every entry cancel first, then confirm them with one private
            # read. A reconcile per cancel kept the account stale for ~30s.
            cancelled=False
            for row in self.store.active_orders():
                if row['state'] not in TERMINAL and not row['payload'].get('lot'):
                    self.cancel(row,reconcile=False);cancelled=True
            try:
                if cancelled:self.reconcile()
            finally:
                self.ready=False
                with self.store.db:self.store.save(self.model)

    def manual_close(self, lid):
        with self.lock:
            self.reconcile()
            if lid not in self.model.lots:return 'ALREADY_CLOSED'
            if self.emergency.blocks_lot(lid):
                self.emergency.process()
                self.reconcile()
                return 'CLOSED' if lid not in self.model.lots else 'CLOSE_PENDING'
            for row in self.store.orders():
                if row['state'] not in TERMINAL and row['payload'].get('lot')==lid:self.cancel(row)
            if lid not in self.model.lots:return 'CLOSED'
            lot=self.model.lots[lid]
            self.model.next_id+=1
            payload=dict(kind='market',id=str(self.model.next_id),market=lot['market'],buy=not lot['buy'],
                         price=lot['entry'],qty=lot['qty'],lot=lid,requested=self.clock())
            self.submit(payload)
            self.reconcile()
            return 'CLOSED' if lid not in self.model.lots else 'CLOSE_SUBMITTED'

    def results(self, limit=20):
        return read_results(self.store.db,self.ctx.account_id,self.model.checkpoint(),limit)

    def status(self):
        with self.lock:
            pending=self.store.active_orders()
            return dict(loaded=True,started=self.started,venue='READY' if self.ready else 'RECONCILING',
                trading_style='scalp',open_positions=len(self.model.lots),
                last_sync=self.last_sync,syncing=False,feed_connected=self.model.connected,
                equity_usd=float(self.model.budget['equity_usd']),
                sizing_mode='exchange_minimum',
                minimum_entries=self.minimum_entries(),
                waiting_for_market=self.model.waiting_entry_market,
                strategy_entry_slots=CONFIG['max_entry_slots'],
                one_trade_per_coin=False,
                loss_cooldown_seconds=15 if self.entry_policy.enabled else 0,
                coin_cooldowns={m:until for m,until in self.model.entry_cooldowns.items() if until>self.clock()},
                ambiguous_orders=sum(r['state'] in ('unknown','submitting','cancel_pending') for r in pending),
                pending_entries=sum(not r['payload'].get('lot') for r in pending),
                emergency_stop_usd=1 if self.emergency.enabled else None,
                emergency_stop_mode='bot_managed' if self.emergency.enabled else None,
                emergency_close_pending=self.emergency.engaged,
                entries_allowed=self.model.allow_entries and self.ready and self.model.connected and not self.emergency.engaged,last_error=self.last_error,
                positions=[dict(position_id=lid,market=l['market'],direction='long' if l['buy'] else 'short',
                  qty=str(l['qty']),entry_price=str(l['entry']),state='SCALP_MANAGED',
                  strategy=STRATEGY,opened_ts=l['opened']) for lid,l in self.model.lots.items()])


def read_results(db, account_id, state, limit=20):
    rows=[]
    for record in db.execute('select payload from fills order by rowid desc'):
        fill=json.loads(record[0]);ctx=fill.get('context',{});lot=ctx.get('lot')
        if not lot:continue
        qty=float(fill['qty']);price=float(fill['price']);fee=float(fill['fee'])
        pnl=(price-lot['entry'])*qty*(1 if lot['buy'] else -1)
        rows.append(dict(market=lot['market'],direction='long' if lot['buy'] else 'short',strategy=STRATEGY,
            qty=str(qty),entry_price=str(lot['entry']),exit_price=str(price),pnl=pnl,fees=fee,
            reason='EMERGENCY_STOP' if ctx['order'].get('reason')=='EMERGENCY_STOP' else 'STOP_EXIT' if ctx['order'].get('reason')=='STOP_EXIT' else 'NET_TARGET_IOC' if ctx['order']['kind']=='ioc' else 'USER_MARKET_CLOSE' if ctx['order']['kind']=='market' else 'MAKER_EXIT',
            opened_ts=lot['opened'],closed_ts=float(fill['ts'])/1000 if float(fill['ts'])>1e11 else float(fill['ts'])))
    return dict(account_id=account_id,mode='live',summary=dict(closed_trades=len(rows),wins=sum(r['pnl']>r['fees'] for r in rows),
      losses=sum(r['pnl']<r['fees'] for r in rows),realized_pnl=sum(r['pnl'] for r in rows),fees=state.get('stats',{}).get('fees',0),
      volume=sum(float(r['qty'])*float(r['exit_price']) for r in rows),open_positions=len(state.get('lots',{})),
      last_trade_at=rows[0]['closed_ts'] if rows else None),trades=rows[:limit])
