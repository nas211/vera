"""Owner activation never trusts an unrelated, reverted or unfunded transaction."""
import time
from types import SimpleNamespace
import pytest
from hexbytes import HexBytes
from web3 import Web3
from tools import activate_doge_lending as activation

OWNER='0x'+'11'*20
MARKET='0x'+'22'*20
OTHER='0x'+'33'*20
HASH=HexBytes('0x'+'44'*32)
CODE=HexBytes('0x6000')

def fixture(monkeypatch):
    inputs=dict(owner=OWNER,manager=OWNER,data='0x1234',runtimeCodeHash=Web3.keccak(CODE).to_0x_hex())
    eth=SimpleNamespace(chain_id=6281971)
    receipts={k:SimpleNamespace(status=1,blockNumber=b,blockHash=HASH,contractAddress=MARKET) for k,b in [('deploy',90),('fund',95)]}
    txs=dict(deploy={'from':OWNER,'to':None,'value':0,'input':HexBytes(inputs['data'])},
             fund={'from':OWNER,'to':MARKET,'value':100*10**18,'input':Web3.keccak(text='fund()')[:4]})
    eth.get_block=lambda n:SimpleNamespace(number=100 if n=='latest' else n,timestamp=time.time(),hash=HASH)
    eth.get_transaction_receipt=lambda h:receipts[h]
    eth.get_transaction=lambda h:txs[h]
    eth.get_code=lambda *args,**kwargs:CODE
    observed={'liquidityWei':str(100*10**18)}
    monkeypatch.setattr(activation.DogeLendingReader,'snapshot',lambda *args:observed)
    return SimpleNamespace(eth=eth),inputs,txs,receipts,observed

def test_funded_verified_market_produces_only_native_testnet_config(monkeypatch):
    w,i,_,_,_=fixture(monkeypatch)
    config,evidence=activation.verify(w,'deploy','fund',i)
    assert config['enabled'] and config['market']==MARKET and config['chainId']==6281971
    assert config['conversion']['enabled'] is False and evidence['fundingWei']==str(100*10**18)

@pytest.mark.parametrize('transaction,field,value',[
    ('deploy','from',OTHER),('deploy','to',MARKET),('deploy','value',1),('deploy','input',HexBytes('0x1235')),
    ('fund','from',OTHER),('fund','to',OTHER),('fund','value',0),('fund','input',HexBytes('0x1234')),
])
def test_wrong_owner_or_transaction_cannot_activate(monkeypatch,transaction,field,value):
    w,i,txs,_,_=fixture(monkeypatch);txs[transaction][field]=value
    with pytest.raises(ValueError,match='does not match'):activation.verify(w,'deploy','fund',i)

@pytest.mark.parametrize('transaction',['deploy','fund'])
@pytest.mark.parametrize('field,value',[('status',0),('blockNumber',99),('blockHash',HexBytes('0x'+'55'*32))])
def test_reverted_unconfirmed_or_reorganized_receipt_is_rejected(monkeypatch,transaction,field,value):
    w,i,_,receipts,_=fixture(monkeypatch);setattr(receipts[transaction],field,value)
    with pytest.raises(ValueError,match='Canonical successful'):activation.verify(w,'deploy','fund',i)

def test_spent_pool_and_changed_runtime_are_not_activated(monkeypatch):
    w,i,_,_,observed=fixture(monkeypatch);observed['liquidityWei']='0'
    with pytest.raises(ValueError,match='Funded lending'):activation.verify(w,'deploy','fund',i)
    w.eth.get_code=lambda *args,**kwargs:HexBytes('0x6001')
    with pytest.raises(ValueError,match='runtime'):activation.verify(w,'deploy','fund',i)

def test_reorg_during_market_read_is_rejected(monkeypatch):
    w,i,_,_,observed=fixture(monkeypatch)
    def read(*args):
        w.eth.get_block=lambda n:SimpleNamespace(hash=HexBytes('0x'+'55'*32))
        return observed
    monkeypatch.setattr(activation.DogeLendingReader,'snapshot',read)
    with pytest.raises(ValueError,match='during verification'):activation.verify(w,'deploy','fund',i)
