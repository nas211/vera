import time,uuid,json,hashlib
from decimal import Decimal as D
from vela.db import connect, transaction, get, put
from .risk import RISK

def open_db(path):
    db=connect(path)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS auth_nonces(id TEXT PRIMARY KEY,address TEXT,message TEXT,expires REAL,used INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS auth_sessions(hash TEXT PRIMARY KEY,address TEXT,expires REAL);
    CREATE TABLE IF NOT EXISTS vaults(owner TEXT PRIMARY KEY,address TEXT UNIQUE,data TEXT,checked REAL);
    CREATE TABLE IF NOT EXISTS loans(id TEXT PRIMARY KEY,owner TEXT,vault TEXT,nonce INTEGER,principal TEXT,interest TEXT,
      original TEXT,paid TEXT,last REAL,apr TEXT,state TEXT,seized TEXT DEFAULT '0',bad_debt TEXT DEFAULT '0',
      UNIQUE(vault,nonce));
    CREATE TABLE IF NOT EXISTS loan_events(id INTEGER PRIMARY KEY,owner TEXT,loan TEXT,at REAL,kind TEXT,amount TEXT,detail TEXT);
    CREATE TABLE IF NOT EXISTS operations(owner TEXT,id TEXT,body TEXT,result TEXT,PRIMARY KEY(owner,id));
    CREATE TABLE IF NOT EXISTS chain_receipts(hash TEXT PRIMARY KEY,owner TEXT,block INTEGER,block_hash TEXT);
    ''')
    with transaction(db):
        if get(db,'pool') is None: put(db,'pool',dict(cash=RISK['initialDemoLiquidity'],buyerCash=RISK['demoLiquidationLiquidity'],interestIncome='0',seizedDoge='0'))
    return db

def account(db,owner):
    db.execute("INSERT OR IGNORE INTO users(address,session,strategy,status,initial,balance,doge_in,started,start_tx) VALUES (?,?,1,'active','0','0','0',?,'local-demo-ledger')",(owner,uuid.uuid4().hex,time.time()))
    if get(db,'paper-prefs:'+owner) is None: put(db,'paper-prefs:'+owner,dict(autonomous=False,generation=0,leverage='1'))

def cashflow(db,owner,amount):
    row=db.execute('SELECT balance,initial FROM users WHERE address=?',(owner,)).fetchone()
    db.execute('UPDATE users SET balance=?,initial=? WHERE address=?',(format(D(row['balance'])+amount,'f'),format(D(row['initial'])+amount,'f'),owner))

def event(db,owner,loan,kind,amount='0',detail=''):
    db.execute('INSERT INTO loan_events(owner,loan,at,kind,amount,detail) VALUES (?,?,?,?,?,?)',(owner,loan,time.time(),kind,str(amount),detail))

def once(db,owner,key,body,fn):
    if not isinstance(key,str) or not 8<=len(key)<=120: raise ValueError('Idempotency key required')
    encoded=json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False)
    with transaction(db):
        old=db.execute('SELECT * FROM operations WHERE owner=? AND id=?',(owner,key)).fetchone()
        if old:
            if old['body']!=encoded: raise ValueError('Idempotency key conflict')
            return json.loads(old['result'])
        result=fn()
        db.execute('INSERT INTO operations VALUES (?,?,?,?)',(owner,key,encoded,json.dumps(result)))
        return result
