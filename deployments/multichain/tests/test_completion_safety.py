import asyncio
import fcntl
import sqlite3
import threading
from pathlib import Path

import httpx
import pytest
from eth_account.messages import encode_defunct

from autodefi.api import create_app
from autodefi.http_security import RequestLimits
from autodefi.ownership import OwnedDatabase
from tools.backup_state import backup_database
from vela.db import get
from conftest import ALICE
from test_api_ai import FixtureModel
from decimal import Decimal as D
from conftest import funded, fill
from autodefi.trading import GovernedPaper
from vela.db import transaction


async def ticking(fn):
    # A periodic wakeup allows real worker threads to finish in the restricted
    # test executor whose selector wakeup sockets are disabled.
    async def heartbeat():
        while True:
            await asyncio.sleep(.005)
    beat = asyncio.create_task(heartbeat())
    try:
        return await fn()
    finally:
        await asyncio.get_running_loop().shutdown_default_executor()
        beat.cancel()


def test_second_engine_fails_before_any_database_initialization(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    first = OwnedDatabase(path, sqlite3.connect)
    first.execute('CREATE TABLE ledger(value TEXT)')
    first.execute("INSERT INTO ledger VALUES ('preserve')")
    first.commit()
    called = []
    def opener(path):
        called.append(path)
        return sqlite3.connect(path)
    with pytest.raises(BlockingIOError):
        OwnedDatabase(path, opener)
    assert called == []
    assert first.execute('SELECT value FROM ledger').fetchone() == ('preserve',)
    first.close()
    second = OwnedDatabase(path, opener)
    assert len(called) == 1
    second.close()


def test_failed_database_initialization_releases_ownership(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    def fail(_):
        raise ValueError('fixture initialization failure')
    with pytest.raises(ValueError):
        OwnedDatabase(path, fail)
    db = OwnedDatabase(path, sqlite3.connect)
    db.close()


@pytest.mark.parametrize('interrupt', ['stop', 'logout', 'expiry', 'permission'])
def test_pending_model_preflight_cannot_restore_revoked_consent(env, interrupt):
    app = create_app(env['config'], feed=env['feed'], model=FixtureModel())
    async def scenario():
        headers = {'Origin': env['config'].origin, 'X-ADP-Client': 'web'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=env['config'].origin, headers=headers) as client:
            challenge = (await client.post('/api/auth/nonce', json={'address': ALICE.address, 'chainId': 6281971})).json()
            signature = ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
            assert (await client.post('/api/auth/verify', json=dict(address=ALICE.address, nonce=challenge['nonce'], signature=signature))).status_code == 200
            paper = app.state.paper
            # Direct paper capital, no loan or chain transaction.
            owner = env['owner']
            app.state.db.execute('DELETE FROM vaults')
            app.state.db.execute("UPDATE users SET balance='500',initial='500'")
            paper.configure(owner, dict(confirm=True, allocation='500', positionSize='100', maxLoss='1', leverage='1'))
            entered, release = asyncio.Event(), asyncio.Event()
            async def preflight(*args):
                entered.set()
                await release.wait()
                return {'verdict': 'delay'}
            app.state.runtime.run_blocking = preflight
            start = asyncio.create_task(client.post('/api/trading/start', json={'confirm':True}))
            await entered.wait()
            duplicate = await client.post('/api/trading/start', json={'confirm':True})
            assert duplicate.status_code == 409
            if interrupt in ('stop', 'logout'):
                route = '/api/trading/stop' if interrupt == 'stop' else '/api/logout'
                assert (await client.post(route, json={})).status_code == 200
            elif interrupt == 'expiry':
                app.state.db.execute('UPDATE auth_sessions SET expires=0')
            else:
                paper.configure(owner, dict(confirm=True, allocation='400', positionSize='100', maxLoss='.5', leverage='1'))
            release.set()
            result = await start
            assert result.status_code in (400, 401)
            assert not get(app.state.db, 'paper-prefs:' + owner)['autonomous']
            assert not app.state.runtime.starting
    try:
        asyncio.run(ticking(scenario))
    finally:
        app.state.db.close()


def test_shutdown_drains_model_thread_before_ledger_can_be_reowned(env):
    app = create_app(env['config'], feed=env['feed'], model=FixtureModel())
    runtime = app.state.runtime
    entered, release = threading.Event(), threading.Event()
    def worker():
        entered.set()
        assert release.wait(3)
    async def scenario():
        work = asyncio.create_task(runtime.run_blocking(worker))
        while not entered.is_set():
            await asyncio.sleep(.005)
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        stop = asyncio.create_task(runtime.stop())
        await asyncio.sleep(.01)
        assert not stop.done()
        with pytest.raises(BlockingIOError):
            OwnedDatabase(env['config'].db_path, sqlite3.connect)
        release.set()
        await stop
        assert not runtime.inflight
    try:
        asyncio.run(ticking(scenario))
    finally:
        release.set()
        app.state.db.close()
    restarted = OwnedDatabase(env['config'].db_path, sqlite3.connect)
    restarted.close()


def test_model_capacity_bounds_threads_and_remains_reserved_after_cancellation(env):
    app = create_app(env['config'], feed=env['feed'], model=FixtureModel())
    runtime = app.state.runtime
    release = threading.Event()
    def worker():
        assert release.wait(3)
    async def scenario():
        tasks = [asyncio.create_task(runtime.run_blocking(worker)) for _ in range(2)]
        await asyncio.sleep(.02)
        with pytest.raises(RuntimeError, match='capacity'):
            await runtime.run_blocking(worker)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert len(runtime.inflight) == 2
        with pytest.raises(RuntimeError, match='capacity'):
            await runtime.run_blocking(worker)
        release.set()
        await runtime.stop()
        assert not runtime.inflight
    try:
        asyncio.run(ticking(scenario))
    finally:
        release.set()
        app.state.db.close()


def test_actual_body_limit_and_auth_rate_limit_without_content_length():
    called = []
    async def endpoint(scope, receive, send):
        called.append(await receive())
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'{}'})
    middleware = RequestLimits(endpoint, maximum=24, auth_limit=2)
    async def request(chunks, path='/api/auth/nonce'):
        messages = []
        iterator = iter(chunks)
        async def receive():
            return next(iterator)
        async def send(message):
            messages.append(message)
        await middleware(dict(type='http', method='POST', path=path, client=('fixture-peer', 1)), receive, send)
        return messages[0]['status']
    async def scenario():
        assert await request([dict(type='http.request', body=b'x'*20, more_body=True), dict(type='http.request', body=b'x'*20)], '/api/trading/allocate') == 413
        assert called == []
        for body in (b'[]', b'{"amount":NaN}', b'{'):
            assert await request([dict(type='http.request', body=body)], '/api/repay') == 400
        for _ in range(2):
            assert await request([dict(type='http.request', body=b'{}')]) == 200
        assert await request([dict(type='http.request', body=b'{}')]) == 429
        assert len(called) == 2
    asyncio.run(scenario())


