"""Public unsigned testnet deployment input. Never reads keys or broadcasts."""
import hashlib
import json
import sys
from pathlib import Path
from eth_abi import encode
from eth_utils import keccak
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_contracts import artifact
OWNER='0xF8b7120F466f39063398fdc62F5f8cBCe37Ad1dB'
CHAIN=6281971
def build():
    a,code,sources=artifact('TestDogeLending')
    refs=a['deployedBytecode']['immutableReferences']
    def nodes(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from nodes(v)
        elif isinstance(x,list):
            for v in x:yield from nodes(v)
    ids={str(n['id']) for n in nodes(a['ast']) if n.get('nodeType')=='VariableDeclaration' and n.get('name')=='deployedChainId' and n.get('mutability')=='immutable'}
    if set(refs)!=ids or len(ids)!=1:raise ValueError('Unexpected deployment immutables')
    runtime=bytearray.fromhex(a['deployedBytecode']['object'].removeprefix('0x'))
    for items in refs.values():
        for slot in items:
            if slot['length']!=32:raise ValueError('Unexpected immutable encoding')
            runtime[slot['start']:slot['start']+32]=CHAIN.to_bytes(32,'big')
    init=code+encode(['address'],[OWNER])
    result=dict(chainId=CHAIN,owner=OWNER,manager=OWNER,aprBps=500,maxLtvBps=7000,data='0x'+init.hex(),
        initHash='0x'+keccak(init).hex(),runtimeCodeHash='0x'+keccak(runtime).hex(),
        sourceKeccak256=sources,loanAsset='test DOGE',conversionEnabled=False,
        prepared=True,deployed=False,verifiedWalletFlow=False)
    out=ROOT/'site/vela-dashboard/src/owner/doge-lending-deployment.json'
    out.write_text(json.dumps(result,indent=2)+'\n')
    return dict(path=str(out),initHash=result['initHash'],runtimeCodeHash=result['runtimeCodeHash'],unsigned=True)
if __name__=='__main__':print(json.dumps(build()))
