"""Verify the owner-signed reward deployment from canonical public chain data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

from web3 import Web3
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_contracts import artifact, prepare, RPC, CHAIN


def runtime(name, values):
    data, _, _ = artifact(name)
    names = {}
    def walk(value):
        if isinstance(value,dict):
            if value.get('nodeType')=='VariableDeclaration' and value.get('mutability')=='immutable':
                names[str(value['id'])]=value['name']
            for child in value.values(): walk(child)
        elif isinstance(value,list):
            for child in value: walk(child)
    walk(data.get('ast'))
    result=bytearray.fromhex(data['deployedBytecode']['object'].removeprefix('0x'))
    for node, locations in data['deployedBytecode']['immutableReferences'].items():
        if node not in names or names[node] not in values:
            raise ValueError('Missing reviewed immutable: '+name+':'+node+'; compile with --ast')
        value=values[names[node]]
        encoded=(int(value,16) if isinstance(value,str) else value).to_bytes(32,'big')
        for loc in locations:
            start,length=loc['start'],loc['length']
            if length!=32 or start<0 or start+length>len(result) or any(result[start:start+length]):
                raise ValueError('Unexpected immutable patch location')
            result[start:start+length]=encoded
    return bytes(result)


def verify():
    inputs=json.loads((ROOT/'site/vela-dashboard/src/owner/deployment.json').read_text())
    owner,signer,manager,treasury=(inputs[k] for k in ('owner','attestor','manager','treasury'))
    prepared=prepare(deployer=owner,nonce=int(inputs['expectedNonce'],16),attestor=signer,treasury=treasury,
                     rewarded=True,reward_manager=manager,apr_bps=500)
    if prepared['unsignedTransaction']['data']!=inputs['data']:
        raise ValueError('Current compiled constructor differs from the owner review')
    factory=prepared['contracts']['RewardedVaultFactory']['predictedAddress']
    pool=prepared['contracts']['NativeRewardPool']['predictedAddress']
    w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={'timeout':15}))
    if w3.eth.chain_id!=CHAIN: raise ValueError('Wrong chain')
    head=w3.eth.get_block('latest')
    if not 0<=time.time()-head.timestamp<=90: raise ValueError('Stale chain')
    height=head.number-2; observed=w3.eth.get_block(height)
    if not w3.eth.get_code(factory,height): raise ValueError('Deployment not confirmed yet')
    lower=json.loads((ROOT/'.runtime/releases/noah-factory-live-review/latest-quote.json').read_text())['block']
    if lower>=height or w3.eth.get_code(factory,lower): lower=0
    upper=height
    while upper-lower>1:
        middle=(lower+upper)//2
        if w3.eth.get_code(factory,middle): upper=middle
        else: lower=middle
    creation=w3.eth.get_block(upper,full_transactions=True)
    matches=[t for t in creation.transactions if t['from'].lower()==owner.lower() and t.nonce==int(inputs['expectedNonce'],16)]
    if len(matches)!=1: raise ValueError('Creation transaction not uniquely identified')
    tx=matches[0]; receipt=w3.eth.get_transaction_receipt(tx.hash)
    if (tx.to is not None or tx.value!=0 or bytes(tx.input)!=bytes.fromhex(inputs['data'][2:])
            or receipt.status!=1 or receipt.contractAddress.lower()!=factory.lower()
            or receipt.blockHash!=creation.hash or receipt.blockNumber!=upper):
        raise ValueError('Confirmed creation receipt differs from the review')
    values=dict(settlementSigner=signer,liquidationTreasury=treasury,rewardManager=manager,
                initialAprBps=500,rewardPool=pool,factory=factory,expectedChainId=CHAIN)
    codes={name:runtime(name,values) for name in ('RewardedVaultFactory','NativeRewardPool','RewardedCollateralVault')}
    for name,address in (('RewardedVaultFactory',factory),('NativeRewardPool',pool)):
        if bytes(w3.eth.get_code(address,height))!=codes[name]:
            raise ValueError('Compiled runtime/immutable mismatch: '+name)
    def contract(name,address):
        return w3.eth.contract(address=Web3.to_checksum_address(address),abi=artifact(name)[0]['abi'])
    f,p=contract('RewardedVaultFactory',factory),contract('NativeRewardPool',pool)
    specs=[(f,'settlementSigner',signer),(f,'liquidationTreasury',treasury),(f,'rewardManager',manager),
           (f,'rewardPool',pool),(f,'initialAprBps',500),(p,'factory',factory),(p,'rewardManager',manager),
           (p,'expectedChainId',CHAIN),(p,'aprBps',500)]
    def check(spec):
        c,name,expected=spec; actual=getattr(c.functions,name)().call(block_identifier=height)
        if (actual.lower()!=expected.lower()) if isinstance(expected,str) else (actual!=expected):
            raise ValueError('Role/term mismatch: '+name)
    with ThreadPoolExecutor(max_workers=8) as executor: list(executor.map(check,specs))
    reserve=p.functions.rewardReserve().call(block_identifier=height)
    if w3.eth.get_balance(pool,height)<reserve: raise ValueError('Pool backing mismatch')
    legacy=json.loads((ROOT/'config/dogeos-deployment.json').read_text())
    if legacy['roles']['settlementAttestor'].lower()!=signer.lower():
        raise ValueError('Existing owner attestor differs; preserve legacy workflow')
    if w3.eth.get_block(height).hash!=observed.hash or w3.eth.get_block(upper).hash!=creation.hash:
        raise ValueError('Chain reorganized during verification')
    hashes={name:'0x'+Web3.keccak(code).hex().removeprefix('0x') for name,code in codes.items()}
    return dict(verifiedAtUtc=datetime.now(timezone.utc).isoformat(),chainId=CHAIN,
        transactionHash='0x'+bytes(tx.hash).hex(),blockNumber=upper,blockHash='0x'+bytes(creation.hash).hex(),
        confirmedAtBlock=height,confirmations=head.number-upper+1,owner=owner,attestor=signer,manager=manager,
        factory=factory,pool=pool,initialAprBps=500,reserveWei=str(reserve),
        creationCalldataMatched=True,deployedFactoryAndPoolBytecodeMatched=True,
        vaultRuntime='Compiled runtime with reviewed shared immutables; no customer vault created',runtimeHashes=hashes,
        backendActivationChecked=False,rewardPayoutVerified=False,
        bindings=dict(VERA_PROTOCOL_NATIVE_ACTIONS_ENABLED='1',VERA_PROTOCOL_REWARD_FACTORY=factory,
            VERA_PROTOCOL_REWARD_FACTORY_CODEHASH=hashes['RewardedVaultFactory'],
            VERA_PROTOCOL_REWARD_VAULT_CODEHASH=hashes['RewardedCollateralVault'],
            VERA_PROTOCOL_REWARD_POOL_CODEHASH=hashes['NativeRewardPool'],VERA_PROTOCOL_DEMO_ATTESTOR_ADDRESS=signer))


def observe_pool(funding_tx=None):
    if funding_tx is not None and not re.fullmatch(r'0x[0-9a-fA-F]{64}', funding_tx):
        raise ValueError('Valid funding transaction hash required')
    verified=json.loads((ROOT/'verification/noah/reward-deployment-verified.json').read_text())
    w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={'timeout':15}))
    if w3.eth.chain_id!=CHAIN: raise ValueError('Wrong chain')
    head=w3.eth.get_block('latest'); height=head.number-2
    if not 0<=time.time()-head.timestamp<=90: raise ValueError('Stale chain')
    block=w3.eth.get_block(height);address=verified['pool']
    code=w3.eth.get_code(address,height)
    if '0x'+bytes(Web3.keccak(code)).hex()!=verified['runtimeHashes']['NativeRewardPool']:
        raise ValueError('Pool bytecode changed')
    pool=w3.eth.contract(address=address,abi=artifact('NativeRewardPool')[0]['abi'])
    names=('rewardReserve','totalRewardsFunded','totalRewardsClaimed','totalPrincipal','aprBps')
    with ThreadPoolExecutor(max_workers=5) as executor:
        values=list(executor.map(lambda n:getattr(pool.functions,n)().call(block_identifier=height),names))
    logs=pool.events.PoolFunded().get_logs(from_block=verified['blockNumber'],to_block=height)
    balance=w3.eth.get_balance(address,height)
    if balance<values[0]: raise ValueError('Pool backing mismatch')
    receipt_check=None
    if funding_tx:
        tx=w3.eth.get_transaction(funding_tx); receipt=w3.eth.get_transaction_receipt(funding_tx)
        matches=[e for e in logs if bytes(e.transactionHash)==bytes(receipt.transactionHash)]
        if (bytes(tx.hash)!=bytes.fromhex(funding_tx[2:]) or tx.hash!=receipt.transactionHash
                or receipt.status!=1 or receipt.blockNumber>height
                or tx.blockHash!=receipt.blockHash or tx.blockNumber!=receipt.blockNumber
                or w3.eth.get_block(receipt.blockNumber).hash!=receipt.blockHash
                or tx['from'].lower()!=verified['owner'].lower() or (tx.to or '').lower()!=address.lower()
                or tx.value!=10**18 or bytes(tx.input)!=bytes(Web3.keccak(text='topUpReserve()')[:4])
                or len(matches)!=1 or matches[0].args.funder.lower()!=verified['owner'].lower()
                or matches[0].args.amount!=10**18 or matches[0].blockHash!=receipt.blockHash):
            raise ValueError('Funding receipt differs from the authorized one-DOGE donation')
        receipt_check=dict(transactionHash=funding_tx,successful=True,owner=tx['from'],pool=tx.to,
                           valueWei=str(tx.value),method='topUpReserve()',block=receipt.blockNumber,
                           blockHash='0x'+bytes(receipt.blockHash).hex(),confirmations=head.number-receipt.blockNumber+1,
                           canonicalReceiptMatched=True,fundingEventMatched=True)
    if w3.eth.get_block(height).hash!=block.hash: raise ValueError('Observation reorganized')
    return dict(observedAtUtc=datetime.now(timezone.utc).isoformat(),pool=address,block=height,
                chainId=CHAIN,poolBalanceWei=str(balance),fundingReceipt=receipt_check,
                **dict(zip(names,map(str,values))),
                funding=[dict(funder=e.args.funder,amountWei=str(e.args.amount),transactionHash='0x'+bytes(e.transactionHash).hex(),block=e.blockNumber) for e in logs])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'verification/noah/reward-deployment-verified.json')
    parser.add_argument('--pool-only',action='store_true')
    parser.add_argument('--funding-tx',help='Verify the authorized one-DOGE funding receipt (requires --pool-only)')
    args=parser.parse_args()
    if args.funding_tx and not args.pool_only: parser.error('--funding-tx requires --pool-only')
    result=observe_pool(args.funding_tx) if args.pool_only else verify()
    if args.pool_only and args.output==ROOT/'verification/noah/reward-deployment-verified.json':
        args.output=ROOT/'verification/noah/reward-funding-observation.json'
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('bindings','runtimeHashes')}))
