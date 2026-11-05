#!/usr/bin/env python3
"""Prepare reproducible deployment inputs. This program has no RPC/write/signing path."""
import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path

import rlp
from eth_abi import encode
from eth_utils import keccak, to_checksum_address, is_address

ROOT = Path(__file__).resolve().parents[1]
CHAIN = 6281971
RPC = 'https://rpc.testnet.dogeos.com/'
EXPLORER = 'https://dogeos-testnet.l2scan.co'


def address(value):
    if not is_address(value) or int(value, 16) == 0:
        raise ValueError('A nonzero public EVM address is required')
    return to_checksum_address(value)


def artifact(name):
    data = json.loads((ROOT / f'contracts/out/{name}.sol/{name}.json').read_text())
    metadata = data['metadata']
    if not metadata['compiler']['version'].startswith('0.8.30+'):
        raise ValueError('Unexpected contract compiler')
    settings = metadata['settings']
    if settings['evmVersion'] != 'prague' or settings['optimizer'] != {'enabled': True, 'runs': 200}:
        raise ValueError('Unexpected compiler settings')
    if data['bytecode']['linkReferences']:
        raise ValueError('Unresolved contract libraries')
    sources = {}
    for name_, source in metadata['sources'].items():
        path = (ROOT / 'contracts' / name_).resolve()
        if not path.is_relative_to(ROOT / 'contracts'):
            raise ValueError('Contract source escaped project')
        digest = '0x' + keccak(path.read_bytes()).hex()
        if digest != source['keccak256']:
            raise ValueError('Compiled artifact does not match current source: ' + name_)
        sources[name_] = digest
    if data['abi'] != json.loads((ROOT / f'config/{name}.abi.json').read_text()):
        raise ValueError('Frontend ABI differs from compiled contract')
    bytecode = bytes.fromhex(data['bytecode']['object'].removeprefix('0x'))
    return data, bytecode, sources


def verification_command(name, target, arguments, version):
    return shlex.join([
        str(ROOT / '.tools/foundry/forge'), 'verify-contract', target,
        f'src/{name}.sol:{name}', '--root', str(ROOT / 'contracts'),
        '--chain', str(CHAIN), '--rpc-url', RPC, '--compiler-version', version,
        '--num-of-optimizations', '200', '--evm-version', 'prague',
        '--constructor-args', '0x' + arguments.hex() if isinstance(arguments,bytes) else arguments,
        '--verifier', 'blockscout', '--verifier-url', EXPLORER + '/api/',
        '--skip-is-verified-check', '--watch',
    ])


