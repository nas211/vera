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


def prepare(deployer=None, nonce=None, attestor=None, treasury=None, owner=None):
    values = (deployer, nonce, attestor, treasury)
    if any(x is not None for x in values) and not all(x is not None for x in values):
        raise ValueError('Provide deployer, nonce, attestor and treasury together')
    if owner and deployer is None:
        raise ValueError('Vault prediction requires the factory deployment inputs')
    contracts = {}
    binaries = {}
    for name in ('VaultFactory', 'DogeCollateralVault'):
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
    if deployer is not None:
        deployer, attestor, treasury = map(address, (deployer, attestor, treasury))
        if isinstance(nonce, bool) or not isinstance(nonce, int) or nonce < 0:
            raise ValueError('A nonnegative deployment nonce is required')
        factory = to_checksum_address(keccak(rlp.encode([bytes.fromhex(deployer[2:]), nonce]))[-20:])
        arguments = encode(['address', 'address'], [attestor, treasury])
        initcode = binaries['VaultFactory'] + arguments
        result['unsignedTransaction'] = {
            'from': deployer, 'chainId': CHAIN, 'nonce': nonce, 'value': '0x0',
            'data': '0x' + initcode.hex(),
            'note': 'No gas/fee quotation or signature. Recheck nonce, chain, code and funding immediately before an approved deployment.',
        }
        contracts['VaultFactory']['predictedAddress'] = factory
        contracts['VaultFactory']['verificationCommand'] = verification_command(
            'VaultFactory', factory, arguments, contracts['VaultFactory']['compiler'])
        if owner:
            owner = address(owner)
            args = encode(['address', 'address', 'address'], [owner, attestor, treasury])
            salt = bytes.fromhex(owner[2:]).rjust(32, b'\0')
            predicted = to_checksum_address(keccak(b'\xff' + bytes.fromhex(factory[2:]) + salt
                + keccak(binaries['DogeCollateralVault'] + args))[-20:])
            contracts['DogeCollateralVault']['predictedAddress'] = predicted
            contracts['DogeCollateralVault']['verificationCommand'] = verification_command(
                'DogeCollateralVault', predicted, args, contracts['DogeCollateralVault']['compiler'])
        result['pending'] = ['Recheck deployer nonce and testnet balance', 'Explicit deployment/spending approval',
                             'Confirmed receipt, deployed code/immutable values and explorer verification']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('deployer', 'attestor', 'treasury', 'owner'):
        parser.add_argument('--' + name)
    parser.add_argument('--nonce', type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(**vars(args)), sort_keys=True, indent=2))
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
