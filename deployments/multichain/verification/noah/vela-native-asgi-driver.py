"""Real ASGI/ledger integration with a deterministic native transport, no sockets.

Native transitions here are test doubles, not evidence of EVM execution. The
separate Foundry suite executes the candidate contract and its reward arithmetic.
Every database and key is disposable or a public test identity.
"""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'backend'),str(ROOT/'tests')]
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_abi import encode
from web3 import Web3
from autodefi.api import create_app
from autodefi.config import Config,CHAIN_ID
from autodefi.chain import abi
from conftest import ALICE,ATTESTOR,fixture_feed,fixture_vault
from test_api_ai import FixtureModel
from tools.dogeos_readonly import create_app as gateway_app, ORIGIN

OWNER=ALICE.address.lower();FACTORY='0x'+'22'*20;POOL='0x'+'66'*20
CODE='0x60006000';CODEHASH='0x'+Web3.keccak(hexstr=CODE).hex().removeprefix('0x')
BLOCK='0x'+'44'*32

class NativeTransport:
    def __init__(self):
        self.v=None;self.receipts={};self.pending={};self.height=98;self.counter=0
        self.pool=dict(source='chain',address=POOL,factory=FACTORY,manager=OWNER,codeHash=CODEHASH,bytecodeVerified=True,
                       aprBps='500',reserveWei='0',availableReserveWei='0',reservedWei='0',fundedWei='0',claimedWei='0',totalPrincipalWei='0')
    def sync_pool(self):
        p=self.pool;v=self.v;reserved=int(v['rewards']['claimableWei']) if v else 0
        p.update(availableReserveWei=str(int(p['reserveWei'])-reserved),reservedWei=str(reserved),totalPrincipalWei=v['collateral'] if v else '0')
        if v:v['rewards'].update(pool=POOL,aprBps=p['aprBps'],reserveWei=p['reserveWei'],availableReserveWei=p['availableReserveWei'],fundedWei=p['fundedWei'],principalWei=v['collateral'],loanLocked=v['loanState'] in (1,2,4))
    def state(self,owner,tx=None):
        assert owner==OWNER
        return copy.deepcopy(dict(source='chain',chainId=CHAIN_ID,owner=owner,factory=FACTORY,factoryKind='rewarded',bytecodeVerified=True,
            factoryCodeHash=CODEHASH,vaultCodeHash=CODEHASH,block=self.height,blockHash=BLOCK,observedAt=int(time.time()),confirmations=3,
            walletBalanceWei=str(100000*10**18),vault=self.v,rewardPool=self.pool,transaction=self.receipts.get(tx,dict(hash=tx,status='PENDING')) if tx else None))
    def read(self,owner,tx=None):
        s=self.state(owner,tx)
        if not s['vault']:raise ValueError('Create vault first')
        if tx and s['transaction']['status']!='CONFIRMED':raise ValueError('Confirmed transaction required')
        return s['vault'],s['transaction']
    def broadcast(self,tx):
        assert tx['from']==OWNER and int(tx['chainId'],16)==CHAIN_ID
        self.counter+=1;h='0x'+format(self.counter,'064x');self.pending[h]=tx
        return h
    def verify(self,action,a,b,c,args):
        v=self.v;deadline=args['deadline'];signature=args['signature']
        digest=Web3.keccak(encode(['bytes32','uint256','address','address','uint256','uint8','bytes32','uint256','uint256','uint256','uint64'],
            [Web3.keccak(text='ADP_DEMO_V1'),CHAIN_ID,v['address'],OWNER,v['loanNonce'],action,bytes.fromhex(v['loanId'][2:]),a,b,c,deadline]))
        assert time.time()<=deadline<=time.time()+3600
        assert Account.recover_message(encode_defunct(primitive=digest),signature=signature)==ATTESTOR.address
    def mine(self,h):
        tx=self.pending.pop(h);reward=0
        fn,args=Web3().eth.contract(abi=abi('RewardedVaultFactory' if tx['to']==FACTORY else 'NativeRewardPool' if tx['to']==POOL else 'RewardedCollateralVault')).decode_function_input(tx['data'])
        name=fn.fn_name;v=self.v;value=int(tx['value'],16)
        if name=='createVault':
            assert v is None
            v=fixture_vault(OWNER,nonce=0,amount=0)
            v.update(kind='rewarded',factory=FACTORY,collateral='0',withdrawable='0',loanState=0,bytecodeVerified=True,stopBudgetMicro='2000000')
            v['rewards']=dict(source='chain',aprBps='500',reserveWei='0',claimableWei='0',availableReserveWei='0',claimedWei='0',fundedWei='0',principalWei='0',loanLocked=False)
            self.v=v
        elif name=='deposit':v['collateral']=str(int(v['collateral'])+value)
        elif name=='topUpReserve':
            r=self.pool;r['reserveWei']=str(int(r['reserveWei'])+value);r['fundedWei']=str(int(r['fundedWei'])+value)
        elif name=='setRewardRate':self.pool['aprBps']=str(args['nextBps'])
        elif name=='requestDemoBorrow':
            assert v['loanState'] in (0,3) and not v['trading'] and int(v['collateral'])>0
            v.update(loanId='0x'+args['id'].hex(),principalMicro=str(args['amountMicro']),loanNonce=v['loanNonce']+1,loanState=1)
        elif name=='acknowledgeDemoLoan':
            assert v['loanState']==1;self.verify(1,int(v['principalMicro']),0,0,args);v['loanState']=2
        elif name=='cancelUnissuedLoan':
            assert v['loanState']==1;self.verify(3,0,0,0,args);v['loanState']=3
        elif name=='settleDemoLoan':
            assert v['loanState']==2
            a,b,c=args['repaidMicro'],args['seizeWei'],args['remainingBadDebtMicro'];self.verify(2,a,b,c,args)
            assert a+c>=int(v['principalMicro']) and b<=int(v['collateral'])
            v.update(loanState=4 if c else 3,trading=False,collateral=str(int(v['collateral'])-b),badDebtMicro=str(c))
        elif name=='withdraw':
            assert v['loanState'] in (0,3) and not v['trading'] and args['amount']<=int(v['withdrawable'])
            v['collateral']=str(int(v['collateral'])-args['amount'])
        elif name=='claimRewards':
            r=v['rewards'];reward=int(r['claimableWei']);assert 0<reward<=int(r['reserveWei'])
            r.update(claimableWei='0',claimedWei=str(int(r['claimedWei'])+reward))
            self.pool.update(reserveWei=str(int(self.pool['reserveWei'])-reward),claimedWei=str(int(self.pool['claimedWei'])+reward))
        else:raise AssertionError(name)
        self.height+=1
        if v:v.update(block=self.height,blockHash=BLOCK,withdrawable=v['collateral'] if v['loanState'] in (0,3) and not v['trading'] else '0')
        self.sync_pool()
        self.receipts[h]=dict(hash=h,status='CONFIRMED',sender=OWNER,to=tx['to'],data=tx['data'],valueWei=str(value),block=self.height,blockHash=BLOCK,rewardPaidWei=str(reward))

