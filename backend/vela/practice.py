"""Independent simulation ledger: no blockchain signer, outbox or wallet funds."""
import hashlib
import re
import time
import uuid
from dataclasses import replace
from decimal import Decimal as D
from .db import connect, get, put, transaction
from .paper import PaperAdapter, money
from .prices import quote, entries_allowed
from .agents import Agents


def practice_settings(settings):
    return replace(settings, db_path=settings.db_path.with_name("practice.sqlite3"), chain_writes=False)


def identity(token):
    if not token or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Invalid practice session")
    return "practice:" + hashlib.sha256(token.encode()).hexdigest()


def start(db, token, amount):
    address = identity(token)
    amount = D(amount)
    if not amount.is_finite() or not D(".01") <= amount <= D("100000"):
        raise ValueError("Choose 0.01 to 100,000 virtual DOGE")
    if not entries_allowed(db, "DOGE/USD"):
        raise ValueError("Waiting for fresh market prices")
    credit = money(amount * quote(db, "DOGE/USD"))
    if credit <= 0:
        raise ValueError("Amount is too small")
    with transaction(db):
        existing = db.execute("SELECT * FROM users WHERE address=?", (address,)).fetchone()
        if existing and existing["status"] in ("active","stopping"):
            return dict(existing)  # Duplicate clicks/timeouts must not reset the book.
        if existing and time.time()-existing["started"] < 60:
            raise ValueError("Wait one minute before starting another portfolio")
        if db.execute("SELECT count(*) FROM users WHERE status IN ('active','stopping')").fetchone()[0] >= 20:
            raise ValueError("All practice slots are in use; retry later")
        session = uuid.uuid4().hex
        db.execute("""INSERT OR REPLACE INTO users(address,session,strategy,status,initial,balance,doge_in,started,start_tx)
                      VALUES (?,?,1,'active',?,?,?,?, '')""",
                   (address,session,str(credit),str(credit),str(int(amount*10**18)),time.time()))
        put(db, "ai-approved:"+address, None)
        put(db, "ai-stage:"+address, {})
        return dict(db.execute("SELECT * FROM users WHERE address=?", (address,)).fetchone())


def stop(db, token):
    address = identity(token)
    db.execute("UPDATE users SET status='stopping' WHERE address=? AND status='active'", (address,))


def snapshot(db, token, settings):
    address = identity(token)
    row = db.execute("SELECT * FROM users WHERE address=?", (address,)).fetchone()
    if not row:
        return dict(user=None, positions=[], trades=[], equity=[], decision=None, mode="practice")
    user = dict(row)
    adapter = PaperAdapter(db, settings, on_chain=False)
    positions = adapter.getPosition(address)
    for p in positions:
        try:
            mark = adapter.getPrice(p["symbol"])
            sign = D(1) if p["side"] == "long" else D(-1)
            p["mark"] = str(mark)
            p["unrealized"] = str((mark-D(p["entry"]))*D(p["qty"])*sign-D(p["entry_fee_left"]))
        except ValueError:
            p["mark"] = p["unrealized"] = None
    try:
        equity = adapter.equity(user)
        user.update(equity=str(equity),pnl=str(equity-D(user["initial"])),
                    pnlPercent=str((equity/D(user["initial"])-1)*100),
                    dogeEquivalent=str(max(D(0),equity)/quote(db,"DOGE/USD")))
    except ValueError:
        user.update(equity=None,pnl=None,pnlPercent=None,dogeEquivalent=None)
    trades = [dict(r) for r in db.execute("SELECT * FROM trades WHERE session=? ORDER BY closed DESC LIMIT 100",(user["session"],))]
    history = [dict(r) for r in db.execute("SELECT ts,equity,pnl FROM equity WHERE session=? ORDER BY ts DESC LIMIT 500",(user["session"],))][::-1]
    decision = get(db,"ai-decision:"+address)
    if decision and decision.get("session") != user["session"]:
        decision = None
    return dict(user=user,positions=positions,trades=trades,equity=history,decision=decision,mode="practice",
                scope="Current practice session; simulated trading fees included; no blockchain fees")


class Practice:
    def __init__(self, source_db, settings, model):
        self.source = source_db
        self.settings = practice_settings(settings)
        self.db = connect(self.settings.db_path)
        self.adapter = PaperAdapter(self.db, self.settings, on_chain=False)
        self.agents = Agents(self.db, model)
        self.last_candles = 0

    def tick(self):
        now = time.time()
        self.db.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)",
                           [tuple(r) for r in self.source.execute("SELECT * FROM prices")])
        if now-self.last_candles >= 30:
            self.db.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)",
                [tuple(r) for r in self.source.execute("SELECT * FROM candles WHERE ts>?", (now-300*310,))])
            self.last_candles = now
        # Bound abandoned browser sessions to 24 hours; prices still must be fresh.
        self.db.execute("UPDATE users SET status='stopping' WHERE status='active' AND started<?", (now-86400,))
        self.adapter.tick(self.agents)
        self.agents.tick_ai()

    def close(self):
        self.db.close()
