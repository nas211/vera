import hashlib
import json
from pathlib import Path
import sqlite3
import time


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(spec):
    return hashlib.sha256(packed(spec).encode()).hexdigest()


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('pragma journal_mode=WAL')
    db.execute('pragma busy_timeout=10000')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS candidates(
      id TEXT PRIMARY KEY, spec TEXT NOT NULL, origin TEXT NOT NULL,
      stage TEXT NOT NULL DEFAULT 'QUEUED', created REAL NOT NULL,
      screened REAL, paper_started REAL, historical TEXT, fingerprint TEXT,
      reason TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS positions(
      id TEXT PRIMARY KEY, candidate TEXT NOT NULL, market TEXT NOT NULL,
      side TEXT NOT NULL, entry REAL NOT NULL, notional REAL NOT NULL,
      target REAL NOT NULL, stop REAL NOT NULL, opened REAL NOT NULL,
      expires REAL NOT NULL, last_quote REAL NOT NULL,
      evidence_ok INTEGER NOT NULL DEFAULT 1, UNIQUE(candidate,market));
    CREATE TABLE IF NOT EXISTS trades(
      id TEXT PRIMARY KEY, candidate TEXT NOT NULL, market TEXT NOT NULL,
      side TEXT NOT NULL, opened REAL NOT NULL, closed REAL NOT NULL,
      entry REAL NOT NULL, exit REAL NOT NULL, notional REAL NOT NULL,
      gross REAL NOT NULL, costs REAL NOT NULL, pnl REAL NOT NULL,
      reason TEXT NOT NULL, evidence_ok INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS seen(candidate TEXT,market TEXT,bar INTEGER,
      PRIMARY KEY(candidate,market,bar));
    CREATE TABLE IF NOT EXISTS signals(
      id TEXT PRIMARY KEY,candidate TEXT,market TEXT,side TEXT,bar INTEGER,
      ts REAL,expires REAL,price REAL);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL,kind TEXT,
      candidate TEXT,detail TEXT);
    CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT);
    CREATE INDEX IF NOT EXISTS trade_candidate ON trades(candidate,closed);
    CREATE INDEX IF NOT EXISTS candidate_stage ON candidates(stage);
    ''')
    db.commit()
    return db


def event(db, kind, candidate=None, **detail):
    db.execute('insert into events(ts,kind,candidate,detail) values(?,?,?,?)',
               (time.time(), kind, candidate, packed(detail)))


def put(db, key, value):
    db.execute('insert or replace into kv values(?,?)', (key, packed(value)))


def get(db, key, default=None):
    row = db.execute('select value from kv where key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(packed(value)+'\n')
    tmp.replace(path)
