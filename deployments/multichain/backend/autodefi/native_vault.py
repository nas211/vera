"""Canonical native vault/reward observations, over the existing DogeOS connection.

No funds or rewards are recorded in a second ledger. Deployment hashes must be
reviewed explicitly; legacy owners are retained and duplicate registrations fail closed.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3
from .chain import abi
from .config import CHAIN_ID, ROOT

# Bounded across observations, not a fresh pool for each browser/tab. Every
# value remains pinned to the same confirmed height and is reorg-checked below.
READERS = ThreadPoolExecutor(max_workers=8, thread_name_prefix='native-read')


def read_values(contract, names, height):
    def read(name):
        return getattr(contract.functions, name)().call(block_identifier=height)
    return dict(zip(names, READERS.map(read, names)))


def hexed(value):
    return '0x' + bytes(value).hex()


def state(chain, owner, tx=None, migration=None):
    c, w3 = chain.config, chain.w3
    if not c.network_enabled:
        raise ValueError('Native vault reads are disabled')
    if tx is not None and not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx):
        raise ValueError('Invalid transaction hash')
    if w3.eth.chain_id != CHAIN_ID:
        raise ValueError('Wrong RPC chain')
    head = w3.eth.get_block('latest')
    if not 0 <= time.time() - head.timestamp <= 90:
        raise ValueError('Stale native chain head')
    height = head.number - c.confirmations + 1
    if height < 0:
        raise ValueError('Insufficient confirmations')
    observed = w3.eth.get_block(height)
    legacy = json.loads((ROOT / 'config/dogeos-deployment.json').read_text())['contracts']
    deployments = []
    if c.factory:
        if c.factory.lower() != legacy['VaultFactory']['address'].lower():
            raise ValueError('Legacy factory lacks a reviewed deployment record')
        deployments.append((c.factory, 'VaultFactory', 'DogeCollateralVault', legacy['VaultFactory']['runtimeKeccak256'], legacy['DogeCollateralVault']['runtimeKeccak256']))
    if c.reward_factory:
        deployments.append((c.reward_factory, 'RewardedVaultFactory', 'RewardedCollateralVault', c.reward_factory_codehash, c.reward_vault_codehash))
    if not deployments:
        raise ValueError('No native vault deployment configured')
    if len({d[0].lower() for d in deployments}) != len(deployments):
        raise ValueError('Duplicate factory configuration')
    def code(target, expected):
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}', expected or ''):
            raise ValueError('Reviewed runtime hash required')
        runtime = w3.eth.get_code(Web3.to_checksum_address(target), block_identifier=height)
        if not runtime or hexed(Web3.keccak(runtime)).lower() != expected.lower():
            raise ValueError('Native runtime bytecode mismatch')
    candidates = []
    reward_target = None
    pool_state = None
    pool = None
    for deployment in deployments:
        factory_address, factory_name, vault_name, factory_hash, vault_hash = deployment
        code(factory_address, factory_hash)
        factory = w3.eth.contract(Web3.to_checksum_address(factory_address), abi=abi(factory_name))
        if factory_name == 'RewardedVaultFactory':
            pool_address = factory.functions.rewardPool().call(block_identifier=height)
            code(pool_address, c.reward_pool_codehash)
            pool = w3.eth.contract(Web3.to_checksum_address(pool_address), abi=abi('NativeRewardPool'))
            pool_values = read_values(pool, ('factory', 'rewardManager', 'expectedChainId', 'rewardReserve',
                'totalRewardsFunded', 'totalRewardsClaimed', 'DENOMINATOR', 'previewIndex',
                'availableReserve', 'aprBps', 'totalPrincipal'), height)
            pool_read = pool_values.__getitem__
            manager = factory.functions.rewardManager().call(block_identifier=height)
            if (pool_read('factory').lower() != factory_address.lower() or pool_read('rewardManager').lower() != manager.lower()
                    or pool_read('expectedChainId') != CHAIN_ID):
                raise ValueError('Reward pool role or domain mismatch')
            reserve, funded, claimed = (pool_read(name) for name in ('rewardReserve', 'totalRewardsFunded', 'totalRewardsClaimed'))
            denominator = pool_read('DENOMINATOR')
            _, liability = pool_read('previewIndex')
            available = pool_read('availableReserve')
            if (denominator != 10_000 * 365 * 86400 or liability < 0 or liability > reserve * denominator
                    or available != (reserve * denominator - liability) // denominator
                    or funded - claimed != reserve or w3.eth.get_balance(Web3.to_checksum_address(pool_address), block_identifier=height) < reserve):
                raise ValueError('Shared reward pool backing invariant failed')
            pool_state = dict(source='chain', address=pool_address.lower(), factory=factory_address.lower(), manager=manager.lower(),
                              codeHash=c.reward_pool_codehash, bytecodeVerified=True, aprBps=str(pool_read('aprBps')),
                              reserveWei=str(reserve), availableReserveWei=str(available), reservedWei=str(reserve-available),
                              fundedWei=str(funded), claimedWei=str(claimed), totalPrincipalWei=str(pool_read('totalPrincipal')))
        mapped = factory.functions.vaultOf(Web3.to_checksum_address(owner)).call(block_identifier=height)
        if factory_name == 'RewardedVaultFactory':
            reward_target = dict(targetFactory=factory_address.lower(),
                targetVault=factory.functions.predict(Web3.to_checksum_address(owner)).call(block_identifier=height).lower(),
                factoryCodeHash=factory_hash)
        if int(mapped, 16):
            if factory.functions.predict(Web3.to_checksum_address(owner)).call(block_identifier=height).lower() != mapped.lower():
                raise ValueError('Factory prediction mismatch')
            candidates.append((deployment, factory, mapped))
    if len(candidates) > 1 and not migration:
        raise ValueError('Multiple registered vaults require explicit migration reconciliation')
    selected = candidates[0][0] if candidates else deployments[-1]
    factory_address, factory_name, vault_name, factory_hash, vault_hash = selected
    result = None
    results = []
    for deployment, factory, mapped in candidates:
        factory_address, factory_name, vault_name, factory_hash, vault_hash = deployment
        code(mapped, vault_hash)
        vault = w3.eth.contract(Web3.to_checksum_address(mapped), abi=abi(vault_name))
        names = ['owner', 'settlementSigner', 'liquidationTreasury', 'limits', 'collateral', 'withdrawable',
                 'loanState', 'loanId', 'loanNonce', 'principalMicro', 'paused', 'trading', 'agent',
                 'permissionRevision', 'badDebtMicro']
        if vault_name == 'RewardedCollateralVault':
            names += ['rewardPool', 'expectedChainId', 'rewardManager', 'previewRewards', 'stopBudgetMicro']
        read = read_values(vault, names, height).__getitem__
        if read('owner').lower() != owner.lower():
            raise ValueError('Vault ownership mismatch')
        for role in ('settlementSigner', 'liquidationTreasury'):
            if read(role).lower() != getattr(factory.functions, role)().call(block_identifier=height).lower():
                raise ValueError('Vault role mismatch')
        limits = dict(zip(('allocation', 'positionSize', 'expiresAt', 'maxLoss', 'leverage', 'markets', 'autoReduce'), read('limits')))
        result = dict(address=mapped.lower(), owner=owner.lower(), chainId=CHAIN_ID, factory=factory_address.lower(),
                      kind='rewarded' if vault_name == 'RewardedCollateralVault' else 'legacy',
                      block=height, blockHash=hexed(observed.hash), collateral=str(read('collateral')),
                      withdrawable=str(read('withdrawable')), loanState=read('loanState'), loanId=hexed(read('loanId')),
                      loanNonce=read('loanNonce'), principalMicro=str(read('principalMicro')), paused=read('paused'), trading=read('trading'),
                      agent=read('agent'), limits={k: v if isinstance(v, bool) else str(v) for k, v in limits.items()},
                      permissionRevision=read('permissionRevision'), settlementSigner=read('settlementSigner'), badDebtMicro=str(read('badDebtMicro')),
                      bytecodeVerified=True, rewards=None)
        if w3.eth.get_balance(Web3.to_checksum_address(mapped), block_identifier=height) < int(result['collateral']):
            raise ValueError('Vault collateral backing invariant failed')
        if vault_name == 'RewardedCollateralVault':
            if (not pool_state or read('rewardPool').lower() != pool_state['address'] or read('expectedChainId') != CHAIN_ID
                    or read('rewardManager').lower() != pool_state['manager']):
                raise ValueError('Reward vault domain or manager mismatch')
            claimable, carry = read('previewRewards')
            balance = w3.eth.get_balance(Web3.to_checksum_address(mapped), block_identifier=height)
            pool_owner, pool_principal, _, _, pool_claimed = pool.functions.accounts(mapped).call(block_identifier=height)
            if (claimable > int(pool_state['reserveWei']) or balance < int(result['collateral'])
                    or pool_owner.lower() != owner.lower() or pool_principal != int(result['collateral'])
                    or tuple(pool.functions.preview(mapped).call(block_identifier=height)) != (claimable, carry)):
                raise ValueError('Reward backing invariant failed')
            result['stopBudgetMicro'] = str(read('stopBudgetMicro'))
            result['rewards'] = dict(source='chain', pool=pool_state['address'], aprBps=pool_state['aprBps'], reserveWei=pool_state['reserveWei'],
                                     claimableWei=str(claimable), availableReserveWei=pool_state['availableReserveWei'], carry=str(carry),
                                     claimedWei=str(pool_claimed), fundedWei=pool_state['fundedWei'],
                                     manager=read('rewardManager'), principalWei=result['collateral'], loanLocked=read('loanState') in (1, 2, 4))
        results.append(result)
    upgrade = None
    if len(results) > 1:
        from .vault_migration import empty_legacy
        retired = next((v for v in results if v['kind'] == 'legacy'), None)
        rewarded = next((v for v in results if v['kind'] == 'rewarded'), None)
        if (not migration or migration.get('version') != 1 or migration.get('owner') != owner.lower()
                or migration.get('chainId') != CHAIN_ID or not empty_legacy(retired) or not rewarded
                or retired['address'] != migration.get('sourceVault')
                or rewarded['address'] != migration.get('targetVault')
                or rewarded['factory'] != migration.get('targetFactory')
                or retired['loanNonce'] != migration.get('sourceNonce')
                or retired['permissionRevision'] != migration.get('sourceRevision')):
            raise ValueError('Multiple registered vaults require explicit migration reconciliation; legacy vault must remain empty and settled')
        result = dict(rewarded, migratedFrom=retired)
        selected = next(d for d, _, m in candidates if m.lower() == rewarded['address'])
    elif result and result['kind'] == 'legacy' and reward_target:
        from .vault_migration import empty_legacy
        upgrade = dict(reward_target, sourceVault=result['address'], eligible=empty_legacy(result),
            reason=None if empty_legacy(result) else 'Withdraw all legacy collateral and confirm settlement first.')
    factory_address, factory_name, vault_name, factory_hash, vault_hash = selected
    transaction = None
    if tx:
        transaction = dict(hash=tx, status='PENDING')
        try:
            receipt = w3.eth.get_transaction_receipt(tx)
        except Exception as exc:
            from web3.exceptions import TransactionNotFound
            if not isinstance(exc, TransactionNotFound):
                raise
            receipt = None
        if receipt and receipt.blockNumber <= height:
            sent = w3.eth.get_transaction(tx)
            allowed = {factory_address.lower(), result['address'] if result else ''}
            if pool_state:
                allowed.add(pool_state['address'])
            if (hexed(receipt.transactionHash).lower() != tx.lower() or hexed(sent['hash']).lower() != tx.lower()
                    or sent['from'].lower() != owner.lower() or (sent['to'] or '').lower() not in allowed
                    or sent['blockHash'] != receipt.blockHash or w3.eth.get_block(receipt.blockNumber).hash != receipt.blockHash):
                raise ValueError('Transaction identity or canonical receipt mismatch')
            reward_paid = 0
            pool_paid = 0
            topic = Web3.keccak(text='RewardClaimed(address,uint256)')
            pool_topic = Web3.keccak(text='PoolRewardPaid(address,address,uint256)')
            for log in receipt.logs:
                if result and log['address'].lower() == result['address'] and log['topics'] and log['topics'][0] == topic:
                    if len(log['topics']) != 2 or '0x' + bytes(log['topics'][1])[-20:].hex() != owner.lower() or len(log['data']) != 32:
                        raise ValueError('Invalid reward claim event')
                    reward_paid += int.from_bytes(log['data'], 'big')
                if pool_state and log['address'].lower() == pool_state['address'] and log['topics'] and log['topics'][0] == pool_topic:
                    if (not result or len(log['topics']) != 3 or '0x' + bytes(log['topics'][1])[-20:].hex() != result['address']
                            or '0x' + bytes(log['topics'][2])[-20:].hex() != owner.lower() or len(log['data']) != 32):
                        raise ValueError('Invalid shared pool payout event')
                    pool_paid += int.from_bytes(log['data'], 'big')
            if result and result['kind'] == 'rewarded' and pool_paid != reward_paid:
                raise ValueError('Shared pool and vault payout events disagree')
            transaction = dict(hash=tx, status='CONFIRMED' if receipt.status == 1 else 'REVERTED',
                               block=receipt.blockNumber, blockHash=hexed(receipt.blockHash), sender=sent['from'], to=sent['to'],
                               data=hexed(sent['input']), valueWei=str(sent['value']), rewardPaidWei=str(reward_paid))
    wallet_balance = w3.eth.get_balance(Web3.to_checksum_address(owner), block_identifier=height)
    if w3.eth.get_block(height).hash != observed.hash:
        raise ValueError('Native observation reorganized')
    return dict(source='chain', chainId=CHAIN_ID, owner=owner.lower(), factory=factory_address.lower(),
                factoryKind='rewarded' if vault_name == 'RewardedCollateralVault' else 'legacy', bytecodeVerified=True,
                factoryCodeHash=factory_hash, vaultCodeHash=vault_hash,
                block=height, blockHash=hexed(observed.hash), observedAt=int(time.time()), confirmations=c.confirmations,
                walletBalanceWei=str(wallet_balance),
                vault=result, rewardPool=pool_state, upgrade=upgrade, transaction=transaction)
