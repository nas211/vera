"""Independent public RPC reads only; no signer, credential or chain mutation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from decimal import Decimal
import argparse
import json
from pathlib import Path
import httpx
from eth_utils import keccak
from eth_abi import decode
try:
    from .dogeos_receipt_fees import receipt_fees
except ImportError:
    from dogeos_receipt_fees import receipt_fees

def public_state(state):
    # Wei values exceed JavaScript's exact integer range. Keep decimal strings
    # throughout evidence export, as for individual receipt fees.
    money={'collateral','reserve','claimed','poolPrincipal','poolBalanceWei','amount','total','remaining'}
    return {key:str(value) if key in money else value for key,value in state.items()}

OWNER='0xf8b7120f466f39063398fdc62f5f8cbce37ad1db'
VAULT='0x2937bfdaea76fc29529d7792ac674b6eef55eca5'
FACTORY='0x7304ebde8d313c95c3a89caa20e7915545a0665e'
POOL='0x79a20c6c39525a6253c8aa5d04ee5682ecb2b0ae'
EXPECTED=[('createVault','0x5b2750a6cd5db170cbb456cb2dab13edfaae3df2682b326524513d2c6da8b603'),('deposit','0x1fad3b1797bc08c7aa6339501f320180e61d7cff91906201d72fd9d8d501202c'),('claimRewards','0x737df894fca8138f8fcbd57ca9473775092053caa2c3d310fdbd7b46a6b6d53d'),('withdraw','0x764907c41d388795073b92b89645997925a123a4b0ad1de19f968daa6722a886')]
ALLOWED={'eth_chainId','eth_blockNumber','eth_getTransactionReceipt','eth_getTransactionByHash','eth_getBlockByNumber','eth_getBalance','eth_getCode','eth_call','eth_getTransactionCount'}
def rpc(method,params):
    assert method in ALLOWED
    r=httpx.post('https://rpc.testnet.dogeos.com/',json=dict(jsonrpc='2.0',id=1,method=method,params=params),timeout=15)
    r.raise_for_status();data=r.json()
    if 'error' in data:raise RuntimeError(data['error'])
    return data['result']
def hx(data):return bytes.fromhex(data.removeprefix('0x'))
def topic(sig):return '0x'+keccak(text=sig).hex()
def events(receipt,address,sig,indexed,plain):
    result=[]
    for log in receipt['logs']:
        if log['address'].lower()!=address or not log['topics'] or log['topics'][0].lower()!=topic(sig):continue
        values={name:decode([kind],hx(value))[0] for (name,kind),value in zip(indexed,log['topics'][1:],strict=True)}
        values.update(zip([n for n,k in plain],decode([k for n,k in plain],hx(log['data'])),strict=True))
        result.append(values)
    return result
def verify(item):
    method,tx=item
    r=rpc('eth_getTransactionReceipt',[tx]);t=rpc('eth_getTransactionByHash',[tx])
    assert r and t and int(r['status'],16)==1
    block=rpc('eth_getBlockByNumber',[r['blockNumber'],False])
    assert r['blockHash']==t['blockHash']==block['hash']
    assert t['hash']==r['transactionHash']==tx
    assert int(t['chainId'],16)==6281971 and t['from'].lower()==OWNER
    assert t['to'].lower()==(FACTORY if method=='createVault' else VAULT)
    signature=method+('(uint256)' if method=='withdraw' else '()')
    assert t['input'][:10].lower()=='0x'+keccak(text=signature)[:4].hex()
    assert int(t['value'],16)==(10*10**18 if method=='deposit' else 0)
    proof={}
    if method=='createVault':
        logs=events(r,FACTORY,'VaultCreated(address,address)',[('owner','address'),('vault','address')],[])
        assert logs==[dict(owner=OWNER,vault=VAULT)];proof['VaultCreated']=logs
    elif method in ('deposit','withdraw'):
        name='CollateralDeposited' if method=='deposit' else 'CollateralWithdrawn'
        last='total' if method=='deposit' else 'remaining'
        logs=events(r,VAULT,name+'(address,uint256,uint256)',[('owner','address')],[('amount','uint256'),(last,'uint256')])
        assert logs==[dict(owner=OWNER,amount=10*10**18,**{last:10*10**18 if method=='deposit' else 0})]
        if method=='withdraw':assert decode(['uint256'],hx(t['input'][10:]))==(10*10**18,)
        proof[name]=logs
    else:
        claimed=events(r,VAULT,'RewardClaimed(address,uint256)',[('owner','address')],[('amount','uint256')])
        paid=events(r,POOL,'PoolRewardPaid(address,address,uint256)',[('vault','address'),('owner','address')],[('amount','uint256')])
        assert len(claimed)==len(paid)==1
        assert claimed[0]['owner']==paid[0]['owner']==OWNER and paid[0]['vault']==VAULT
        assert claimed[0]['amount']==paid[0]['amount']==3757610350076
        proof=dict(rewardPaidWei=str(paid[0]['amount']),RewardClaimed=claimed,PoolRewardPaid=paid)
    proof={key:[public_state(event) for event in value] if isinstance(value,list) else value
           for key,value in proof.items()}
    return dict(action=method,tx=tx,status='CONFIRMED',block=int(r['blockNumber'],16),blockHash=r['blockHash'],nonce=int(t['nonce'],16),**receipt_fees(r),**proof)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert not args.output.exists(), 'Choose a new evidence file; existing evidence is preserved'
    assert int(rpc('eth_chainId',[]),16)==6281971
    head=rpc('eth_blockNumber',[])
    with ThreadPoolExecutor(max_workers=4) as ex:receipts=list(ex.map(verify,EXPECTED))
    assert [r['nonce'] for r in receipts]==list(range(109,113))
    assert all(r['block']<=int(head,16) for r in receipts)
    before=int(rpc('eth_getBalance',[OWNER,hex(receipts[0]['block']-1)]),16)
    after=int(rpc('eth_getBalance',[OWNER,hex(receipts[-1]['block'])]),16)
    paid=int(receipts[2]['rewardPaidWei']);gas=sum(int(r['totalFeeWei']) for r in receipts)
    assert after-before==paid-gas
    public=httpx.get('https://defivela.xyz/api/config',timeout=15).json()['nativeRewardDeployment']
    expected_hashes={FACTORY:public['factoryCodeHash'],VAULT:public['vaultCodeHash'],POOL:public['poolCodeHash']}
    def codecheck(item):
        address,expected=item;actual='0x'+keccak(hx(rpc('eth_getCode',[address,head]))).hex();assert actual==expected
        return dict(address=address,codeHash=actual,verified=True)
    with ThreadPoolExecutor(max_workers=3) as ex:codes=list(ex.map(codecheck,expected_hashes.items()))
    def view(address,signature,types):return decode(types,hx(rpc('eth_call',[dict(to=address,data='0x'+keccak(text=signature)[:4].hex()),head])))
    queries=[('vaultOwner',VAULT,'owner()',['address']),('collateral',VAULT,'collateral()',['uint256']),('loanState',VAULT,'loanState()',['uint8']),('trading',VAULT,'trading()',['bool']),('pool',VAULT,'rewardPool()',['address']),('aprBps',POOL,'aprBps()',['uint256']),('reserve',POOL,'rewardReserve()',['uint256']),('claimed',POOL,'totalRewardsClaimed()',['uint256']),('poolPrincipal',POOL,'totalPrincipal()',['uint256'])]
    with ThreadPoolExecutor(max_workers=5) as ex:state=dict(ex.map(lambda q:(q[0],view(q[1],q[2],q[3])[0]),queries))
    assert state['vaultOwner']==OWNER and state['collateral']==0 and state['loanState']==0 and state['trading'] is False
    assert state['pool']==POOL and state['aprBps']==500 and state['claimed']==paid and state['reserve']>0
    state['poolBalanceWei']=int(rpc('eth_getBalance',[POOL,head]),16)
    assert state['poolBalanceWei']>=state['reserve'], 'Pool reserve exceeds native balance'
    report=dict(status='PASS',observedAt=datetime.now(timezone.utc).isoformat(),chainId=6281971,head=int(head,16),receipts=receipts,codeChecks=codes,currentState=public_state(state),rewardPaidDoge=str(Decimal(paid)/10**18),walletBalanceReconciled=True,totalTransactionFeesWei=str(gas),transactionsSent=False,ledgerModified=False,credentialsRead=False,scope='Independent canonical RPC receipt, event, code and state checks for the completed reward-vault cycle; not a contract audit')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:output.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__ == '__main__':
    main()
