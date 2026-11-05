"""Read-only confirmed DogeOS state; wallet performs every on-chain write."""
import json,time
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_abi import encode
from web3 import Web3
from vela.db import transaction
from .config import ROOT,CHAIN_ID
from .store import event

def abi(name): return json.loads((ROOT/'config'/f'{name}.abi.json').read_text())
class DogeOS:
    def __init__(self,config):
        self.config=config
        self.w3=Web3(Web3.HTTPProvider(config.rpc,request_kwargs={'timeout':8}))
    def state(self,owner,tx=None,migration=None):
        from .native_vault import state
        return state(self,owner,tx,migration=migration)
    def read(self,owner,tx=None,migration=None):
        if self.config.reward_factory:
            observed=self.state(owner,tx,**({"migration":migration} if migration else {}))
            result=observed['vault']
            if not result:raise ValueError('Create and confirm your vault first')
            receipt=observed['transaction']
            if tx and (not receipt or receipt['status']!='CONFIRMED'):raise ValueError('Confirmed successful owner transaction required')
            return result,receipt
        c=self.config
        if not c.network_enabled or not c.factory: raise ValueError('DogeOS factory not deployed/configured; testnet authorization required')
        if self.w3.eth.chain_id!=CHAIN_ID: raise ValueError('Wrong RPC chain')
        head=self.w3.eth.get_block('latest')
        if abs(time.time()-head.timestamp)>90: raise ValueError('Stale chain head')
        height=head.number-c.confirmations+1
        block_hash=self.w3.eth.get_block(height).hash
        factory=self.w3.eth.contract(Web3.to_checksum_address(c.factory),abi=abi('VaultFactory'))
        address=factory.functions.vaultOf(Web3.to_checksum_address(owner)).call(block_identifier=height)
        if int(address,16)==0: raise ValueError('Create and confirm your vault first')
        vault=self.w3.eth.contract(address,abi=abi('DogeCollateralVault'))
        def call(name): return getattr(vault.functions,name)().call(block_identifier=height)
        if call('owner').lower()!=owner: raise ValueError('Vault ownership mismatch')
        limits=dict(zip(('allocation','positionSize','expiresAt','maxLoss','leverage','markets','autoReduce'),call('limits')))
        result=dict(address=address.lower(),owner=owner,chainId=CHAIN_ID,block=height,blockHash=block_hash.hex(),
            collateral=str(call('collateral')),withdrawable=str(call('withdrawable')),loanState=call('loanState'),loanId='0x'+bytes(call('loanId')).hex(),
            loanNonce=call('loanNonce'),principalMicro=str(call('principalMicro')),paused=call('paused'),trading=call('trading'),
            agent=call('agent'),limits={k:str(v) if isinstance(v,int) and not isinstance(v,bool) else v for k,v in limits.items()},
            permissionRevision=call('permissionRevision'),settlementSigner=call('settlementSigner'),badDebtMicro=str(call('badDebtMicro')))
        receipt=None
        if tx:
            r=self.w3.eth.get_transaction_receipt(tx); t=self.w3.eth.get_transaction(tx)
            if r.status!=1 or r.blockNumber>height or t['from'].lower()!=owner or (t['to'] or '').lower() not in (address.lower(),c.factory.lower()): raise ValueError('Confirmed owner vault transaction required')
            if self.w3.eth.get_block(r.blockNumber).hash!=r.blockHash: raise ValueError('Transaction reorganized')
            receipt=dict(hash=r.transactionHash.hex(),block=r.blockNumber,blockHash=r.blockHash.hex())
        if self.w3.eth.get_block(height).hash!=block_hash: raise ValueError('Vault observation reorganized; retry confirmation')
        return result,receipt
    def sync(self,db,owner,tx=None):
        from .vault_migration import context
        result,receipt=self.read(owner,tx,**context(db,owner))
        return DogeOS.record(db,owner,result,receipt)
    @staticmethod
    def record(db,owner,result,receipt=None):
        if result['owner']!=owner or result['chainId']!=CHAIN_ID: raise ValueError('Confirmed vault owner and Chikyū chain required')
        with transaction(db):
            previous_state=db.execute('SELECT data FROM vaults WHERE owner=?',(owner,)).fetchone()
            if previous_state and int(result['block'])<int(json.loads(previous_state[0])['block']):
                # A slow manual sync must not overwrite a newer revocation, settlement,
                # or withdrawal observed by the background RPC reader.
                raise ValueError('Older vault observation discarded; refresh confirmed state')
            if previous_state and json.loads(previous_state[0])['address'] != result['address']:
                from .vault_migration import verify_switch
                verify_switch(db,owner,json.loads(previous_state[0]),result)
            db.execute('INSERT OR REPLACE INTO vaults VALUES (?,?,?,?)',(owner,result['address'],json.dumps(result),time.time()))
            if receipt:
                previous=db.execute('SELECT owner FROM chain_receipts WHERE hash=?',(receipt['hash'],)).fetchone()
                if previous and previous[0]!=owner: raise ValueError('Foreign transaction')
                if not previous:
                    db.execute('INSERT INTO chain_receipts VALUES (?,?,?,?)',(receipt['hash'],owner,receipt['block'],receipt['blockHash']))
                    event(db,owner,result['loanId'],'CONFIRMED_TESTNET_TRANSACTION','0',receipt['hash'])
            loan=db.execute('SELECT id,state FROM loans WHERE vault=? AND nonce=?',(result['address'],result['loanNonce'])).fetchone()
            if loan and result['loanState'] in (3,4) and loan['state'] in ('AWAITING_CHAIN','DEFAULT_PENDING'):
                db.execute('UPDATE loans SET state=? WHERE id=?',('REPAID' if result['loanState']==3 else 'DEFAULTED',loan['id']))
        return result

def certificate(config,v,action,a=0,b=0,c=0):
    if not config.attestor_key: raise ValueError('Dedicated demo attestor not configured')
    signer=Account.from_key(config.attestor_key)
    if signer.address.lower()!=v['settlementSigner'].lower(): raise ValueError('Demo attestor differs from deployed vault')
    deadline=int(time.time())+600
    digest=Web3.keccak(encode(['bytes32','uint256','address','address','uint256','uint8','bytes32','uint256','uint256','uint256','uint64'],
        [Web3.keccak(text='ADP_DEMO_V1'),CHAIN_ID,v['address'],v['owner'],v['loanNonce'],action,bytes.fromhex(v['loanId'][2:]),int(a),int(b),int(c),deadline]))
    signature=signer.sign_message(encode_defunct(primitive=digest)).signature.hex()
    return dict(action=action,a=str(a),b=str(b),c=str(c),deadline=deadline,signature='0x'+signature.removeprefix('0x'))
