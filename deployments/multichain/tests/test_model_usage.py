import json
from contextlib import closing
from io import BytesIO
from urllib.request import Request

import pytest
from autodefi.model_usage import UsageLedger, MeteredOpener
from vela.db import get


def payload():
    return json.dumps({'model': 'fixture', 'input': 'private prompt never persisted', 'max_output_tokens': 3000}).encode()


def test_model_budget_survives_restart_and_unknown_usage(env):
    first = UsageLedger(env['config'].db_path, calls=2)
    one = first.reserve(payload())
    first.finish(one, {'input_tokens': 10, 'output_tokens': 20})
    second = UsageLedger(env['config'].db_path, calls=2)
    two = second.reserve(payload())
    second.finish(two, status='request_failed_usage_unknown')
    with pytest.raises(RuntimeError, match='PERSISTENT_BUDGET'):
        UsageLedger(env['config'].db_path, calls=2).reserve(payload())
    with closing(second.connect()) as db:
        record = get(db, one)
        totals = get(db, record['day'])
        assert totals['calls'] == 2 and totals['unknownUsage'] == 1
        assert totals['inputTokens'] == 10 and totals['outputTokens'] == 20
        assert record['estimatedCostUsd'] is None
        assert 'private prompt' not in str(db.execute("SELECT value FROM kv WHERE key LIKE 'model-%'").fetchall())


def test_usage_records_actual_tokens_and_only_explicit_cost_rates(env):
    ledger = UsageLedger(env['config'].db_path, rates=('1', '2'))
    one = ledger.reserve(payload())
    ledger.finish(one, {'prompt_tokens': 100, 'completion_tokens': 50})
    ledger.finish(one, {'prompt_tokens': 9999, 'completion_tokens': 9999})
    with closing(ledger.connect()) as db:
        record = get(db, one)
        assert record['estimatedCostUsd'] == '0.0002'
        assert get(db, record['day'])['budgetTokens'] == 150


def test_token_budget_fails_closed_before_network(env):
    class Opener:
        def open(self, *args, **kwargs):
            pytest.fail('Network request must not start')
    opener = MeteredOpener(Opener(), UsageLedger(env['config'].db_path, tokens=100))
    with pytest.raises(RuntimeError, match='PERSISTENT_BUDGET'):
        opener.open(Request('https://fixture.invalid/responses', data=payload()), timeout=1)


def test_failed_provider_call_reservation_is_not_refunded(env):
    class Opener:
        def open(self, *args, **kwargs):
            raise TimeoutError('fixture timeout')
    ledger = UsageLedger(env['config'].db_path, calls=1)
    opener = MeteredOpener(Opener(), ledger)
    with pytest.raises(TimeoutError):
        opener.open(Request('https://fixture.invalid/responses', data=payload()))
    with pytest.raises(RuntimeError, match='PERSISTENT_BUDGET'):
        ledger.reserve(payload())


def test_recording_opener_preserves_response_and_measures_usage(env):
    body = json.dumps({'output': [], 'usage': {'input_tokens': 17, 'output_tokens': 11}}).encode()
    class Opener:
        def open(self, *args, **kwargs):
            return BytesIO(body)
    ledger = UsageLedger(env['config'].db_path)
    with MeteredOpener(Opener(), ledger).open(Request('https://fixture.invalid/responses', data=payload())) as response:
        assert response.read(262145) == body
    with closing(ledger.connect()) as db:
        value = json.loads(db.execute("SELECT value FROM kv WHERE key LIKE 'model-budget:%'").fetchone()[0])
        assert value['inputTokens'] == 17 and value['outputTokens'] == 11
        assert value['unknownUsage'] == 0
