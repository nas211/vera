"""Explicit native-workflow settings for the existing PAPER / DEMO service.

Reads only this project's named runtime variables after opt-in. Never opens a
secret file, copies credentials, changes ledgers or contacts the chain.
"""
from dataclasses import replace
import json
import re

from eth_account import Account
from autodefi.config import ROOT, env


def configured(config):
    flag = env('NATIVE_ACTIONS_ENABLED', '0')
    if flag == '0':
        # The registered deployment remains readable before reward activation.
        # Only public metadata is loaded; this never imports a signer or enables writes.
        if config.network_enabled and config.rpc == 'https://rpc.testnet.dogeos.com/':
            legacy = json.loads((ROOT / 'config/dogeos-deployment.json').read_text())['contracts']['VaultFactory']['address']
            if config.factory and config.factory.lower() != legacy.lower():
                raise ValueError('Preserve the registered legacy factory and its owners')
            return replace(config, factory=legacy)
        return config
    if flag != '1' or env('EXECUTION', 'PAPER') != 'PAPER' or env('LENDING', 'DEMO') != 'DEMO':
        raise ValueError('Native workflow requires an explicit PAPER / DEMO testnet setting')
    if not config.network_enabled or config.rpc != 'https://rpc.testnet.dogeos.com/':
        raise ValueError('Native workflow requires the configured DogeOS testnet reader')
    legacy = json.loads((ROOT / 'config/dogeos-deployment.json').read_text())['contracts']['VaultFactory']['address']
    factory = env('FACTORY', legacy)
    reward = env('REWARD_FACTORY')
    if factory.lower() != legacy.lower():
        raise ValueError('Preserve the registered legacy factory and its owners')
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', reward) or int(reward, 16) == 0 or reward.lower() == factory.lower():
        raise ValueError('Distinct deployed reward factory required')
    hashes = {name: env(name) for name in ('REWARD_FACTORY_CODEHASH', 'REWARD_VAULT_CODEHASH', 'REWARD_POOL_CODEHASH')}
    if any(not re.fullmatch(r'0x[0-9a-fA-F]{64}', value) or int(value, 16) == 0 for value in hashes.values()):
        raise ValueError('All three reviewed runtime hashes are required')
    signer_address, signer_key = env('DEMO_ATTESTOR_ADDRESS'), env('DEMO_ATTESTOR_KEY')
    try:
        valid_signer = (re.fullmatch(r'0x[0-9a-fA-F]{40}', signer_address)
                        and Account.from_key(signer_key).address.lower() == signer_address.lower())
    except Exception:
        valid_signer = False
    if not valid_signer:
        raise ValueError('Dedicated demo attestor must match its configured public address')
    return replace(config, factory=factory, reward_factory=reward,
                   reward_factory_codehash=hashes['REWARD_FACTORY_CODEHASH'],
                   reward_vault_codehash=hashes['REWARD_VAULT_CODEHASH'],
                   reward_pool_codehash=hashes['REWARD_POOL_CODEHASH'],
                   attestor_key=signer_key, native_actions_enabled=True)
