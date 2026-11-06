"""Chain boundary regressions. No user keys, production RPC, or ledger writes."""
import copy
import json
from types import SimpleNamespace
import pytest
from hexbytes import HexBytes
from web3 import Web3
from autodefi.doge_lending import configuration, public_configuration, DogeLendingReader

OWNER='0x'+'11'*20
MARKET='0x'+'22'*20
HASH=HexBytes('0x'+'33'*32)
CODE=HexBytes('0x6000')
def settings():
    return dict(schemaVersion=1,chainId=6281971,enabled=True,market=MARKET,
        manager=OWNER,runtimeCodeHash=Web3.keccak(CODE).to_0x_hex(),deploymentBlock=1,
        confirmations=3,conversion=dict(enabled=False,reason='Not connected'))

class Eth:
    chain_id=6281971
    def __init__(self):
        self.head_age=0;self.code=CODE;self.backing=110*10**18;self.reorg=False;self.blocks=0
        self.owner=OWNER;self.reads=[];self.tx=None;self.receipt=None
    def get_block(self,n):
        if n==98:self.blocks+=1
        return SimpleNamespace(number=100 if n=='latest' else n,timestamp=1000-self.head_age,
            hash=HexBytes('0x'+'44'*32) if self.reorg and self.blocks>1 else HASH)
    def get_code(self,*args,**kwargs):return self.code
    def get_balance(self,a,**kwargs):return self.backing if a.lower()==MARKET else 20*10**18
    def contract(self,*args,**kwargs):
        eth=self
        class Functions:
            def __getattr__(self,name):
                def f(*args):
                    def call(block_identifier):
                        eth.reads.append(block_identifier)
                        return dict(deployedChainId=6281971,owner=eth.owner,MAX_LTV_BPS=7000,RISK_LTV_BPS=8000,APR_BPS=500,
                            position=(10*10**18,4*10**18,0,0,1000,4*10**18,2),liquidity=96*10**18,
                            totalCollateral=10*10**18,totalAvailable=4*10**18,capacity=3*10**18,borrowingPaused=False)[name]
                    return SimpleNamespace(call=call)
                return f
        return SimpleNamespace(functions=Functions())
    def get_transaction(self,tx):return self.tx
    def get_transaction_receipt(self,tx):return self.receipt

def reader():
    eth=Eth();return DogeLendingReader(SimpleNamespace(eth=eth),settings(),clock=lambda:1000),eth

def test_configuration_stays_off_without_verified_deployment(tmp_path):
    path=tmp_path/'market.json';d=settings();d.update(enabled=False,market=None,runtimeCodeHash=None,manager=None,deploymentBlock=None)
    path.write_text(json.dumps(d));c=public_configuration(configuration(path))
    assert c['enabled'] is False and c['paperProfitsRedeemable'] is False
    d['enabled']=True;path.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='roles'):configuration(path)

@pytest.mark.parametrize('bad', [True,1,'true',None])
def test_config_cannot_enable_unimplemented_conversion(tmp_path,bad):
    d=settings();d['conversion']['enabled']=bad;p=tmp_path/'config.json';p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='settlement adapter'):configuration(p)

def test_confirmed_native_amounts_are_exact_and_never_demo_usd():
    r,e=reader();s=r.snapshot(OWNER)
    assert s['debtWei']==str(4*10**18) and s['position']['available']==str(4*10**18)
    assert s['availableToBorrowWei']==str(3*10**18) and s['source']=='chain'
    assert set(e.reads)=={98} and s['confirmations']==3

@pytest.mark.parametrize('case', ['chain','stale','bytecode','manager','backing','reorg'])
def test_wrong_stale_unbacked_or_reorganized_state_rejected(case):
    r,e=reader()
    if case=='chain':e.chain_id=1
    elif case=='stale':e.head_age=100
    elif case=='bytecode':e.code=HexBytes('0x60')
    elif case=='manager':e.owner=MARKET
    elif case=='backing':e.backing=109*10**18
    elif case=='reorg':e.reorg=True
    with pytest.raises(ValueError):r.snapshot(OWNER)

def test_matching_receipt_requires_canonical_owner_and_market():
    r,e=reader();tx='0x'+'55'*32
    e.tx={'from':OWNER,'to':MARKET,'input':HexBytes('0x1234'),'value':0}
    e.receipt=SimpleNamespace(blockNumber=98,blockHash=HASH,status=1)
    s=r.snapshot(OWNER,tx);assert s['transaction']['status']=='CONFIRMED'
    e.tx['from']=MARKET
    with pytest.raises(ValueError,match='different wallet'):r.snapshot(OWNER,tx)

def test_reverted_receipt_never_counts_as_paid():
    r,e=reader();e.tx={'from':OWNER,'to':MARKET,'input':HexBytes('0x1234'),'value':0}
    e.receipt=SimpleNamespace(blockNumber=98,blockHash=HASH,status=0)
    assert r.snapshot(OWNER,'0x'+'55'*32)['transaction']['status']=='REVERTED'
