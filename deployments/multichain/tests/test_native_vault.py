"""Canonical native reads with deterministic Web3 observations; no RPC or wallet."""
import json
import time
from types import SimpleNamespace as NS
import pytest
from web3 import Web3
from web3.exceptions import TransactionNotFound
from autodefi import native_vault
from autodefi.chain import DogeOS
from autodefi.config import Config, CHAIN_ID

OWNER='0x'+'11'*20
FACTORY='0x'+'22'*20
VAULT='0x'+'33'*20
LEGACY='0x'+'44'*20
POOL='0x'+'66'*20
DENOMINATOR=10_000*365*86400
SIGNER='0x'+'55'*20
TX='0x'+'ab'*32
BLOCK=bytes.fromhex('cd'*32)
CODE=b'local reviewed runtime'
CODEHASH='0x'+Web3.keccak(CODE).hex().removeprefix('0x')
ZERO='0x'+'00'*20

class Functions:
    def __init__(self, values): self.values=values
    def __getattr__(self, name):
        def invoke(*args):
            def call(block_identifier=None):
                assert block_identifier==98
                value=self.values[name]
                return value(*args) if callable(value) else value
            return NS(call=call)
        return invoke

class Eth:
    chain_id=CHAIN_ID
    def __init__(self):
        self.mapped=VAULT;self.legacy=ZERO;self.balance=1000;self.pool_balance=100;self.runtime=CODE
        self.receipt=None;self.sent=None;self.head_age=0;self.reorg=False;self.balance_reorg=False;self.reorg_after=False
        self.v=dict(owner=OWNER,settlementSigner=SIGNER,liquidationTreasury=SIGNER,
                    collateral=1000,withdrawable=0,loanState=2,loanId=bytes.fromhex('01'*32),loanNonce=1,
                    principalMicro=1000000,paused=False,trading=False,agent=SIGNER,permissionRevision=1,badDebtMicro=0,
                    limits=(100000000,50000000,int(time.time())+3600,500000,1,7,False),
                    expectedChainId=CHAIN_ID,rewardManager=SIGNER,rewardPool=POOL,previewRewards=(20,0),rewardReserve=100,
                    stopBudgetMicro=500000,aprBps=500,totalRewardsClaimed=10,totalRewardsFunded=110)
        self.p=dict(factory=FACTORY,rewardManager=SIGNER,expectedChainId=CHAIN_ID,rewardReserve=100,totalRewardsFunded=110,totalRewardsClaimed=10,
                    DENOMINATOR=DENOMINATOR,previewIndex=(0,20*DENOMINATOR),availableReserve=80,totalPrincipal=1000,aprBps=500,
                    accounts=lambda _:(OWNER,int(self.v['collateral']),0,20*DENOMINATOR,10),preview=lambda _:self.v['previewRewards'])
    def get_block(self, height):
        return NS(number=100 if height=='latest' else height,timestamp=time.time()-self.head_age,
                  hash=b'bad' if self.reorg or self.reorg_after else BLOCK)
    def get_code(self, target, block_identifier=None):
        assert block_identifier==98
        return self.runtime
    def get_balance(self, target, block_identifier=None):
        assert block_identifier==98
        if target.lower()==OWNER and self.balance_reorg:self.reorg_after=True
        return self.pool_balance if target.lower()==POOL else self.balance if target.lower()==VAULT else 10000
    def contract(self, target, abi):
        if target.lower()==POOL:return NS(functions=Functions(self.p))
        if target.lower() in (FACTORY,LEGACY):
            mapped=self.mapped if target.lower()==FACTORY else self.legacy
            return NS(functions=Functions(dict(vaultOf=mapped,predict=mapped,settlementSigner=SIGNER,liquidationTreasury=SIGNER,rewardManager=SIGNER,rewardPool=POOL)))
        assert target.lower()==VAULT
        return NS(functions=Functions(self.v))
    def get_transaction_receipt(self, tx):
        if self.receipt is None:raise TransactionNotFound("Not mined")
        return self.receipt
    def get_transaction(self, tx):return self.sent
    def confirm(self, status=1):
        self.receipt=NS(transactionHash=bytes.fromhex(TX[2:]),blockNumber=98,blockHash=BLOCK,status=status,
                        logs=[dict(address=VAULT,topics=[Web3.keccak(text='RewardClaimed(address,uint256)'),bytes.fromhex('00'*12+OWNER[2:])],data=(20).to_bytes(32,'big'))] if status else [])
        if status:self.receipt.logs.append(dict(address=POOL,topics=[Web3.keccak(text='PoolRewardPaid(address,address,uint256)'),bytes.fromhex('00'*12+VAULT[2:]),bytes.fromhex('00'*12+OWNER[2:])],data=(20).to_bytes(32,'big')))
        self.sent=dict(hash=bytes.fromhex(TX[2:]),blockHash=BLOCK,to=VAULT,input=b'\x12\x34',value=0,**{'from':OWNER})

