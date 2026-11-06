"""Single-purpose TEST DOGE reserve relay; never opens trades or spends collateral."""
import fcntl
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound
from vela.db import get, put, transaction
from .config import ROOT, CHAIN_ID
from .adapters import split_owner
from .doge_lending import configuration, DogeLendingReader
from .demo_doge_repayment import POLICY, JOB, HEALTH, MIN_PROFIT, settings, eligible, jobs
from .store import cashflow
from decimal import Decimal

GAS_RESERVE = 10**17  # 0.1 test DOGE remains available for transaction fees.
MAX_GAS_FEE = 2 * 10**16


class Relay:
    def __init__(self, db, w3, signer, clock=time.time):
        self.db, self.w3, self.signer, self.clock = db, w3, signer, clock
        self.market_config = configuration()
        self.market = w3.eth.contract(address=Web3.to_checksum_address(self.market_config['market']),
            abi=json.loads((ROOT/'config/TestDogeLending.abi.json').read_text()))

    def health(self, ready, reason, balance=0):
        with transaction(self.db):
            put(self.db, HEALTH, dict(at=self.clock(), ready=ready, reason=reason,
                                     reserveWei=str(max(0, balance-GAS_RESERVE))))

    def reconcile(self, job):
        try:
            receipt = self.w3.eth.get_transaction_receipt(job['hash'])
        except TransactionNotFound:
            # The exact signed transaction is durable before any broadcast.
            # Unknown outcomes only resend these bytes, never a fresh nonce/payment.
            try:
                self.w3.eth.send_raw_transaction(bytes.fromhex(job['raw'].removeprefix('0x')))
            except Exception:
                pass
            return
        if self.w3.eth.block_number < receipt.blockNumber + 2:
            return
        if self.w3.eth.get_block(receipt.blockNumber).hash != receipt.blockHash:
            raise ValueError('Repayment receipt reorganized')
        tx = self.w3.eth.get_transaction(job['hash'])
        if tx['from'].lower() != self.signer.address.lower() or tx['to'].lower() != self.market.address.lower() or int(tx['value']) != int(job['valueWei']) or int(tx['nonce']) != job['nonce']:
            raise ValueError('Repayment transaction identity differs')
        paid = 0
        if receipt.status == 1:
            events = [e for e in self.market.events.Repaid().process_receipt(receipt)
                      if e['address'].lower() == self.market.address.lower()]
            if len(events) != 1:
                raise ValueError('Missing unique repayment event')
            args = events[0]['args']
            if args['account'].lower() != job['wallet'] or args['payer'].lower() != self.signer.address.lower() or int(args['source']) != 2:
                raise ValueError('Repayment recipient differs')
            paid = int(args['interest']) + int(args['principal'])
            if not 0 < paid <= int(job['valueWei']):
                raise ValueError('Repayment amount differs')
        with transaction(self.db):
            current = get(self.db, JOB + job['id'])
            if current['status'] not in ('prepared', 'sent'):
                return
            current.update(status='confirmed' if receipt.status == 1 else 'reverted',
                           paidWei=str(paid), block=receipt.blockNumber,
                           blockHash=receipt.blockHash.to_0x_hex(), confirmedAt=self.clock())
            # Return any excess/refunded DOGE equivalent to the simulated cash
            # reserved before broadcast; never charge a reverted repayment.
            refund = int(job['valueWei']) - paid
            if refund:
                cashflow(self.db, job['owner'], Decimal(refund)/Decimal(10**18))
            put(self.db, JOB + job['id'], current)

    def tick(self):
        d = settings()
        if not d.get('enabled') or d['reserveAddress'].lower() != self.signer.address.lower() or self.w3.eth.chain_id != CHAIN_ID:
            raise ValueError('Testnet repayment identity mismatch')
        head = self.w3.eth.get_block('latest')
        if not 0 <= self.clock()-head.timestamp <= 90:
            raise ValueError('Stale DogeOS chain')
        pending = [j for j in jobs(self.db) if j['status'] in ('prepared', 'sent')]
        if len(pending) > 1:
            raise ValueError('Conflicting reserve transactions')
        if pending:
            self.reconcile(pending[0])
            self.health(True, 'Repayment confirming on DogeOS.')
            return
        balance = self.w3.eth.get_balance(self.signer.address)
        if balance <= GAS_RESERVE + MIN_PROFIT:
            self.health(False, 'Project test-DOGE repayment reserve needs funding.', balance)
            return
        self.health(True, 'Ready. Net closed-trade profit converts at 1 demo USDC = 1 test DOGE.', balance)
        policies = [(r[0][len(POLICY):], json.loads(r[1])) for r in self.db.execute('SELECT key,value FROM kv WHERE key LIKE ?', (POLICY+'%',))]
        for owner, policy in policies:
            if not policy.get('enabled') or self.clock() >= policy['expiresAt']:
                continue
            network, wallet = split_owner(owner)
            if network != 'dogeos':
                continue
            # First avoid RPC work for accounts without new closed profit.
            if eligible(self.db, owner, policy, 10**30, balance-GAS_RESERVE, jobs(self.db), self.clock()) <= 0:
                continue
            snapshot = DogeLendingReader(self.w3, self.market_config).snapshot(wallet)
            debt = int(snapshot['debtWei'])
            if debt == 0:
                with transaction(self.db):
                    current = get(self.db, POLICY+owner)
                    if current['id'] == policy['id']:
                        put(self.db, POLICY+owner, dict(current, enabled=False))
                continue
            amount = eligible(self.db, owner, policy, debt, balance-GAS_RESERVE, jobs(self.db), self.clock())
            if amount <= 0:
                continue
            nonce = self.w3.eth.get_transaction_count(self.signer.address, 'pending')
            if nonce != self.w3.eth.get_transaction_count(self.signer.address, 'latest'):
                raise ValueError('Reserve wallet has an untracked pending transaction')
            call = self.market.functions.repayFor(Web3.to_checksum_address(wallet))
            # Interest may turn zero storage slots nonzero in the next block.
            # Include that storage-write margin, within the fixed fee ceiling.
            gas = (call.estimate_gas({'from': self.signer.address, 'value': amount}) * 150 + 99)//100 + 50000
            price = self.w3.eth.gas_price
            if gas > 500000 or gas*price > MAX_GAS_FEE:
                raise ValueError('Repayment network fee exceeds testnet limit')
            tx = call.build_transaction(dict(chainId=CHAIN_ID, nonce=nonce, gas=gas, gasPrice=price, value=amount, **{'from': self.signer.address}))
            signed = self.signer.sign_transaction(tx)
            identifier = signed.hash.to_0x_hex()
            job = dict(id=identifier, hash=identifier, raw=signed.raw_transaction.hex(),
                       owner=owner, wallet=wallet, policyId=policy['id'], nonce=nonce,
                       valueWei=str(amount), status='prepared', createdAt=self.clock())
            with transaction(self.db):
                current = get(self.db, POLICY+owner)
                if current != policy or any(j['status'] in ('prepared', 'sent') for j in jobs(self.db)):
                    return
                if eligible(self.db, owner, current, debt, balance-GAS_RESERVE, jobs(self.db), self.clock()) < amount:
                    return
                cashflow(self.db, owner, -Decimal(amount)/Decimal(10**18))
                put(self.db, JOB+identifier, job)
            # A crash here is recovered by reconcile() resending this exact hash.
            try:
                self.w3.eth.send_raw_transaction(signed.raw_transaction)
            finally:
                with transaction(self.db):
                    saved = get(self.db, JOB+identifier)
                    put(self.db, JOB+identifier, dict(saved, status='sent'))
            return


def main():
    directory = ROOT/'.runtime/demo-doge-repayment'
    key = directory/'reserve.key'
    info = key.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ValueError('Private reserve key permissions differ')
    lock = open(directory/'relay.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    signer = Account.from_key(key.read_text().strip())
    database = ROOT/'.runtime/lighter-showcase/multichain-candidate.sqlite3'
    db = sqlite3.connect(database.as_uri()+'?mode=rw', uri=True, timeout=3, isolation_level=None)
    db.row_factory = sqlite3.Row
    relay = Relay(db, Web3(Web3.HTTPProvider('https://rpc.testnet.dogeos.com/', request_kwargs={'timeout': 8})), signer)
    while True:
        try:
            relay.tick()
        except Exception as error:
            # No keys, signed transaction bytes, RPC response bodies or account data.
            relay.health(False, 'Repayment temporarily unavailable ('+type(error).__name__+').')
        time.sleep(5)


if __name__ == '__main__':
    main()
