"""Read-only DogeOS deployment quotation; no wallet keys or transaction sends."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import urllib.request

import rlp
from eth_abi import encode
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_contracts import prepare

RPC = 'https://rpc.testnet.dogeos.com/'
ALLOWED = {'eth_chainId', 'eth_getBalance', 'eth_getTransactionCount', 'eth_gasPrice',
           'eth_blockNumber', 'eth_estimateGas', 'eth_call', 'eth_getCode'}


def rpc(method, params):
    if method not in ALLOWED:
        raise ValueError('Read-only RPC method required')
    request = urllib.request.Request(RPC, data=json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params)).encode(),
                                     headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read())
    if 'error' in result:
        raise RuntimeError('RPC quotation failed: '+str(result['error'].get('message','unknown')))
    return result['result']


def quote():
    roles = json.loads((ROOT/'config/dogeos-deployment.json').read_text())['roles']
    owner, attestor = roles['deployer'], roles['settlementAttestor']
    if int(rpc('eth_chainId', []), 16) != 6281971:
        raise RuntimeError('Wrong testnet')
    nonce = int(rpc('eth_getTransactionCount', [owner, 'pending']), 16)
    prepared = prepare(deployer=owner, nonce=nonce, attestor=attestor, treasury=roles['liquidationTreasury'],
                       rewarded=True, reward_manager=owner, apr_bps=500)
    tx = {k:prepared['unsignedTransaction'][k] for k in ('from','data','value')}
    factory = prepared['contracts']['RewardedVaultFactory']['predictedAddress']
    if rpc('eth_getCode', [factory,'latest']) != '0x':
        raise RuntimeError('Predicted address already has code; reconcile before deployment')
    estimated = int(rpc('eth_estimateGas', [tx]), 16)
    gas = (estimated*125+99)//100
    price = int(rpc('eth_gasPrice', []), 16)*2
    if gas <= 0 or price <= 0:
        raise RuntimeError('Invalid fee quote')
    unsigned = rlp.encode([nonce, price, gas, b'', 0, bytes.fromhex(tx['data'][2:]), 6281971, 0, 0])
    data = '0x'+(keccak(text='getL1Fee(bytes)')[:4]+encode(['bytes'], [unsigned])).hex()
    data_fee = int(rpc('eth_call', [{'to':'0x5300000000000000000000000000000000000002','data':data},'latest']),16)
    balance = int(rpc('eth_getBalance', [owner, 'pending']), 16)
    reserve = gas*price+data_fee*2
    if int(rpc('eth_getTransactionCount', [owner, 'pending']),16) != nonce:
        raise RuntimeError('Nonce changed while quoting')
    prepared['unsignedTransaction'].update(nonce=hex(nonce), chainId=hex(6281971), gas=hex(gas), gasPrice=hex(price))
    prepared.update(approvalRequired=False, walletSignatureRequired=True, broadcast=False,
                    observedAtUtc=datetime.now(timezone.utc).isoformat(),
                    block=int(rpc('eth_blockNumber',[]),16),
                    feeQuote=dict(estimatedExecutionGas=estimated, bufferedGas=gas, bufferedGasPriceWei=str(price),
                        dataFinalityEstimateWei=str(data_fee), feeReserveWei=str(reserve),
                        feeReserveTestDoge=str(Decimal(reserve)/10**18), walletBalanceWei=str(balance),
                        walletBalanceTestDoge=str(Decimal(balance)/10**18), balanceSufficient=balance>=reserve,
                        reserveFundingIncluded=False, mustRefreshBeforeWalletConfirmation=True))
    return prepared


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = quote()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output), factoryPredicted=result['contracts']['RewardedVaultFactory']['predictedAddress'],
                          poolPredicted=result['contracts']['NativeRewardPool']['predictedAddress'],
                          **result['feeQuote'], broadcast=False)))