@pytest.fixture
def chain(tmp_path, monkeypatch):
    folder=tmp_path/'config';folder.mkdir()
    (folder/'dogeos-deployment.json').write_text(json.dumps(dict(contracts={'VaultFactory':dict(address=LEGACY,runtimeKeccak256=CODEHASH),'DogeCollateralVault':dict(runtimeKeccak256=CODEHASH)})))
    monkeypatch.setattr(native_vault,'ROOT',tmp_path)
    c=DogeOS(Config(network_enabled=True,reward_factory=FACTORY,reward_factory_codehash=CODEHASH,reward_vault_codehash=CODEHASH,reward_pool_codehash=CODEHASH))
    c.w3=NS(eth=Eth())
    return c

def test_native_rewards_are_read_only_confirmed_backed_and_separate(chain):
    s=chain.state(OWNER)
    assert s['block']==98 and s['confirmations']==3 and s['source']=='chain'
    assert s['vault']['withdrawable']=='0'
    assert s['vault']['rewards']['claimableWei']=='20'
    assert s['vault']['rewards']['reserveWei']=='100'
    assert s['vault']['stopBudgetMicro']=='500000'
    assert s['transaction'] is None
    assert Config().native_actions_enabled is False and not Config().reward_factory

def test_independent_reads_are_bounded_parallel_and_fail_closed(chain):
    import threading
    active=peak=0
    lock=threading.Lock()
    def value():
        nonlocal active,peak
        with lock: active+=1;peak=max(peak,active)
        time.sleep(.01)
        with lock: active-=1
        return 500
    contract=NS(functions=Functions({str(i):value for i in range(20)}))
    assert native_vault.read_values(contract,tuple(str(i) for i in range(20)),98)=={str(i):500 for i in range(20)}
    assert 2<=peak<=8
    def failed(): raise ValueError('RPC failure')
    chain.w3.eth.p['rewardReserve']=failed
    with pytest.raises(ValueError,match='RPC failure'):chain.state(OWNER)

@pytest.mark.parametrize('mutation', ['disabled','chain','stale','future','hash','owner','manager','domain','unbacked','overclaim','reorg'])
def test_rejects_unverifiable_or_unbacked_state(chain,mutation):
    e=chain.w3.eth
    if mutation=='disabled':chain.config.network_enabled=False
    if mutation=='chain':e.chain_id=1
    if mutation=='stale':e.head_age=91
    if mutation=='future':e.head_age=-1
    if mutation=='hash':e.runtime=b'changed'
    if mutation=='owner':e.v['owner']=SIGNER
    if mutation=='manager':e.v['rewardManager']=OWNER
    if mutation=='domain':e.v['expectedChainId']=1
    if mutation=='unbacked':e.balance=999
    if mutation=='overclaim':e.v['previewRewards']=(101,0)
    if mutation=='reorg':e.balance_reorg=True
    with pytest.raises(ValueError):chain.state(OWNER)

def test_legacy_owner_preserved_new_owner_uses_candidate_duplicate_fails(chain):
    e=chain.w3.eth;chain.config.factory=LEGACY;e.mapped=ZERO;e.legacy=VAULT
    assert chain.state(OWNER)['vault']['kind']=='legacy'
    assert chain.state(OWNER)['vault']['rewards'] is None
    e.mapped=VAULT
    with pytest.raises(ValueError,match='migration'):chain.state(OWNER)
    e.mapped=e.legacy=ZERO
    s=chain.state(OWNER)
    assert s['vault'] is None and s['factory']==FACTORY

@pytest.mark.parametrize('status',[0,1])
def test_canonical_claim_receipt_and_exact_paid_event(chain,status):
    e=chain.w3.eth
    assert chain.state(OWNER,TX)['transaction']['status']=='PENDING'
    e.confirm(status)
    t=chain.state(OWNER,TX)['transaction']
    assert t['status']==('CONFIRMED' if status else 'REVERTED')
    assert t['rewardPaidWei']==('20' if status else '0') and t['data']=='0x1234'
    if status:assert chain.read(OWNER,TX)[1]['hash']==TX
    else:
        with pytest.raises(ValueError,match='successful'):chain.read(OWNER,TX)
    e.receipt.blockNumber=99
    assert chain.state(OWNER,TX)['transaction']['status']=='PENDING'

