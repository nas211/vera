"""Signatures bind origin, network, wallet, nonce and expiry; one shared auth store."""
import secrets
from datetime import datetime,timezone
from urllib.parse import urlsplit
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from Crypto.Signature import eddsa
from vela.db import transaction,get,put
from .auth import Auth,digest,valid_nonce
from .adapters import adapter,canonical_wallet,subject,split_owner,solana_key,pool_key
from .store import account
from .risk import RISK

class MultichainAuth(Auth):
    def challenge(self,wallet,chain_id,network='dogeos'):
        chain=adapter(network)
        if chain_id!=chain.chain_id or isinstance(chain_id,bool): raise ValueError('Incorrect chain ID')
        if network=='dogeos': return super().challenge(wallet,chain_id)
        wallet=canonical_wallet(network,wallet);owner=subject(network,wallet)
        now=self.clock();nonce=secrets.token_hex(24)
        timestamp=lambda n:datetime.fromtimestamp(n,timezone.utc).isoformat().replace('+00:00','Z')
        message=(f'{urlsplit(self.config.origin).netloc} wants you to sign in with your '
            f'{"Solana" if chain.family=="solana" else "Ethereum"} account:\n{wallet if chain.family=="solana" else to_checksum_address(wallet)}\n\n'
            'VELLA: authenticate only. No transaction or spending approval. Demo funds are not redeemable.\n\n'
            f'URI: {self.config.origin}\nVersion: 1\nChain ID: {chain.chain_id}\n'
            f'Network: {network}\nNonce: {nonce}\nIssued At: {timestamp(now)}\nExpiration Time: {timestamp(now+300)}')
        if chain.family=='solana':
            # Native Ed25519 authentication. Avoid presenting a custom network
            # identifier as an EVM chain ID or a claimed SIWS implementation.
            message=(f'VELLA Solana wallet authentication\nDomain: {urlsplit(self.config.origin).netloc}\n'
                f'Wallet: {wallet}\nURI: {self.config.origin}\nNetwork: {network}\nCluster: mainnet-beta\n'
                f'Nonce: {nonce}\nIssued At: {timestamp(now)}\nExpiration Time: {timestamp(now+300)}\n'
                'Authenticate only. No transaction or spending approval. Demo funds are not redeemable.')
        with transaction(self.db):
            self.db.execute('DELETE FROM auth_nonces WHERE expires<?',(now,))
            self.db.execute('DELETE FROM auth_sessions WHERE expires<=?',(now,))
            if self.db.execute('SELECT count(*) FROM auth_nonces WHERE address=? AND used=0',(owner,)).fetchone()[0]>=10 or self.db.execute('SELECT count(*) FROM auth_nonces').fetchone()[0]>=5000:
                raise ValueError('Authentication capacity reached')
            self.db.execute('INSERT INTO auth_nonces(id,address,message,expires) VALUES (?,?,?,?)',(nonce,owner,message,now+300))
        return dict(nonce=nonce,message=message,network=network)

    def verify(self,nonce,signature,wallet,network='dogeos'):
        valid_nonce(nonce)
        if network=='dogeos': return super().verify(nonce,signature,wallet)
        chain=adapter(network);wallet=canonical_wallet(network,wallet);owner=subject(network,wallet)
        with transaction(self.db):
            row=self.db.execute('SELECT * FROM auth_nonces WHERE id=?',(nonce,)).fetchone()
            if not row or row['used'] or row['expires']<=self.clock() or row['address']!=owner: raise ValueError('Invalid or expired authentication request')
            try:
                if chain.family=='solana':
                    if not isinstance(signature,str) or len(signature)!=128: raise ValueError()
                    eddsa.new(eddsa.import_public_key(solana_key(wallet)),'rfc8032').verify(row['message'].encode(),bytes.fromhex(signature))
                elif Account.recover_message(encode_defunct(text=row['message']),signature=signature).lower()!=wallet: raise ValueError()
            except Exception: raise ValueError('Signature does not match wallet') from None
            self.db.execute('UPDATE auth_nonces SET used=1 WHERE id=?',(nonce,))
            token=secrets.token_hex(32)
            self.db.execute('INSERT INTO auth_sessions VALUES (?,?,?)',(digest(token),owner,self.clock()+86400))
            account(self.db,owner)
            if get(self.db,pool_key(owner)) is None:
                put(self.db,pool_key(owner),dict(cash=RISK['initialDemoLiquidity'],buyerCash=RISK['demoLiquidationLiquidity'],interestIncome='0',seizedDoge='0'))
        return token,owner

    def session(self,token,expected=None,network=None):
        owner=super().session(token)
        actual,wallet=split_owner(owner)
        if network is not None and actual!=adapter(network).id: raise PermissionError('Network/session mismatch')
        if expected and canonical_wallet(actual,expected)!=wallet: raise PermissionError('Wallet/session mismatch')
        return owner
