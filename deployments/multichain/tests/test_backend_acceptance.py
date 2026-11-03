import asyncio
from dataclasses import replace

import httpx
import pytest
from eth_account.messages import encode_defunct

from autodefi.api import create_app
from conftest import ALICE
from test_api_ai import FixtureModel


async def authenticate(client):
    challenge = (await client.post('/api/auth/nonce', json=dict(address=ALICE.address, chainId=6281971))).json()
    signature = ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
    assert (await client.post('/api/auth/verify', json=dict(address=ALICE.address, nonce=challenge['nonce'], signature=signature))).status_code == 200


@pytest.mark.parametrize('enabled', [False, True])
def test_staking_api_is_opt_in_authenticated_and_idempotent(env, enabled):
    app = create_app(replace(env['config'], demo_staking=enabled), feed=env['feed'], model=FixtureModel())
    async def scenario():
        headers = {'Origin': env['config'].origin, 'X-ADP-Client': 'web'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=env['config'].origin, headers=headers) as client:
            assert (await client.get('/api/staking')).status_code == 401
            await authenticate(client)
            response = await client.get('/api/staking')
            assert response.status_code == (200 if enabled else 503)
            app.state.db.execute("UPDATE users SET balance='500',initial='500'")
            headers = {'Idempotency-Key': 'backend-demo-staking'}
            body = dict(confirm=True, amount='100')
            first = await client.post('/api/staking/deposit', json=body, headers=headers)
            assert first.status_code == (200 if enabled else 503)
            if enabled:
                assert first.json()['mode'] == 'DEMO' and first.json()['realFunding'] is False
                retry = await client.post('/api/staking/deposit', json=body, headers=headers)
                assert retry.json() == first.json()
                assert app.state.paper.snapshot(env['owner'])['balance'] == '400'
                conflict = await client.post('/api/staking/deposit', json=dict(confirm=True, amount='200'), headers=headers)
                assert conflict.status_code == 400
            else:
                assert app.state.paper.snapshot(env['owner'])['balance'] == '500'
    try:
        asyncio.run(scenario())
    finally:
        app.state.db.close()


def test_auth_malformed_nonce_and_huge_length_are_rejected_without_server_error(env):
    app = create_app(env['config'], feed=env['feed'], model=FixtureModel())
    async def scenario():
        headers = {'Origin': env['config'].origin, 'X-ADP-Client': 'web'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=env['config'].origin, headers=headers) as client:
            for nonce in (None, [], {}, 4, 'not-a-nonce'):
                response = await client.post('/api/auth/verify', json=dict(address=ALICE.address, signature='0'*130, nonce=nonce))
                assert response.status_code == 400
            response = await client.post('/api/auth/nonce', content=b'{}', headers={'Content-Length': '9'*5000})
            assert response.status_code == 413
    try:
        asyncio.run(scenario())
    finally:
        app.state.db.close()


def test_runtime_readiness_fails_closed_and_supervisor_recovers(env):
    app = create_app(env['config'], feed=env['feed'], model=FixtureModel())
    runtime = app.state.runtime
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=env['config'].origin) as client:
            assert (await client.get('/api/ready')).status_code == 503
            await runtime.start()
            await asyncio.sleep(.02)
            assert (await client.get('/api/ready')).status_code == 200
            runtime.errors['runtime'] = 'fixture execution failure'
            assert (await client.get('/api/ready')).status_code == 503
            runtime.errors.pop('runtime')
            env['feed'].markets['BTC/USD'].book_at = 0
            assert (await client.get('/api/ready')).status_code == 503
            await runtime.stop()
        called = 0
        recovered = asyncio.Event()
        async def failing_once():
            nonlocal called
            called += 1
            if called == 1:
                raise RuntimeError('fixture transient failure')
            recovered.set()
            await asyncio.Event().wait()
        # Exercise the supervisor independently from the stopped real loops.
        runtime.stopping = False
        task = asyncio.create_task(runtime.supervise('fixture', failing_once))
        try:
            await asyncio.wait_for(recovered.wait(), timeout=2)
            assert called == 2 and runtime.restarts['fixture'] == 1
            assert 'task:fixture' not in runtime.errors
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        app.state.db.close()