async def main():
    frozen=time.time();time.time=lambda:frozen
    async def heartbeat():
        while True:await asyncio.sleep(.01)
    pulse=asyncio.create_task(heartbeat())
    parent=ROOT/'.runtime/lighter-showcase';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vela-native-test-',dir=parent) as folder:
        config=Config(origin=ORIGIN,db_path=Path(folder)/'isolated.sqlite3',attestor_key=ATTESTOR.key.hex(),network_enabled=True,
                      native_actions_enabled=True,reward_factory=FACTORY,reward_factory_codehash=CODEHASH,
                      reward_vault_codehash=CODEHASH,reward_pool_codehash=CODEHASH)
        native=NativeTransport();app=create_app(config,feed=fixture_feed(),chain=native,model=FixtureModel())
        gateway=gateway_app(reader=SimpleNamespace(factory=FACTORY,reference='0x'+'ab'*20),
                            transport=httpx.ASGITransport(app=app),native_workflow=True)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url=config.origin,headers={'Origin':config.origin,'X-ADP-Client':'web'}) as client:
                assert (await client.get('/api/config')).json()['nativeWorkflowGatewayEnabled'] is True
                c=(await client.post('/api/auth/nonce',json=dict(address=OWNER,chainId=CHAIN_ID))).json()
                sig=ALICE.sign_message(encode_defunct(text=c['message'])).signature.hex()
                auth=await client.post('/api/auth/verify',json=dict(address=OWNER,nonce=c['nonce'],signature=sig));assert auth.status_code==200,auth.text
                print(json.dumps(dict(ready=True,address=OWNER,now=int(time.time()),code=CODE,blockHash=BLOCK)),flush=True)
                while True:
                    line=await asyncio.to_thread(sys.stdin.readline)
                    if not line:break
                    m=json.loads(line)
                    if m.get('control')=='broadcast':result=dict(status=200,body=native.broadcast(m['tx']))
                    elif m.get('control')=='mine':native.mine(m['hash']);result=dict(status=200,body=True)
                    elif m.get('control')=='rewards':
                        # Synthetic chain accrual for transport checks; never entered into USD ledger.
                        r=native.v['rewards'];r['claimableWei']=str(10**16);native.sync_pool()
                        result=dict(status=200,body=True)
                    elif m.get('control')=='tick':app.state.paper.settle_tick();result=dict(status=200,body=True)
                    else:
                        ctx=m['context'];h={'X-Wallet-Address':ctx['address'],'X-Vella-Network':ctx['network']}
                        if m.get('key'):h['Idempotency-Key']=m['key']
                        response=await client.request('POST' if 'body' in m else 'GET',m['path'],json=m.get('body'),headers=h)
                        result=dict(status=response.status_code,body=response.json())
                    print(json.dumps(result),flush=True)
        finally:
            app.state.db.close();await asyncio.get_running_loop().shutdown_default_executor();pulse.cancel()
asyncio.run(main())