@pytest.mark.parametrize('mutation',['sender','target','hash','block','event_owner','event_data'])
def test_claim_receipt_identity_and_event_validation(chain,mutation):
    e=chain.w3.eth;e.confirm()
    if mutation=='sender':e.sent['from']=SIGNER
    if mutation=='target':e.sent['to']=SIGNER
    if mutation=='hash':e.sent['hash']=b'wrong'
    if mutation=='block':e.sent['blockHash']=b'wrong'
    if mutation=='event_owner':e.receipt.logs[0]['topics'][1]=bytes(32)
    if mutation=='event_data':e.receipt.logs[0]['data']=b'\x01'
    with pytest.raises(ValueError):chain.state(OWNER,TX)

@pytest.mark.parametrize('mutation',['pool_backing','pool_domain','pool_factory','pool_manager','pool_liability','pool_available','pool_principal','pool_owner','missing_pool_hash','pool_payout'])
def test_shared_pool_fails_closed_on_unbacked_or_mismatched_connections(chain,mutation):
    e=chain.w3.eth
    if mutation=='pool_backing':e.pool_balance=99
    if mutation=='pool_domain':e.p['expectedChainId']=1
    if mutation=='pool_factory':e.p['factory']=OWNER
    if mutation=='pool_manager':e.p['rewardManager']=OWNER
    if mutation=='pool_liability':e.p['previewIndex']=(0,101*DENOMINATOR)
    if mutation=='pool_available':e.p['availableReserve']=81
    if mutation=='pool_principal':e.p['accounts']=lambda _:(OWNER,999,0,0,0)
    if mutation=='pool_owner':e.p['accounts']=lambda _:(SIGNER,1000,0,0,0)
    if mutation=='missing_pool_hash':chain.config.reward_pool_codehash=''
    if mutation=='pool_payout':e.confirm();e.receipt.logs.pop()
    with pytest.raises(ValueError):chain.state(OWNER,TX if mutation=='pool_payout' else None)


def test_project_pool_funding_is_observable_before_owner_creates_a_vault(chain):
    e=chain.w3.eth;e.mapped=ZERO;e.confirm();e.sent['to']=POOL;e.receipt.logs=[]
    s=chain.state(OWNER,TX)
    assert s['vault'] is None and s['rewardPool']['address']==POOL
    assert s['rewardPool']['reserveWei']=='100' and s['rewardPool']['reservedWei']=='20'
    assert s['transaction']['status']=='CONFIRMED' and s['transaction']['rewardPaidWei']=='0'


@pytest.fixture
def migrating(chain):
    e=chain.w3.eth;chain.config.factory=LEGACY
    old='0x'+'77'*20;e.legacy=old
    previous=dict(e.v,collateral=0,withdrawable=0,loanState=3,agent=ZERO,permissionRevision=2,
                  limits=(0,0,0,0,0,0,False))
    original=e.contract
    def contract(target,abi):
        if target.lower()==old:return NS(functions=Functions(previous))
        return original(target,abi)
    e.contract=contract
    intent=dict(version=1,owner=OWNER,chainId=CHAIN_ID,sourceVault=old,targetFactory=FACTORY,
                targetVault=VAULT,sourceNonce=1,sourceRevision=2)
    return chain,previous,intent


def test_dual_registry_selects_only_reviewed_destination_and_keeps_retired_proof(migrating):
    chain,old,intent=migrating
    s=chain.state(OWNER,migration=intent)
    assert s['factory']==FACTORY and s['vault']['address']==VAULT
    assert s['vault']['migratedFrom']['address']==intent['sourceVault']
    assert s['vault']['migratedFrom']['blockHash']==s['blockHash']
    assert s['vault']['rewards']['claimableWei']=='20'


@pytest.mark.parametrize('problem',['owner','chain','target','factory','source','revision','nonce','balance','loan','code','reorg'])
def test_migration_cannot_hide_legacy_funds_or_mismatched_state(migrating,problem):
    chain,old,intent=migrating
    if problem=='owner':intent['owner']=SIGNER
    if problem=='chain':intent['chainId']=1
    if problem=='target':intent['targetVault']=SIGNER
    if problem=='factory':intent['targetFactory']=LEGACY
    if problem=='source':intent['sourceVault']=VAULT
    if problem=='revision':old['permissionRevision']+=1
    if problem=='nonce':old['loanNonce']+=1
    if problem=='balance':old['collateral']=1
    if problem=='loan':old['loanState']=2
    if problem=='code':chain.w3.eth.runtime=b'wrong'
    if problem=='reorg':chain.w3.eth.balance_reorg=True
    with pytest.raises(ValueError):chain.state(OWNER,migration=intent)
