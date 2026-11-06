"""Verify owner-signed testnet deployment/funding; optionally activate public config.

No private keys, wallet prompts, broadcasts, service restarts or ledger migration.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from web3 import Web3
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from autodefi.doge_lending import configuration, DogeLendingReader

def verify(w3, deployment_hash, funding_hash, inputs):
    if w3.eth.chain_id!=6281971:raise ValueError('Wrong testnet')
    head=w3.eth.get_block('latest')
    if not 0<=time.time()-head.timestamp<=90:raise ValueError('Stale testnet head')
    def receipt(h):
        r=w3.eth.get_transaction_receipt(h);t=w3.eth.get_transaction(h)
        if r.status!=1 or head.number-r.blockNumber<2 or w3.eth.get_block(r.blockNumber).hash!=r.blockHash:
            raise ValueError('Canonical successful transaction with three confirmations required')
        return r,t
    r,t=receipt(deployment_hash)
    if t['from'].lower()!=inputs['owner'].lower() or t['to'] is not None or t['value']!=0 or t['input'].to_0x_hex().lower()!=inputs['data'].lower():
        raise ValueError('Deployment does not match the reviewed owner and bytecode')
    target=r.contractAddress
    if Web3.keccak(w3.eth.get_code(target,block_identifier=head.number-2)).to_0x_hex()!=inputs['runtimeCodeHash']:
        raise ValueError('Unexpected deployed runtime')
    funding,ft=receipt(funding_hash)
    if ft['from'].lower()!=inputs['owner'].lower() or (ft['to'] or '').lower()!=target.lower() or ft['value']<=0 or ft['input'].to_0x_hex()!=Web3.keccak(text='fund()')[:4].to_0x_hex():
        raise ValueError('Funding does not match the owner and lending reserve')
    d=dict(schemaVersion=1,chainId=6281971,enabled=True,market=target,runtimeCodeHash=inputs['runtimeCodeHash'],manager=inputs['manager'],deploymentBlock=r.blockNumber,confirmations=3,
        conversion=dict(enabled=False,reason='USDC conversion and real trading settlement are not configured.'))
    snapshot=DogeLendingReader(w3,d).snapshot(inputs['owner'])
    if int(snapshot['liquidityWei'])<=0:raise ValueError('Funded lending liquidity required')
    # The verified account read can span several RPC calls. Recheck both
    # activation receipts immediately before handing the config to the writer.
    for confirmed in (r,funding):
        if w3.eth.get_block(confirmed.blockNumber).hash!=confirmed.blockHash:
            raise ValueError('Activation receipt reorganized during verification')
    return d,dict(deploymentHash=deployment_hash,fundingHash=funding_hash,market=target,
        deploymentBlock=r.blockNumber,fundingBlock=funding.blockNumber,fundingWei=str(ft['value']),liquidityWei=snapshot['liquidityWei'],runtimeVerified=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--deployment-tx',required=True);p.add_argument('--funding-tx',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--report',required=True);args=p.parse_args()
    inputs=json.loads((ROOT/'site/vela-dashboard/src/owner/doge-lending-deployment.json').read_text())
    w3=Web3(Web3.HTTPProvider('https://rpc.testnet.dogeos.com/',request_kwargs={'timeout':15}))
    d,evidence=verify(w3,args.deployment_tx,args.funding_tx,inputs)
    target=ROOT/'config/doge-lending.json';old=configuration(target)
    if old['enabled'] and old['market'].lower()!=d['market'].lower():raise ValueError('Existing active market must not be replaced')
    if args.apply:
        raw=json.dumps(d,indent=2)+'\n';fd,temp=tempfile.mkstemp(prefix='.doge-lending-',dir=target.parent)
        try:
            with os.fdopen(fd,'w') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.chmod(temp,0o644);os.replace(temp,target)
        finally:Path(temp).unlink(missing_ok=True)
    result=dict(**evidence,applied=args.apply,broadcast=False,backendRestarted=False,legacyLedgerChanged=False,usdcConversionEnabled=False,physicalWalletFlowVerified=False,liveProtectionVerified=False)
    Path(args.report).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
