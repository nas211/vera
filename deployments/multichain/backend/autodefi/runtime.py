import asyncio,fcntl,time,json
from contextlib import suppress
from vela.db import connect,transaction,put,get,log
from vela.lighter import monitor
from .trading import GovernedPaper,agents_for
from .lending import Lending
from .chain import DogeOS
from .repayment import Repayment

class Runtime:
    def __init__(self,config,db,feed,paper,model,chain):
        self.config,self.db,self.feed,self.paper,self.model,self.chain=config,db,feed,paper,model,chain
        self.tasks=[];self.lock=None;self.errors={};self.last_chain=0;self.candle_cache={}
        self.inflight=set();self.starting=set();self.stopping=False;self.last_cycle=0;self.restarts={}
    @property
    def error(self):
        # Each loop clears its own entry after a successful pass, so a single
        # transient failure is not reported for ever.
        return '; '.join(v for v in self.errors.values() if v)
    async def start(self):
        # create_app acquired ownership before SQLite initialization.
        if self.tasks or self.stopping:raise RuntimeError('Runtime has already been started or stopped')
        self.tasks=[asyncio.create_task(self.supervise('position-risk-and-execution',self.loop),name='position-risk-and-execution')]
        if self.config.network_enabled:
            self.tasks.extend(asyncio.create_task(self.supervise(name,fn),name=name) for fn,name in (
                (self.feed.refresh,'market-history'),(self.feed.stream,'market-stream'),
                (self.model_loop,'model-decisions'),(self.chain_loop,'chain-reconciliation')))
    async def supervise(self,name,fn):
        delay=1
        while not self.stopping:
            started=time.monotonic()
            try:
                await fn()
            except asyncio.CancelledError:raise
            except Exception:pass  # No provider response, key or account data in logs.
            if self.stopping:return
            self.errors['task:'+name]=name+' interrupted; restarting'
            self.restarts[name]=self.restarts.get(name,0)+1
            if time.monotonic()-started>60:delay=1
            await asyncio.sleep(delay)
            delay=min(30,delay*2)
            self.errors.pop('task:'+name,None)
    async def stop(self):
        self.stopping=True
        for t in self.tasks:t.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        # Cancelling to_thread does not stop its worker. Retain database
        # ownership until every model worker has closed its SQLite connection.
        while self.inflight:
            await asyncio.gather(*tuple(self.inflight),return_exceptions=True)
    async def run_blocking(self,fn,*args):
        if self.stopping:raise RuntimeError('Runtime is shutting down')
        # One background evaluation plus one connection preflight. Refuse a
        # queue of wallet requests waiting behind the serialized model client.
        if len(self.inflight)>=2:raise RuntimeError('Model worker capacity reached; retry later')
        task=asyncio.create_task(asyncio.to_thread(fn,*args))
        self.inflight.add(task)
        def finished(done):
            self.inflight.discard(done)
            if not done.cancelled():done.exception()  # Also observe failures after requester cancellation.
        task.add_done_callback(finished)
        return await asyncio.shield(task)
    def model_tick(self):
        # Model HTTP must never hold up protective position management.
        db=connect(self.config.db_path)
        try:
            paper=GovernedPaper(db,self.config.paper(),self.feed,require_session=True)
            agents_for(db,self.model,paper).tick_ai()
            monitor.review_position(db,self.model)
        finally:db.close()
    async def model_loop(self):
        delay=5
        while True:
            try:
                await self.run_blocking(self.model_tick)
                self.errors.pop('model',None)
                delay=5
            except Exception as exc:
                self.errors['model']='Model step unavailable; no unapproved entries'
                # Do not repeatedly spend model budget when persistence fails.
                # Log only the exception class, never provider/request contents.
                delay=min(60,delay*2)
                with suppress(Exception):
                    log(self.db,'runtime','model_step_failed','warning',error=type(exc).__name__,retrySeconds=delay)
            await asyncio.sleep(delay)
    async def chain_loop(self):
        while True:
            failed=False
            for row in self.db.execute('SELECT owner,data FROM vaults').fetchall():
                if json.loads(row['data']).get('simulated'):continue
                try:
                    from .vault_migration import context
                    result,receipt=await asyncio.to_thread(self.chain.read,row[0],**context(self.db,row[0]))
                    # Reconcile owner-confirmed settlements even if the mobile page closed
                    # before it could call /api/vault/sync. Never manufacture a receipt.
                    DogeOS.record(self.db,row[0],result,receipt)
                except Exception:
                    failed=True;self.errors['chain']='Chain refresh unavailable; entries require fresh confirmations'
            if not failed:self.errors.pop('chain',None)
            await asyncio.sleep(10)
    def sample_equity(self,now):
        # Observations, not reconstructed returns. Leave stale gaps visible and
        # avoid the old desk's high-frequency unbounded duplicate observations.
        with transaction(self.db):
            for row in self.db.execute("SELECT * FROM users WHERE status='active'").fetchall():
                latest=self.db.execute('SELECT MAX(ts) FROM equity WHERE user=? AND session=?',
                                       (row['address'],row['session'])).fetchone()[0]
                if latest is not None and now-latest<60:continue
                snapshot=self.paper.snapshot(row['address'])
                if snapshot['equity'] is None:continue
                self.db.execute('INSERT INTO equity(user,session,ts,equity,pnl) VALUES (?,?,?,?,?)',
                    (row['address'],row['session'],now,snapshot['equity'],snapshot['pnl']))
    async def loop(self):
        while True:
            try:
                # One commit per observation batch, not thousands of unchanged
                # candle fsyncs per second. This checkout owns its own cache.
                with transaction(self.db):
                    for m in self.feed.markets.values():
                        self.db.execute('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)',(m.symbol,str(m.mark),time.time(),'Lighter public',int(m.fresh()),'' if m.fresh() else 'stale'))
                        current=tuple((c['ts'],c['o'],c['h'],c['l'],c['c']) for c in m.candles)
                        if self.candle_cache.get(m.symbol)!=current:
                            for c in current:self.db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(m.symbol,*c))
                self.candle_cache={m.symbol:tuple((c['ts'],c['o'],c['h'],c['l'],c['c']) for c in m.candles) for m in self.feed.markets.values()}
                self.paper.risk_governor();self.paper.tick();self.paper.settle_tick()
                Repayment(self.db,self.paper).tick()
                with transaction(self.db): monitor.consume_exits(self.paper)
                agents=agents_for(self.db,self.model,self.paper)
                for row in self.db.execute("SELECT * FROM users WHERE status='active'").fetchall():
                    try:
                        self.paper.gate(row['address'])
                        signal=agents.signal(row)
                        if signal:
                            with transaction(self.db):self.paper.open(row['address'],signal)
                    except ValueError:pass
                if time.time()-self.last_chain>=60:
                    self.sample_equity(time.time());self.last_chain=time.time()
                self.errors.pop('runtime',None)
                self.last_cycle=time.time()
            except Exception as exc:self.errors['runtime']='Runtime step failed: '+type(exc).__name__
            await asyncio.sleep(.25)
