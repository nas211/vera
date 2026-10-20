import fcntl
import os
import signal
import time
from .settings import Settings
from .db import connect, enqueue, health, log, put
from .prices import PriceFeed
from .paper import PaperAdapter
from .agents import Agents
from .chain import Chain
from .quotes import process_requests
from .practice import Practice


def queue_active_price(db, settings, now, last_push):
    """Legacy vault maintenance only while a real session is active/stopping."""
    active = db.execute("SELECT 1 FROM users WHERE status IN ('active','stopping')").fetchone()
    if not active:
        db.execute("UPDATE outbox SET status='cancelled',error='No active session; idle price update cancelled' "
                   "WHERE kind='updatePrice' AND status='queued'")
        return last_push
    if not settings.chain_writes or settings.config.get("deployment", {}).get("version", 1) >= 2:
        return last_push
    if now - last_push < 30:
        return last_push
    if not db.execute("SELECT 1 FROM outbox WHERE kind='updatePrice' AND status IN ('queued','signed','sent')").fetchone():
        enqueue(db, "price:" + str(int(now)), "updatePrice", {})
    return now


def main():
    settings = Settings.from_env()
    db = connect(settings.db_path)
    lock = settings.db_path.with_suffix(".worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    feed, adapter, agents = PriceFeed(db, settings), PaperAdapter(db, settings), Agents(db)
    practice = Practice(db, settings, agents.model)
    put(db, "ai_model_configured", bool(agents.model.ready))
    chain = None
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_feed = last_push = last_warmup = 0
    while running:
        now = time.time()
        try:
            if now - last_feed >= 5:
                feed.poll()
                last_feed = now
            if now - last_warmup >= 300:
                feed.bootstrap_candles()
                last_warmup = now
            # Practice never instantiates Chain or queues transactions. It runs even
            # while the separate vault operator is disabled or its RPC is offline.
            try:
                practice.tick()
                health(db,"practice",True,"Practice engine running; wallet funds are not used")
            except Exception as exc:
                health(db,"practice",False,"Practice cycle interrupted; retrying")
                log(db,"engine","practice_cycle_failed","warning",error=type(exc).__name__)
            if chain is None and settings.config.get("deployment"):
                chain = Chain(db, settings, os.getenv("VELA_OPERATOR_PRIVATE_KEY"))
            if chain is None:
                health(db, "events", False, "Contracts not deployed")
                health(db, "engine", False, "Waiting for a confirmed on-chain session")
                health(db, "operator", False, "Contracts not deployed")
            else:
                caught_up = chain.sync()
                last_push = queue_active_price(db, settings, now, last_push)
                if caught_up:
                    process_requests(chain, agents.model.ready)
                    # Quotes/events still refresh while writes are disabled. No new
                    # paper entries can create unsettled liabilities during maintenance.
                    if settings.chain_writes:
                        adapter.tick(agents)
                    chain.drain()
                    health(db, "engine", settings.chain_writes,
                           "Confirmed chain cursor caught up" if settings.chain_writes else
                           "Trading paused while network fee controls and vault upgrade are verified")
                    if settings.chain_writes:
                        agents.tick_ai()
                else:
                    health(db, "engine", False, "Catching up events; new fills paused")
                health(db, "agents", agents.model.ready, "Research model configured" if agents.model.ready else "AI research is not connected; configure dedicated Vela model credentials")
        except Exception as exc:
            health(db, "engine", False, "Cycle failed; entries paused")
            health(db, "events", False, type(exc).__name__)
            log(db, "worker", "cycle_failed", "warning", error=type(exc).__name__)
        time.sleep(1)
    db.close()
    practice.close()
    lock.close()


if __name__ == "__main__":
    main()
