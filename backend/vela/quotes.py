"""Wallet-bound EIP-712 entry prices. Creating a quote never sends a transaction."""
import time
from eth_account.messages import encode_typed_data
from web3 import Web3
from .prices import quote, entries_allowed
from .db import packed, enqueue

TYPES = {"StartQuote":[{"name":name,"type":kind} for name,kind in (
    ("user","address"),("strategyId","uint256"),("dogeIn","uint256"),
    ("price","uint256"),("issuedAt","uint256"),("deadline","uint256"),("nonce","uint256"))]}


def sign_quote(account, chain_id, vault, user, strategy, value, price, issued_at, nonce):
    message = dict(user=Web3.to_checksum_address(user),strategyId=strategy,dogeIn=value,
                   price=price,issuedAt=issued_at,deadline=issued_at+120,nonce=nonce)
    domain = dict(name="VelaVault",version="2",chainId=chain_id,verifyingContract=vault)
    encoded = encode_typed_data(domain_data=domain,message_types=TYPES,message_data=message)
    signature = Web3.to_hex(account.sign_message(encoded).signature)
    return dict(user=message["user"],**{k:str(v) for k,v in message.items() if k!="user"},
                vault=vault,chainId=chain_id,signature=signature)


def process_requests(chain, model_ready):
    db, s = chain.db, chain.settings
    now=time.time()
    db.execute("UPDATE start_quotes SET status='expired',error='Quote request expired' "
               "WHERE status='pending' AND created<?",(now-90,))
    db.execute("DELETE FROM start_quotes WHERE created<?",(now-3600,))
    if not s.chain_writes or not model_ready or not chain.account:
        return
    for row in db.execute("SELECT * FROM start_quotes WHERE status='pending' ORDER BY created LIMIT 5").fetchall():
        try:
            if row["strategy"] != 1 or not entries_allowed(db,"DOGE/USD",s.max_price_age):
                raise ValueError("Strategy or price unavailable")
            user=Web3.to_checksum_address(row["user"])
            session=chain.vault.functions.sessions(user).call()
            if session[0]!=0:
                raise ValueError("Wallet already has a session")
            if chain.vault.functions.paused().call():
                raise ValueError("Vault is paused")
            if chain.w3.eth.get_balance(user) <= int(row["value_wei"]):
                raise ValueError("Wallet needs deposit and network fees")
            if s.config["deployment"].get("version",1)<2:
                # The deployed V1 needs one price write before the first visitor
                # can start after inactivity. Never refresh on an idle timer.
                updated=chain.vault.functions.priceUpdatedAt().call()
                block_time=chain.w3.eth.get_block("latest")["timestamp"]
                if block_time-updated>60 or updated==0:
                    if not db.execute("SELECT 1 FROM outbox WHERE kind='updatePrice' AND status IN ('queued','signed','sent')").fetchone():
                        enqueue(db,"entry-price:"+str(int(now//30)),"updatePrice",{})
                    continue
                result=dict(user=user,vault=chain.vault.address,chainId=s.chain_id,
                    strategyId=str(row["strategy"]),dogeIn=row["value_wei"],
                    price=str(chain.vault.functions.dogeUsd().call()),issuedAt=str(updated),
                    deadline=str(updated+120),version=1)
                db.execute("UPDATE start_quotes SET status='ready',result=? WHERE id=?",(packed(result),row["id"]))
                continue
            price=int(quote(db,"DOGE/USD",s.max_price_age)*100_000_000)
            issued=int(db.execute("SELECT ts FROM prices WHERE symbol='DOGE/USD'").fetchone()[0])
            issued=min(issued,chain.w3.eth.get_block("latest")["timestamp"])
            result=sign_quote(chain.account,s.chain_id,chain.vault.address,user,row["strategy"],
                              int(row["value_wei"]),price,issued,session[5]+1)
            db.execute("UPDATE start_quotes SET status='ready',result=? WHERE id=?",(packed(result),row["id"]))
        except Exception:
            db.execute("UPDATE start_quotes SET status='failed',error='Fresh entry quote unavailable; retry shortly' WHERE id=?",(row["id"],))
