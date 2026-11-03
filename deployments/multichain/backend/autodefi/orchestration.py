"""Agent Mode composes the shared services; it owns no financial ledger."""
from vela.db import get
from .lending import Lending


class AgentOrchestrator:
    def __init__(self, db, paper, repayment):
        self.db, self.paper, self.repayment = db, paper, repayment

    def status(self, owner):
        vault = self.db.execute('SELECT address FROM vaults WHERE owner=?', (owner,)).fetchone()
        lending = Lending(self.db)
        loan = lending.loan(owner)
        return dict(vault=vault[0] if vault else None, loan=loan,
                    trading=get(self.db, 'paper-prefs:' + owner, {}).get('autonomous', False),
                    repayment=self.repayment.status(owner),
                    liquidationEligible=bool(vault and lending.quote(owner) and
                        lending.health(owner)['healthFactor'] is not None and
                        float(lending.health(owner)['healthFactor']) < 1))

    def settle(self, owner, confirmed):
        self.repayment.disable(owner, 'Stop & Settle supersedes automated repayment')
        self.paper.settle(owner, confirmed)
        return dict(status='CLOSING', simulated=True,
                    disclosure='Paper positions will close; simulated debt remains until repaid')
