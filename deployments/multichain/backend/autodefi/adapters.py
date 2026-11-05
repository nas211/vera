"""Network capabilities and identity boundaries; no signing keys or execution path.

New vaults are persistent simulations. Contract addresses and token transfers are
deliberately absent. Internal collateral units use the existing ledger precision.
"""
from dataclasses import dataclass
from decimal import Decimal as D
import json
from .config import ROOT

@dataclass(frozen=True)
class ChainAdapter:
    id: str
    name: str
    family: str
    chain_id: object
    collateral: str
    price: str
    rpc: str
    explorer: str
    testnet: bool = False
    enabled: bool = True

    @property
    def simulated(self): return self.id != 'dogeos'

    def public(self, config):
        assets=[dict(symbol=self.collateral,illustrativePrice=self.price,kind='native')]
        if self.family=='evm' and self.simulated:
            assets.append(dict(symbol='W'+self.collateral,illustrativePrice=self.price,
                               kind='BEP-20 demo' if self.id=='bnb' else 'ERC-20 demo'))
        return dict(id=self.id,name=self.name,family=self.family,chainId=self.chain_id,
            collateral=self.collateral,assets=assets,testnet=self.testnet,enabled=self.enabled,
            status='Simulated vaults · paper trading' if self.simulated else 'DogeOS testnet · demo lending',
            wallet='Phantom / compatible Solana' if self.family=='solana' else 'MyDoge / EVM' if self.id=='dogeos' else 'EVM browser wallet',
            network=dict(chainId=self.chain_id,chainName=self.name,
                nativeCurrency=dict(name=self.collateral,symbol=self.collateral,decimals=9 if self.family=='solana' else 18),
                rpcUrls=[self.rpc] if self.rpc else [],blockExplorerUrls=[self.explorer],testnet=self.testnet),
            capabilities=dict(walletAuthentication=self.enabled,simulatedVault=self.simulated and self.enabled,
                testnetVault=not self.simulated and bool(config.factory or config.reward_factory),demoLending=self.enabled and (self.simulated or bool(config.attestor_key and (config.factory or config.reward_factory))),
                paperTrading=self.enabled,realLending=False,realStablecoin=False,liveTrading=False,
                tokenTransfers=False if self.simulated else bool(config.factory or config.reward_factory),crossChainSettlement=False,
                genuineWalletVerified=False,settlement='simulation' if self.simulated else 'owner-confirmed DogeOS testnet'),
            oracle='Illustrative prices; explicitly refreshed, never a live collateral oracle',
            transactionHandling='Local atomic ledger; no wallet transaction or blockchain receipt' if self.simulated else 'Existing confirmed DogeOS transactions')

CHAINS={a.id:a for a in (
    ChainAdapter('dogeos','DogeOS','evm',6281971,'DOGE','.20','https://rpc.testnet.dogeos.com/','https://dogeos-testnet.l2scan.co',True),
    ChainAdapter('solana','Solana','solana','solana:mainnet-beta','SOL','150','https://api.mainnet-beta.solana.com','https://explorer.solana.com'),
    ChainAdapter('base','Base','evm',8453,'ETH','3000','https://mainnet.base.org','https://basescan.org'),
    ChainAdapter('ethereum','Ethereum','evm',1,'ETH','3000','','https://etherscan.io'),
    ChainAdapter('bnb','BNB Smart Chain','evm',56,'BNB','600','https://bsc-dataseed.bnbchain.org','https://bscscan.com'),
    ChainAdapter('arbitrum','Arbitrum','evm',42161,'ETH','3000','https://arb1.arbitrum.io/rpc','https://arbiscan.io'),
    # Official published testnet, verified in docs on 2026-10-22. No contracts deployed.
    ChainAdapter('robinhood','Robinhood Chain Testnet','evm',46630,'ETH','3000','https://rpc.testnet.chain.robinhood.com','https://explorer.testnet.chain.robinhood.com',True),
)}

def adapter(network='dogeos'):
    if not isinstance(network,str) or network not in CHAINS: raise ValueError('Unsupported network')
    result=CHAINS[network]
    if not result.enabled: raise ValueError('Network Coming Soon')
    return result

def split_owner(owner):
    if ':' not in owner: return 'dogeos',owner
    network,wallet=owner.split(':',1)
    adapter(network)
    return network,wallet

def pool_key(owner):
    network,_=split_owner(owner)
    return 'pool' if network=='dogeos' else 'pool:'+network

BASE58='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
def solana_key(value):
    if not isinstance(value,str) or not 32<=len(value)<=44: raise ValueError('Invalid Solana public key')
    try:
        number=0
        for ch in value: number=number*58+BASE58.index(ch)
        raw=b'\0'*(len(value)-len(value.lstrip('1')))+number.to_bytes((number.bit_length()+7)//8,'big')
        if len(raw)!=32 or raw==bytes(32): raise ValueError()
        return raw
    except (ValueError,OverflowError): raise ValueError('Invalid Solana public key') from None

def canonical_wallet(network,wallet):
    if adapter(network).family=='solana': solana_key(wallet);return wallet
    from .auth import address
    return address(wallet)

def subject(network,wallet):
    wallet=canonical_wallet(network,wallet)
    return wallet if network=='dogeos' else network+':'+wallet
