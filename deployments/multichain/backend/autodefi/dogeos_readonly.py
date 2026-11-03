"""Confirmed native-DOGE observations, kept outside every simulation ledger.

The transport explicitly allows reads only. No signer or transaction sender is
loaded by this module. All values in a snapshot refer to one confirmed block.
"""
import hashlib
import json
import time
from decimal import Decimal

import httpx
from eth_abi import encode, decode
from eth_utils import keccak, to_checksum_address

from .config import ROOT, CHAIN_ID
from .auth import address

READ_METHODS = frozenset({'eth_chainId', 'eth_getBlockByNumber', 'eth_getCode',
                          'eth_call', 'eth_getBalance', 'eth_getTransactionReceipt'})


class DogeOSReadOnly:
    def __init__(self, deployment=None, client=None):
        self.deployment = deployment or json.loads((ROOT / 'config/dogeos-deployment.json').read_text())
        d = self.deployment
        if d['chainId'] != CHAIN_ID or d['customerTransactionsEnabled'] is not False:
            raise ValueError('Read-only Chikyu configuration required')
        if d['rpc'] != 'https://rpc.testnet.dogeos.com/':
            raise ValueError('Official Chikyu RPC required')
        self.contracts = d['contracts']
        self.roles = d['roles']
        self.factory = self.contracts['VaultFactory']['address']
        self.reference = self.contracts['DogeCollateralVault']['address']
        self.abis = {}
        for name, expected in d['abiSha256'].items():
            raw = (ROOT / 'config' / (name + '.abi.json')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('Reviewed ABI changed')
            self.abis[name] = json.loads(raw)
        self.client = client or httpx.Client(timeout=10)

    def rpc(self, method, params):
        if method not in READ_METHODS:
            raise ValueError('Only read-only RPC methods are permitted')
        response = self.client.post(self.deployment['rpc'], json={
            'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        response.raise_for_status()
        result = response.json()
        if result.get('error') or 'result' not in result:
            raise ValueError('DogeOS read unavailable')
        return result['result']

    def call(self, target, contract, name, block, args=()):
        item = next(a for a in self.abis[contract] if a.get('type') == 'function' and a['name'] == name)
        if item['stateMutability'] not in ('view', 'pure'):
            raise ValueError('Read-only ABI function required')
        inputs = [a['type'] for a in item['inputs']]
        data = keccak(text=name + '(' + ','.join(inputs) + ')')[:4] + encode(inputs, args)
        raw = self.rpc('eth_call', [{'to': target, 'data': '0x' + data.hex()}, block])
        values = decode([a['type'] for a in item['outputs']], bytes.fromhex(raw[2:]))
        return values[0] if len(values) == 1 else values

    def code(self, target, name, block):
        raw = self.rpc('eth_getCode', [target, block])
        if '0x' + keccak(bytes.fromhex(raw[2:])).hex() != self.contracts[name]['runtimeKeccak256']:
            raise ValueError('Deployed runtime bytecode mismatch')

    def snapshot(self, wallet=None):
        owner = to_checksum_address(address(wallet or self.roles['vaultOwner']))
        if int(self.rpc('eth_chainId', []), 16) != CHAIN_ID:
            raise ValueError('Wrong RPC chain')
        head = self.rpc('eth_getBlockByNumber', ['latest', False])
        if not 0 <= time.time() - int(head['timestamp'], 16) < 90:
            raise ValueError('Stale DogeOS chain head')
        height = int(head['number'], 16) - 2
        block = hex(height)
        observed = self.rpc('eth_getBlockByNumber', [block, False])
        self.code(self.factory, 'VaultFactory', block)
        def factory(name, args=()):
            return self.call(self.factory, 'VaultFactory', name, block, args)
        for getter, role in (('settlementSigner', 'settlementAttestor'), ('liquidationTreasury', 'liquidationTreasury')):
            if factory(getter).lower() != self.roles[role].lower():
                raise ValueError('Factory role mismatch')
        mapped = factory('vaultOf', (owner,))
        registered = int(mapped, 16) != 0
        vault = None
        if registered:
            mapped = to_checksum_address(mapped)
            if factory('predict', (owner,)).lower() != mapped.lower():
                raise ValueError('Factory registration prediction mismatch')
            self.code(mapped, 'DogeCollateralVault', block)
            def read(name):
                return self.call(mapped, 'DogeCollateralVault', name, block)
            actual_owner, treasury, attestor = read('owner'), read('liquidationTreasury'), read('settlementSigner')
            if (actual_owner.lower() != owner.lower() or treasury.lower() != self.roles['liquidationTreasury'].lower()
                    or attestor.lower() != self.roles['settlementAttestor'].lower()):
                raise ValueError('Vault role mismatch')
            balance = int(self.rpc('eth_getBalance', [mapped, block]), 16)
            collateral, loan_state = read('collateral'), read('loanState')
            vault = dict(address=mapped, owner=to_checksum_address(actual_owner),
                         balanceWei=str(balance), balanceTestDOGE=str(Decimal(balance) / 10**18),
                         collateralWei=str(collateral), collateralTestDOGE=str(Decimal(collateral) / 10**18),
                         loanState=loan_state, paused=read('paused'), trading=read('trading'),
                         treasury=to_checksum_address(treasury), attestor=to_checksum_address(attestor),
                         ownershipVerified=True, registrationVerified=True, bytecodeVerified=True,
                         status='IDLE' if loan_state == 0 else 'ONCHAIN_STATE_' + str(loan_state))
        wallet_balance = int(self.rpc('eth_getBalance', [owner, block]), 16)
        if self.rpc('eth_getBlockByNumber', [block, False])['hash'] != observed['hash']:
            raise ValueError('DogeOS observation reorganized')
        return dict(status='VERIFIED', chainId=CHAIN_ID, network='DogeOS Chikyu Testnet',
                    block=height, blockHash=observed['hash'], observedAt=time.time(),
                    confirmations=3, wallet=owner, walletBalanceWei=str(wallet_balance),
                    walletBalanceTestDOGE=str(Decimal(wallet_balance) / 10**18), factory=self.factory,
                    registered=registered, vault=vault, referenceVault=self.reference,
                    referenceOwner=self.roles['vaultOwner'], explorer=self.deployment['explorer'],
                    deploymentTransactions={n:c['transactionHash'] for n,c in self.contracts.items()},
                    readOnly=True, customerTransactionsEnabled=False,
                    collateralUsedForPaperTrading=False, settlementOnDogeOS=False)

    def verify_deployment(self):
        result = self.snapshot()
        if not result['vault'] or result['vault']['address'].lower() != self.reference.lower():
            raise ValueError('Authorized reference vault not registered')
        for name, item in self.contracts.items():
            receipt = self.rpc('eth_getTransactionReceipt', [item['transactionHash']])
            if not receipt or int(receipt['status'], 16) != 1 or int(receipt['blockNumber'], 16) > result['block']:
                raise ValueError('Confirmed successful deployment receipt required')
            block = self.rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
            if block['hash'] != receipt['blockHash']:
                raise ValueError('Deployment receipt reorganized')
        return result
