"""Independent paper database. One transaction commits state plus the audit trail."""
import json
import sqlite3
from .rules import packed

class Store:
    def __init__(self, path):
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS paper_accounts(user_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_events(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL,
            chain_id TEXT NOT NULL, ts REAL NOT NULL, stage TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS paper_events_owner ON paper_events(user_id,id);
        CREATE TABLE IF NOT EXISTS paper_meta(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_archives(user_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, archived_at REAL NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(user_id,candidate_id));
        CREATE INDEX IF NOT EXISTS paper_archive_fingerprint ON paper_archives(user_id,fingerprint);
        ''')
    def load(self): return {u:json.loads(p) for u,p in self.db.execute('SELECT user_id,payload FROM paper_accounts')}
    def save(self, accounts, events, archives=()):
        with self.db:
            for user,c,summary,at in archives:
                self.db.execute('INSERT INTO paper_archives VALUES (?,?,?,?,?,?)',
                    (user,c['id'],summary['fingerprint'],at,packed(summary),packed(c)))
            for user,a in accounts.items():
                self.db.execute('INSERT INTO paper_accounts VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload',(user,packed(a)))
            self.db.executemany('INSERT INTO paper_events(user_id,chain_id,ts,stage,payload) VALUES (?,?,?,?,?)',
                [(e['user'],e['chain'],e['ts'],e['stage'],packed(e['data'])) for e in events])
    def archived_duplicate(self,user,cid,fingerprint):
        return self.db.execute('SELECT 1 FROM paper_archives WHERE user_id=? AND (candidate_id=? OR fingerprint=?) LIMIT 1',(user,cid,fingerprint)).fetchone() is not None
    def archived_summaries(self,user,limit=24):
        return [json.loads(p) for p, in self.db.execute('SELECT summary FROM paper_archives WHERE user_id=? ORDER BY archived_at DESC,candidate_id DESC LIMIT ?',(user,limit))]
    def archives(self,user,offset=0,limit=25):
        return [dict(summary=json.loads(s),candidate=json.loads(p)) for s,p in self.db.execute('SELECT summary,payload FROM paper_archives WHERE user_id=? ORDER BY archived_at DESC,candidate_id DESC LIMIT ? OFFSET ?',(user,limit,offset))]
    def last_kill(self,user):
        row=self.db.execute("SELECT ts,payload FROM paper_events WHERE user_id=? AND stage='kill' ORDER BY id DESC LIMIT 1",(user,)).fetchone()
        return dict(at=row[0],**json.loads(row[1])) if row else None
    def events(self,user):
        return [dict(id=i,chain_id=c,ts=t,stage=s,data=json.loads(p)) for i,c,t,s,p in self.db.execute(
            'SELECT id,chain_id,ts,stage,payload FROM paper_events WHERE user_id=? ORDER BY id DESC LIMIT 100',(user,))]
    def meta(self,key,default=None):
        row=self.db.execute('SELECT payload FROM paper_meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put_meta(self,key,value):
        with self.db:self.db.execute('INSERT INTO paper_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',(key,packed(value)))
