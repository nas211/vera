import sys,time,json,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pytest
from decimal import Decimal as D
from eth_account import Account
from autodefi.config import Config,ROOT
from autodefi.store import open_db,account
from autodefi.lending import Lending
from autodefi.trading import GovernedPaper
from vela.lighter.feed import LighterFeed,Market
from vela.db import transaction,put

ALICE=Account.from_key(bytes.fromhex('01'*32))
BOB=Account.from_key(bytes.fromhex('02'*32))
ATTESTOR=Account.from_key(bytes.fromhex('03'*32))

def fixture_feed():
    f=LighterFeed()
    for i,(symbol,price) in enumerate((('BTC/USD',60000),('ETH/USD',3000),('SOL/USD',150))):
        p=D(price);m=Market(symbol,i,D('.01'),D('.00001'),D('.00001'),D(1),D(3),D('.05'))
        m.mark=m.index=p;m.funding_rate=D(0);m.nonce=1;m.book_at=m.stats_at=time.time()
        m.bids={p-D('.01'):D(10000)};m.asks={p+D('.01'):D(10000)}
        end=int(time.time())//300*300
        m.candles=[dict(ts=end-300*(250-j),o=price,h=price+1,l=price-1,c=price) for j in range(250)]
        f.markets[symbol]=m
    f.connected=True;f.error='LOCAL FIXTURE — not live data'
    return f

def fixture_vault(owner,nonce=1,amount=800):
    return dict(address='0x'+'ab'*20,owner=owner,chainId=6281971,block=100,blockHash='LOCAL_FIXTURE',
        collateral=str(10000*10**18),withdrawable='0',loanState=1,loanId='0x'+format(nonce,'064x'),loanNonce=nonce,
        principalMicro=str(amount*1000000),paused=False,trading=False,agent=ATTESTOR.address,permissionRevision=1,
        settlementSigner=ATTESTOR.address,badDebtMicro='0',limits=dict(allocation='800000000',positionSize='200000000',expiresAt=str(int(time.time())+3600),maxLoss='2000000',leverage='1',markets='7',autoReduce=False))

def save_vault(db,v):db.execute('INSERT OR REPLACE INTO vaults VALUES (?,?,?,?)',(v['owner'],v['address'],json.dumps(v),time.time()))

@pytest.fixture
def env(monkeypatch):
    frozen=time.time();monkeypatch.setattr(time,'time',lambda:frozen)
    p=ROOT/'.runtime/lighter-showcase'/('test-'+uuid.uuid4().hex+'.sqlite3')
    config=Config(db_path=p,attestor_key=ATTESTOR.key.hex())
    db=open_db(p);feed=fixture_feed();paper=GovernedPaper(db,config.paper(),feed);lending=Lending(db)
    owner=ALICE.address.lower();v=fixture_vault(owner)
    with transaction(db):account(db,owner);save_vault(db,v);lending.quote(owner,'0')
    yield dict(db=db,feed=feed,paper=paper,lending=lending,owner=owner,v=v,config=config)
    db.close()
    for suffix in ('','-wal','-shm','.lock'):Path(str(p)+suffix).unlink(missing_ok=True)

def funded(e):
    with transaction(e['db']):e['lending'].borrow(e['owner'])
    e['v'].update(loanState=2,trading=True)
    with transaction(e['db']):save_vault(e['db'],e['v']);put(e['db'],'paper-prefs:'+e['owner'],dict(autonomous=True,generation=1))

def order(side='long',**kwargs):return dict(symbol='BTC/USD',side=side,type='market',leverage='1',notional='100',riskBudget='2',stopBps='50',targetBps='100',**kwargs)

def fill(e,side='long',**kwargs):
    with transaction(e['db']):r=e['paper'].submit(e['owner'],order(side,**kwargs),'test:'+uuid.uuid4().hex)
    e['db'].execute('UPDATE paper_orders SET due=0 WHERE id=?',(r['id'],));e['paper'].tick()
    p=e['paper'].getPosition(e['owner'])
    assert p,[dict(x) for x in e['db'].execute('SELECT status,error FROM paper_orders')]
    return p[0]
