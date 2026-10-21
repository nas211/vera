"""Restart guards use isolated files; no service manager or live ledger access."""
import fcntl
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.check_integrated_restart import database_gate, proxy_configured
from test_integrated import connected, run
from test_lighter import desk


def test_restart_gate_refuses_owned_ledger_without_modifying_it(tmp_path):
    ledger = tmp_path / 'paper.sqlite3'
    ledger.write_bytes(b'unchanged fixture ledger')
    lock = ledger.with_suffix('.lock')
    with lock.open('w') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert 'active engine owner' in database_gate(ledger, tmp_path)
        assert ledger.read_bytes() == b'unchanged fixture ledger'
    assert database_gate(ledger, tmp_path) is None


def test_restart_gate_refuses_implicit_or_empty_ledger(tmp_path):
    assert 'explicitly select' in database_gate(None, tmp_path)
    assert 'new empty balance store' in database_gate(tmp_path / 'missing.sqlite3', tmp_path)
    assert not list(tmp_path.iterdir())


def test_proxy_file_requires_private_permissions_and_valid_token(tmp_path):
    path = tmp_path / 'proxy.env'
    path.write_text('VELA_SITE_PROXY_TOKEN=' + 'fixture_' * 8 + '\n')
    path.chmod(0o600)
    assert proxy_configured(path)
    path.chmod(0o644)
    assert not proxy_configured(path)
    path.chmod(0o600)
    path.write_text('VELA_SITE_PROXY_TOKEN=short\n')
    assert not proxy_configured(path)


def test_integrated_restart_cannot_take_an_existing_engine_ledger(setup_db, desk):
    async def scenario():
        async with connected(setup_db[1], desk) as (_, client, runtime):
            before = runtime.db.execute('SELECT address,balance FROM users ORDER BY address').fetchall()
            with pytest.raises(RuntimeError, match='already owns this database'):
                async with connected(setup_db[1], desk):
                    pytest.fail('A second engine acquired the same ledger')
            assert (await client.get('/api/paper/health')).status_code == 200
            assert runtime.db.execute('SELECT address,balance FROM users ORDER BY address').fetchall() == before
    run(scenario)
