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


def main():
    settings = Settings.from_env()
    db = connect(settings.db_path)
    lock = settings.db_path.with_suffix(".worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    feed, adapter, agents = PriceFeed(db, settings), PaperAdapter(db, settings), Agents(db)
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
            if chain is None and settings.config.get("deployment"):
                chain = Chain(db, settings, os.getenv("VELA_OPERATOR_PRIVATE_KEY"))
            if chain is None:
                health(db, "events", False, "Contracts not deployed")
                health(db, "engine", False, "Waiting for a confirmed on-chain session")
                health(db, "operator", False, "Contracts not deployed")
            else:
                caught_up = chain.sync()
                # Also prime price while idle so the very first start can succeed.
                active = db.execute("SELECT 1 FROM users WHERE status IN ('active','stopping')").fetchone()
                if now - last_push >= (30 if active else 60):
                    pending = db.execute("SELECT 1 FROM outbox WHERE kind='updatePrice' AND status IN ('queued','signed','sent')").fetchone()
                    if not pending:
                        enqueue(db, "price:" + str(int(now)), "updatePrice", {})
                    last_push = now
                if caught_up:
                    adapter.tick(agents)
                    chain.drain()
                    health(db, "engine", True, "Paper only; confirmed chain cursor caught up")
                    agents.tick_ai()
                else:
                    health(db, "engine", False, "Catching up events; new fills paused")
                health(db, "agents", True, "Unified research model configured" if agents.model.ready else "Unified signal/risk agents active; AI research requires VELA_MODEL and VELA_MODEL_KEY")
        except Exception as exc:
            health(db, "engine", False, "Cycle failed; entries paused")
            health(db, "events", False, type(exc).__name__)
            log(db, "worker", "cycle_failed", "warning", error=type(exc).__name__)
        time.sleep(1)
    db.close()
    lock.close()


if __name__ == "__main__":
    main()
