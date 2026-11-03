"""AI decisions must survive usage metering through another WAL connection."""
import pytest

from autodefi.model_usage import UsageLedger
from autodefi.store import account
from autodefi.trading import agents_for
from conftest import BOB, funded
from test_api_ai import FixtureModel
from test_model_usage import payload
from vela.db import get, put


@pytest.mark.parametrize('provider_fails', [False, True])
def test_multiple_accounts_release_read_snapshot_before_model_call(env, provider_fails):
    funded(env)
    db = env['db']
    # A second active account leaves the users cursor unfinished in the old loop.
    account(db, BOB.address.lower())
    for market in env['feed'].markets.values():
        db.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',
            [(market.symbol, c['ts'], c['o'], c['h'], c['l'], c['c']) for c in market.candles])
    ledger = UsageLedger(env['config'].db_path)

    class MeteredFixture(FixtureModel):
        def ask(self, role, evidence, schema):
            identifier = ledger.reserve(payload())
            if provider_fails:
                ledger.finish(identifier, status='request_failed_usage_unknown')
                raise RuntimeError('AI_REQUEST_FAILED')
            ledger.finish(identifier, {'input_tokens': 10, 'output_tokens': 20})
            return super().ask(role, evidence, schema)

    model = MeteredFixture()
    agents = agents_for(db, model, env['paper'])
    for _ in range(1 if provider_fails else 3):
        put(db, 'ai-last-call', 0)
        agents.tick_ai()
    if provider_fails:
        assert get(db, 'ai-connection')['ok'] is False
        assert get(db, 'ai-approved:' + env['owner']) is None
        assert get(db, 'ai-stage:' + env['owner']) == {}
        assert db.execute("SELECT COUNT(*) FROM logs WHERE message='proposal_rejected_or_unavailable'").fetchone()[0] == 1
    else:
        assert model.calls == ['research', 'review', 'direction']
        assert get(db, 'ai-connection')['ok'] is True
        assert get(db, 'ai-approved:' + env['owner'])['spec']['market'] == 'BTC/USD'
        assert get(db, 'ai-decision:' + env['owner'])['phase'] == 'direction'
    assert ledger.summary()['calls'] == (1 if provider_fails else 3)
