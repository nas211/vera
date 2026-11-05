"""Independent canonical receipt checks with deterministic RPC observations."""
import time
import pytest
from autodefi.dogeos_readonly import DogeOSReadOnly

TX='0x'+'ab'*32
BLOCK='0x'+'cd'*32

class Reader(DogeOSReadOnly):
    def __init__(self):
        super().__init__();self.changed=None;self.status=1;self.receipt_height=98
        self.owner=self.roles['vaultOwner']
    def code(self,*args):pass # Receipt-only fixture; runtime hashes covered by other checks.
    def call(self,target,contract,name,block,args=()):
        return {'settlementSigner':self.roles['settlementAttestor'],'liquidationTreasury':self.roles['liquidationTreasury'],
            'vaultOf':self.reference,'predict':self.reference,'owner':self.owner,'collateral':10**18,
            'loanState':0,'paused':False,'trading':False,'withdrawable':10**18}[name]
    def rpc(self,method,params):
        if method=='eth_chainId':return hex(6281971)
        if method=='eth_getBalance':return hex(10**18)
        if method=='eth_getBlockByNumber':return dict(number=hex(100),timestamp=hex(int(time.time())),hash='bad' if self.changed=='reorg' and params[0]==hex(self.receipt_height) else BLOCK)
        if method=='eth_getTransactionReceipt':return dict(transactionHash=TX,blockNumber=hex(self.receipt_height),blockHash=BLOCK,status=hex(self.status))
        if method=='eth_getTransactionByHash':return dict(hash=TX,blockHash='bad' if self.changed=='block' else BLOCK,
            to=self.factory if self.changed!='target' else '0x'+'00'*20,
            **{'from':self.owner if self.changed!='owner' else '0x'+'12'*20},value='0x0',input='0x1234')
        raise AssertionError(method)

@pytest.mark.parametrize('changed',['owner','target','block','reorg'])
def test_rejects_wrong_identity_and_reorganization(changed):
    reader=Reader();reader.changed=changed
    with pytest.raises(ValueError):reader.snapshot(transaction=TX)

@pytest.mark.parametrize('status',[0,1])
def test_only_confirmed_canonical_receipt_and_wallet_vault_state(status):
    reader=Reader();reader.status=status
    state=reader.snapshot(transaction=TX)
    assert state['transaction']['status']==('CONFIRMED' if status else 'REVERTED')
    assert state['vault']['withdrawableWei']==str(10**18)
    reader.receipt_height=99
    assert reader.snapshot(transaction=TX)['transaction']['status']=='PENDING'
