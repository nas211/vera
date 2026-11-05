"""Export public, unsigned owner-deployment inputs from the verified artifacts."""
import json
from pathlib import Path
import sys
from eth_utils import keccak
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.prepare_contracts import prepare

OWNER = '0xF8b7120F466f39063398fdc62F5f8cBCe37Ad1dB'
ATTESTOR = '0x519d809a40fff13CD3c820aE5Ae542152eFfac70'

if __name__ == '__main__':
    prepared = prepare(deployer=OWNER,attestor=ATTESTOR,treasury=OWNER,
                       rewarded=True,reward_manager=OWNER,apr_bps=500)
    data = prepared['unsignedTransaction']['data']
    reviewed = json.loads((ROOT/'.runtime/releases/noah-factory-live-review/latest-quote.json').read_text())
    if reviewed['unsignedTransaction']['data'] != data or reviewed['unsignedTransaction']['from'].lower() != OWNER.lower():
        raise RuntimeError('Reviewed deployment calldata differs')
    public = dict(chainId=6281971,owner=OWNER,attestor=ATTESTOR,treasury=OWNER,
                  manager=OWNER,aprBps=500,data=data,initHash='0x'+keccak(bytes.fromhex(data[2:])).hex(),
                  expectedNonce=reviewed['unsignedTransaction']['nonce'])
    path = ROOT/'site/vela-dashboard/src/owner/deployment.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(public,indent=2)+'\n')
    print(json.dumps(dict(unsigned=True,initHash=public['initHash'])))