def prepare(deployer=None, nonce=None, attestor=None, treasury=None, owner=None,
            rewarded=False, reward_manager=None, apr_bps=None):
    if not rewarded and (reward_manager is not None or apr_bps is not None):
        raise ValueError('Reward settings require the rewarded contract candidate')
    values = ((deployer, attestor, treasury, reward_manager, apr_bps) if rewarded
              else (deployer, nonce, attestor, treasury))
    if any(x is not None for x in values) and not all(x is not None for x in values):
        raise ValueError('Provide all public deployment roles and reward terms together' if rewarded
                         else 'Provide deployer, nonce, attestor and treasury together')
    if nonce is not None and (deployer is None or isinstance(nonce, bool) or not isinstance(nonce, int) or nonce < 0):
        raise ValueError('A deployment nonce requires public roles and a nonnegative integer')
    if rewarded and apr_bps is not None and (isinstance(apr_bps, bool) or not isinstance(apr_bps, int) or not 0 <= apr_bps <= 10_000):
        raise ValueError('Reward APR must be integer basis points from 0 to 10000')
    if owner and deployer is None:
        raise ValueError('Vault prediction requires the factory deployment inputs')
    factory_name, vault_name = (('RewardedVaultFactory', 'RewardedCollateralVault') if rewarded
                               else ('VaultFactory', 'DogeCollateralVault'))
    contracts = {}
    binaries = {}
    for name in (factory_name, vault_name) + (('NativeRewardPool',) if rewarded else ()):
        data, bytecode, sources = artifact(name)
        binaries[name] = bytecode
        contracts[name] = {
            'compiler': data['metadata']['compiler']['version'],
            'evmVersion': 'prague', 'optimizerRuns': 200,
            'creationBytecodeKeccak256': '0x' + keccak(bytecode).hex(),
            'creationBytecodeSha256': hashlib.sha256(bytecode).hexdigest(),
            'creationBytes': len(bytecode), 'sourceKeccak256': sources,
            'abiSha256': hashlib.sha256(json.dumps(data['abi'], sort_keys=True).encode()).hexdigest(),
            'predictedAddress': None, 'deployedAddress': None, 'verificationCommand': None,
            'verificationCommandTemplate': verification_command(name, '<CONFIRMED_CONTRACT_ADDRESS>',
                '<ABI_ENCODED_CONSTRUCTOR_ARGUMENTS>', data['metadata']['compiler']['version']),
            'constructorInputs': next(x['inputs'] for x in data['abi'] if x['type']=='constructor'),
        }
    result = {
        'project': 'VELLA', 'chainId': CHAIN, 'rpc': RPC, 'explorer': EXPLORER,
        'nativeAsset': 'DOGE', 'nativeDecimals': 18,
        'nativeErc20': '0x530000000000000000000000000000000000d09e',
        'collateralAccounting': 'Native deposit() only. ERC-20 balanceOf is the same DOGE, never added.',
        'approvalRequired': True, 'broadcast': False,
        'contracts': contracts, 'unsignedTransaction': None,
        'pending': ['Authorized deployer address and current nonce', 'Dedicated attestor and testnet treasury addresses',
                    'Explicit deployment/spending approval', 'Confirmed receipt and explorer verification'],
        'verificationDocumentation': 'https://docs.dogeos.com/en/developers/verifying-smart-contracts',
    }
    if rewarded:
        result.update(candidate='FUNDED_NATIVE_REWARDS', customerTransactionsEnabled=False,
                      rewardTerms={'manager': None, 'initialAprBps': None, 'reserveFunding': 'One shared project pool; anyone may fund it without a personal vault'},
                      backendRegistration={'VERA_PROTOCOL_REWARD_FACTORY': None,
                                           'VERA_PROTOCOL_REWARD_FACTORY_CODEHASH': None,
                                           'VERA_PROTOCOL_REWARD_VAULT_CODEHASH': None,
                                           'VERA_PROTOCOL_REWARD_POOL_CODEHASH': None,
                                           'VERA_PROTOCOL_NATIVE_ACTIONS_ENABLED': '0'},
                      legacyPolicy='Keep the registered legacy factory and owners; no automatic migration or second vault for an existing owner',
                      feeRequirements=['Fresh execution gas estimate', 'Fresh DogeOS data/finality fee estimate',
                                       'Funded sender balance covers both fees; reserve donation is separate'],
                      pending=['Reward manager and initial APR', 'Authorized deployment wallet',
                               'Live chain, nonce, balance and both fee estimates', 'Canonical deployment receipt and runtime hashes',
                               'Shared pool funding and owner-wallet flow verification'])
    if deployer is not None:
        deployer, attestor, treasury = map(address, (deployer, attestor, treasury))
        if rewarded:
            reward_manager = address(reward_manager)
            result['rewardTerms'].update(manager=reward_manager, initialAprBps=apr_bps)
        factory = to_checksum_address(keccak(rlp.encode([bytes.fromhex(deployer[2:]), nonce]))[-20:]) if nonce is not None else None
        argument_types = ['address', 'address'] + (['address', 'uint256'] if rewarded else [])
        argument_values = [attestor, treasury] + ([reward_manager, apr_bps] if rewarded else [])
        arguments = encode(argument_types, argument_values)
        initcode = binaries[factory_name] + arguments
        result['unsignedTransaction'] = {
            'from': deployer, 'chainId': CHAIN, 'value': '0x0',
            'data': '0x' + initcode.hex(),
            'note': 'No gas/fee quotation or signature. Recheck nonce, chain, code and funding immediately before an approved deployment.',
        }
        if nonce is not None:
            result['unsignedTransaction']['nonce'] = nonce
        contracts[factory_name]['constructorArguments'] = '0x' + arguments.hex()
        contracts[factory_name]['predictedAddress'] = factory
        if factory:
            contracts[factory_name]['verificationCommand'] = verification_command(
                factory_name, factory, arguments, contracts[factory_name]['compiler'])
        pool_address = None
        if rewarded:
            pool_args = encode(['address', 'uint256'], [reward_manager, apr_bps])
            pool_row = contracts['NativeRewardPool']
            pool_row.update(constructorArguments='0x' + pool_args.hex(), deployment='created inside factory constructor')
            if factory:
                # A newly created EVM contract starts with nonce 1; this is its first CREATE.
                pool_address = to_checksum_address(keccak(rlp.encode([bytes.fromhex(factory[2:]), 1]))[-20:])
                pool_row['predictedAddress'] = pool_address
                pool_row['verificationCommand'] = verification_command('NativeRewardPool', pool_address, pool_args, pool_row['compiler'])
        if owner:
            owner = address(owner)
            args = (encode(['address', 'address', 'address', 'address'], [owner, attestor, treasury, pool_address]) if pool_address
                    else None) if rewarded else encode(['address'] + argument_types, [owner] + argument_values)
            salt = bytes.fromhex(owner[2:]).rjust(32, b'\0')
            contracts[vault_name]['constructorArguments'] = '0x' + args.hex() if args is not None else None
            if factory:
                predicted = to_checksum_address(keccak(b'\xff' + bytes.fromhex(factory[2:]) + salt
                    + keccak(binaries[vault_name] + args))[-20:])
                contracts[vault_name]['predictedAddress'] = predicted
                contracts[vault_name]['verificationCommand'] = verification_command(
                    vault_name, predicted, args, contracts[vault_name]['compiler'])
        result['pending'] = ['Recheck deployer nonce and testnet balance', 'Explicit deployment/spending approval',
                             'Confirmed receipt, deployed code/immutable values and explorer verification']
        if rewarded:
            result['pending'] += ['Record all three runtime hashes and preserve legacy registrations',
                                  'Fund the shared pool once its deployment is confirmed', 'Verify real owner-wallet flows before enabling customer actions']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('deployer', 'attestor', 'treasury', 'owner', 'reward-manager'):
        parser.add_argument('--' + name)
    parser.add_argument('--nonce', type=int)
    parser.add_argument('--rewarded', action='store_true', help='Prepare the new funded-reward contracts, preserving the legacy deployment')
    parser.add_argument('--apr-bps', type=int, help='Initial simple annual rate in basis points; no assumed default')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(**vars(args)), sort_keys=True, indent=2))
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
