import hashlib,secrets,time,re
from datetime import datetime,timezone
from urllib.parse import urlsplit
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from vela.db import transaction
from .config import CHAIN_ID
from .store import account

def address(value):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',value) or int(value,16)==0: raise ValueError('Invalid wallet address')
    return value.lower()
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def valid_nonce(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{48}',value):
        raise ValueError('Invalid authentication request')
class Auth:
    def __init__(self,db,config,clock=None): self.db,self.config,self.clock=db,config,clock or time.time
    def challenge(self,wallet,chain_id):
        wallet=address(wallet)
        if chain_id!=CHAIN_ID: raise ValueError('Switch wallet to DogeOS Chikyū Testnet')
        now=self.clock(); nonce=secrets.token_hex(24)
        with transaction(self.db):
            self.db.execute('DELETE FROM auth_nonces WHERE expires<?',(now,))
            self.db.execute('DELETE FROM auth_sessions WHERE expires<=?',(now,))
            if self.db.execute('SELECT count(*) FROM auth_nonces WHERE address=? AND used=0',(wallet,)).fetchone()[0]>=10: raise ValueError('Too many outstanding wallet requests')
            if self.db.execute('SELECT count(*) FROM auth_nonces').fetchone()[0]>=5000: raise ValueError('Authentication capacity reached')
            issued=datetime.fromtimestamp(now,timezone.utc).isoformat().replace('+00:00','Z')
            expires=datetime.fromtimestamp(now+300,timezone.utc).isoformat().replace('+00:00','Z')
            message=f'{urlsplit(self.config.origin).netloc} wants you to sign in with your Ethereum account:\n{to_checksum_address(wallet)}\n\nVELLA: authenticate only. No transaction or spending approval.\n\nURI: {self.config.origin}\nVersion: 1\nChain ID: {CHAIN_ID}\nNonce: {nonce}\nIssued At: {issued}\nExpiration Time: {expires}'
            self.db.execute('INSERT INTO auth_nonces(id,address,message,expires) VALUES (?,?,?,?)',(nonce,wallet,message,now+300))
        return dict(nonce=nonce,message=message)
    def verify(self,nonce,signature,wallet):
        wallet=address(wallet)
        valid_nonce(nonce)
        if not isinstance(signature,str) or not re.fullmatch(r'(?:0x)?[a-fA-F0-9]{128,130}',signature):
            raise ValueError('Invalid wallet signature')
        with transaction(self.db):
            row=self.db.execute('SELECT * FROM auth_nonces WHERE id=?',(nonce,)).fetchone()
            if not row or row['used'] or row['expires']<=self.clock() or row['address']!=wallet: raise ValueError('Invalid or expired authentication request')
            try: recovered=Account.recover_message(encode_defunct(text=row['message']),signature=signature).lower()
            except Exception: raise ValueError('Invalid wallet signature') from None
            if recovered!=wallet: raise ValueError('Signature does not match wallet')
            self.db.execute('UPDATE auth_nonces SET used=1 WHERE id=?',(nonce,))
            token=secrets.token_hex(32)
            self.db.execute('INSERT INTO auth_sessions VALUES (?,?,?)',(digest(token),wallet,self.clock()+86400))
            account(self.db,wallet)
        return token,wallet
    def session(self,token,expected=None):
        if not token or not re.fullmatch('[a-f0-9]{64}',token): raise PermissionError('Authentication required')
        row=self.db.execute('SELECT * FROM auth_sessions WHERE hash=?',(digest(token),)).fetchone()
        if not row or row['expires']<=self.clock(): raise PermissionError('Session expired')
        if expected and address(expected)!=row['address']: raise PermissionError('Wallet/session mismatch')
        return row['address']
    def logout(self,token): self.db.execute('DELETE FROM auth_sessions WHERE hash=?',(digest(token or ''),))
