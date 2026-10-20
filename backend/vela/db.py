import json
import sqlite3
import time
from contextlib import contextmanager


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=15000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(address TEXT PRIMARY KEY, session TEXT NOT NULL,
      strategy INTEGER NOT NULL, status TEXT NOT NULL, initial TEXT NOT NULL,
      balance TEXT NOT NULL, doge_in TEXT NOT NULL, started REAL NOT NULL,
      start_tx TEXT NOT NULL, stop_tx TEXT, settlement_tx TEXT, doge_out TEXT,
      final_pnl TEXT, nonce INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY, user TEXT NOT NULL,
      session TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
      qty TEXT NOT NULL, entry TEXT NOT NULL, stop TEXT NOT NULL, target TEXT NOT NULL,
      entry_fee_left TEXT NOT NULL, paid_fees TEXT NOT NULL, realized_gross TEXT NOT NULL,
      opened REAL NOT NULL, expires REAL NOT NULL, leverage TEXT NOT NULL,
      UNIQUE(user,symbol));
    CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, user TEXT NOT NULL,
      session TEXT NOT NULL, strategy INTEGER NOT NULL, position_id TEXT NOT NULL,
      symbol TEXT NOT NULL, side TEXT NOT NULL, qty TEXT NOT NULL, entry TEXT NOT NULL,
      exit TEXT NOT NULL, gross TEXT NOT NULL, fees TEXT NOT NULL, pnl TEXT NOT NULL,
      pnl_micro INTEGER NOT NULL, opened REAL NOT NULL, closed REAL NOT NULL,
      reason TEXT NOT NULL, tx_hash TEXT, chain_status TEXT NOT NULL DEFAULT 'queued');
    CREATE TABLE IF NOT EXISTS equity(id INTEGER PRIMARY KEY, user TEXT, session TEXT,
      ts REAL NOT NULL, equity TEXT NOT NULL, pnl TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS prices(symbol TEXT PRIMARY KEY, value TEXT, ts REAL,
      source TEXT, safe INTEGER NOT NULL, reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candles(symbol TEXT, ts INTEGER, o REAL, h REAL,
      l REAL, c REAL, PRIMARY KEY(symbol,ts));
    CREATE TABLE IF NOT EXISTS events(chain_id INTEGER, tx TEXT, log_index INTEGER,
      block INTEGER, block_hash TEXT, kind TEXT, payload TEXT,
      PRIMARY KEY(chain_id,tx,log_index));
    CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
      payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
      tx_hash TEXT, raw_tx TEXT, created REAL NOT NULL, error TEXT);
    CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, ts REAL, service TEXT,
      level TEXT, message TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS services(name TEXT PRIMARY KEY, ts REAL,
      ok INTEGER NOT NULL, detail TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS network_fees(tx_hash TEXT PRIMARY KEY,
      reserved_wei TEXT NOT NULL, actual_wei TEXT, created REAL NOT NULL,
      confirmed REAL, kind TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS start_quotes(id TEXT PRIMARY KEY, user TEXT NOT NULL,
      strategy INTEGER NOT NULL, value_wei TEXT NOT NULL, created REAL NOT NULL,
      status TEXT NOT NULL, result TEXT, error TEXT);
    CREATE INDEX IF NOT EXISTS trades_user ON trades(user,closed);
    CREATE INDEX IF NOT EXISTS equity_user ON equity(user,ts);
    """)
    return db


@contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def packed(x):
    return json.dumps(x, separators=(",", ":"), allow_nan=False)


def get(db, key, default=None):
    r = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def put(db, key, value):
    db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, packed(value)))


def log(db, service, message, level="info", **detail):
    db.execute("INSERT INTO logs(ts,service,level,message,detail) VALUES (?,?,?,?,?)",
               (time.time(), service, level, message, packed(detail)))
    print(packed(dict(ts=time.time(), service=service, level=level, message=message, **detail)), flush=True)


def health(db, service, ok, detail):
    db.execute("INSERT OR REPLACE INTO services VALUES (?,?,?,?)",
               (service, time.time(), int(ok), detail))


def enqueue(db, key, kind, payload):
    db.execute("INSERT OR IGNORE INTO outbox(id,kind,payload,created) VALUES (?,?,?,?)",
               (key, kind, packed(payload), time.time()))