def test_wal_backup_restore_and_existing_destination_refusal(tmp_path):
    source = tmp_path / 'live.sqlite3'
    db = sqlite3.connect(source)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE ledger(value TEXT)')
    db.execute("INSERT INTO ledger VALUES ('committed only in WAL')")
    db.commit()
    folder = tmp_path / 'private'
    folder.mkdir(mode=0o700)
    target = folder / 'backup.sqlite3'
    result = backup_database(source, target)
    assert result['restoreVerified'] and result['integrity'] == 'ok'
    with sqlite3.connect(target) as copy:
        assert copy.execute('SELECT value FROM ledger').fetchone()[0] == 'committed only in WAL'
    assert target.stat().st_mode & 0o077 == 0
    before = target.read_bytes()
    with pytest.raises(ValueError, match='exists'):
        backup_database(source, target)
    assert target.read_bytes() == before
    assert db.execute('SELECT count(*) FROM ledger').fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize('side', ['long', 'short'])
@pytest.mark.parametrize('partial', [False, True])
def test_tighter_permission_reconciles_existing_positions_after_restart(env, side, partial):
    funded(env)
    position=fill(env, side)
    paper,db=env['paper'],env['db']
    if partial:
        with transaction(db):paper.close(position['id'],'fixture partial',D('.5'))
    with transaction(db):
        paper.configure(env['owner'],dict(confirm=True,allocation='800',positionSize='100',maxLoss='.2',leverage='1'))
    restarted=GovernedPaper(db,env['config'].paper(),env['feed'])
    restarted.tick()
    current=restarted.getPosition(env['owner'])[0]
    stop=D(current['stop']);entry=D(current['entry']);qty=D(current['qty']);sign=1 if side=='long' else -1
    loss=(entry-stop)*qty*sign+D(current['paid_fees'])-D(current['realized_gross'])+stop*qty*D('.0004')
    assert loss<=D('.2')
    assert stop%env['feed'].markets['BTC/USD'].tick==0
    assert D(restarted.meta(current)['riskBudget'])==D('.2')
    # Both permission renewal and a later engine instance preserve the limit.
    with transaction(db):
        restarted.configure(env['owner'],dict(confirm=True,allocation='800',positionSize='100',maxLoss='2',leverage='1'))
    again=GovernedPaper(db,env['config'].paper(),env['feed']);again.tick()
    final=again.getPosition(env['owner'])[0]
    assert (D(final['stop'])-stop)*sign>=0
    assert D(again.meta(final)['riskBudget'])==D('.2')
