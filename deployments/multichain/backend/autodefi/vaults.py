"""Vault management delegates custody and confirmations to existing DogeOS code."""
import asyncio
from vela.db import transaction
from .chain import DogeOS
from .lending import Lending


class VaultManagement:
    def __init__(self, db, chain):
        self.db, self.chain = db, chain

    async def sync(self, owner, tx=None):
        result, receipt = await asyncio.to_thread(self.chain.read, owner, tx)
        vault = DogeOS.record(self.db, owner, result, receipt)
        lending = Lending(self.db)
        if not lending.quote(owner):
            with transaction(self.db):
                lending.quote(owner, '0')
        return vault
