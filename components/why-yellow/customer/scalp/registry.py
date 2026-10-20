"""Separate per-account microscalp workers; Swing continues on its existing engine."""
import json
import sqlite3
import queue
from pathlib import Path
import threading
import time
from dataclasses import replace
from .runtime import Runtime, read_results
from .strategy import MARKETS
from ..log import safe_exc, get_logger

log = get_logger("scalp")


class FeedInbox:
    """Read/ack the socket while private REST calls run; retain every increment."""
    def __init__(self,ws,timeout_error,clock=time.monotonic):
        self.ws,self.timeout_error,self.clock=ws,timeout_error,clock
        self.items=queue.Queue(maxsize=10000)
        self.stop=threading.Event();self.error=None
        self.thread=threading.Thread(target=self.run,name='scalp-feed-reader',daemon=True)
    def run(self):
        try:
            while not self.stop.is_set():
                try:raw=self.ws.recv()
                except self.timeout_error:continue
                if not raw:raise RuntimeError('scalp_feed_closed')
                for line in raw.splitlines():
                    msg=json.loads(line)
                    if msg=={}:self.ws.send('{}');continue
                    if 'error' in msg:raise RuntimeError('scalp_feed_error')
                    data=msg.get('subscribe',{}).get('data') or msg.get('push',{}).get('pub',{}).get('data')
                    if data:
                        try:self.items.put_nowait((self.clock(),dict(data,_received_at=time.time())))
                        except queue.Full:raise RuntimeError('scalp_feed_backlog') from None
        except Exception as exc:self.error=exc
    def batch(self):
        if self.error:raise self.error
        try:first=self.items.get(timeout=.5)
        except queue.Empty:return []
        rows=[first]
        # Bounded by the queue size; preserve sequence and never drop increments.
        for _ in range(9999):
            try:rows.append(self.items.get_nowait())
            except queue.Empty:break
        if self.error:raise self.error
        return [data for received,data in rows]
    def close(self):
        self.stop.set()
        self.ws.close()
        self.thread.join(2)


class Worker:
    def __init__(self, ctx, transport, metadata, state_root):
        self.runtime=Runtime(ctx,transport,metadata,state_root)
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,name='scalp-'+ctx.account_id[:8],daemon=True)
        self.cached=self.runtime.status()
    def run(self):
        import websocket
        rt=self.runtime
        while not self.stop.is_set():
            ws=None;inbox=None
            try:
                rt.model.filters={m:dict(tick=float(rt.metadata.filters(m).tick),step=float(rt.metadata.filters(m).step),
                    min_qty=float(rt.metadata.filters(m).min_qty),min_notional=float(rt.metadata.filters(m).min_notional)) for m in MARKETS}
                rt.reconcile();rt.started=True
                ws=websocket.create_connection('wss://trade.api.yellow.pro/ws',timeout=10)
                ws.send(json.dumps({'id':1,'connect':{}}));ws.recv();i=2
                for market in MARKETS:
                    for channel in ('orderbook','trades'):
                        ws.send(json.dumps({'id':i,'subscribe':{'channel':f'public.{channel}.increment.{market}'}}));i+=1
                ws.settimeout(.5)
                inbox=FeedInbox(ws,websocket.WebSocketTimeoutException)
                inbox.thread.start()
                while not self.stop.is_set():
                    rt.frame(inbox.batch());self.cached=rt.status()
            except Exception as exc:
                error=safe_exc(exc)
                log.warning("Scalp worker interrupted: %s",error)
                rt.ready=False;rt.last_error=error
                try:rt.disconnected()
                except Exception:pass
                rt.ready=False;rt.last_error=error
                self.cached=rt.status();self.stop.wait(3)
            finally:
                if inbox:inbox.close()
                elif ws:ws.close()
        try:rt.disconnected()
        finally:
            rt.store.db.close()
            if hasattr(rt.transport,'forget_credentials'):rt.transport.forget_credentials()
    def close(self):
        # Prevent any further entries immediately. Cleanup belongs to this
        # worker, so a slow exchange cannot block the existing Swing runner.
        self.runtime.ctx=replace(self.runtime.ctx,entries_paused=True)
        self.stop.set()



class ScalpRegistry:
    def __init__(self, accounts, transport_factory, metadata, state_root):
        self.accounts,self.factory,self.metadata,self.root=accounts,transport_factory,metadata,state_root
        self.workers={};self.retiring={};self.lock=threading.RLock()
    def refresh(self):
        wanted={c.account_id:c for c in self.accounts.list_execution_contexts() if c.trading_style=='scalp'}
        with self.lock:
            self.retiring={aid:w for aid,w in self.retiring.items() if w.thread.is_alive()}
            for aid in list(self.workers):
                if aid not in wanted:
                    worker=self.workers.pop(aid);worker.close();self.retiring[aid]=worker
            for aid,ctx in wanted.items():
                if aid in self.retiring:continue
                if aid not in self.workers:
                    worker=Worker(ctx,self.factory(ctx),self.metadata,self.root)
                    self.workers[aid]=worker;worker.thread.start()
                else:
                    self.workers[aid].runtime.ctx=ctx
        return {'accounts':len(self.workers)}
    def close_for_user(self, ident, position_id):
        from ..accounts import AccountError
        worker=next((w for w in self.workers.values() if w.runtime.ctx.user_id==ident.user_id),None)
        if not worker:raise AccountError('execution_unavailable',503)
        rt=worker.runtime
        if not rt.lock.acquire(blocking=False):raise AccountError('position_busy',409)
        try:
            if position_id not in rt.model.lots:raise AccountError('position_not_found',404)
            return rt.manual_close(position_id)
        finally:rt.lock.release()
    def results_for_user(self,ident,limit):
        worker=next((w for w in self.workers.values() if w.runtime.ctx.user_id==ident.user_id),None)
        aid=self.accounts.get_me(ident)['account']['id']
        path=Path(self.root)/'tenants'/str(aid)/'scalp.sqlite'
        if not path.exists():
            return dict(account_id=aid,mode='live',summary=dict(closed_trades=0,wins=0,losses=0,realized_pnl=0,fees=0,volume=0,open_positions=0,last_trade_at=None),trades=[])
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.execute('BEGIN')
            row=db.execute('select payload from checkpoint where id=1').fetchone()
            return read_results(db,aid,json.loads(row[0]) if row else {},limit)
    def runtime_info(self,aid):
        worker=self.workers.get(str(aid))
        if not worker:return None
        if not worker.runtime.lock.acquire(blocking=False):
            # Preserve the last exchange check and its actual error. Keep the
            # existing loaded=False guard: a cached snapshot cannot certify a
            # flat account for style changes while orders may be in flight.
            result=dict(worker.cached,loaded=False,syncing=True,entries_allowed=False)
        else:
            try:result=worker.runtime.status()
            finally:worker.runtime.lock.release()
        if time.time()-result.get('last_sync',0)>30:
            result.update(venue='RECONCILING',entries_allowed=False)
            if not result.get('last_error'):result['last_error']='scalp_private_data_stale'
        return result
    def status(self):
        return {aid:self.runtime_info(aid) for aid in list(self.workers)}
    def shutdown(self):
        workers=list(self.workers.values())+list(self.retiring.values())
        for worker in workers:worker.close()
        deadline=time.monotonic()+20
        for worker in workers:worker.thread.join(max(0,deadline-time.monotonic()))
