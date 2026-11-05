"""Vault management delegates custody and confirmations to existing DogeOS code."""
import asyncio
from vela.db import transaction
from .chain import DogeOS
from .lending import Lending
from .vault_migration import context


class VaultManagement:
    def __init__(self, db, chain):
        self.db, self.chain = db, chain

    async def sync(self, owner, tx=None, include_state=False):
        if include_state:
            observed = await asyncio.to_thread(self.chain.state, owner, tx, **context(self.db, owner))
            result, receipt = observed['vault'], observed['transaction']
            if tx and (not receipt or receipt['status'] != 'CONFIRMED'):
                raise ValueError('Confirmed successful owner transaction required')
            # A new owner can inspect/create a vault without manufacturing a
            # custody record. Existing owners are synchronized from this same read.
            if not result:
                if tx: raise ValueError('Confirmed owner vault required')
                return observed
        else:
            result, receipt = await asyncio.to_thread(self.chain.read, owner, tx, **context(self.db, owner))
        vault = DogeOS.record(self.db, owner, result, receipt)
        lending = Lending(self.db)
        if not lending.quote(owner):
            with transaction(self.db):
                lending.quote(owner, '0')
        return observed if include_state else vault
