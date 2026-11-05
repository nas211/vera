"""Judge launch routes and actual paper API. Market data is an explicit test fixture."""
import asyncio
import re
from decimal import Decimal

import httpx
import pytest

from tools.run_judge_demo import judge_app


@pytest.mark.parametrize('side', ['long', 'short'])
def test_judge_routes_and_paper_roundtrip(env, side, monkeypatch):
    monkeypatch.setenv('VELA_MODEL_KEY', 'fixture-not-a-provider-key')
    monkeypatch.setenv('VELA_MODEL', 'fixture-must-not-be-inherited')
    app = judge_app(18802, offline=True, db_path=env['config'].db_path, feed=env['feed'])
    backend = app.state.backend

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                base_url='http://127.0.0.1:18802', headers={'Origin':'http://127.0.0.1:18802',
                'X-ADP-Client':'web','X-Vela-Paper':'d'*64}) as client:
                assert (await client.get('/')).status_code == 200
                for route in ('/app/', '/preview/', '/paper'):
                    response = await client.get(route)
                    assert response.status_code == 200
                    assets = re.findall(r'(?:src|href)="([^\"]+\.(?:js|css))"', response.text)
                    assert assets
                    for asset in assets:
                        from urllib.parse import urljoin
                        assert (await client.get(urljoin(route, asset))).status_code == 200, asset
                config = (await client.get('/api/config')).json()
                assert not config['modelConfigured'] and not config['nativeVaultActionsEnabled']
                assert not config['liveEnabled'] and not config['networkEnabled']
                assert not config['attestorConfigured'] and config['factory'] is None
                assert (await client.get('/api/account')).status_code == 401
                first = await client.post('/api/paper/session', json={})
                assert first.status_code == 200
                body = dict(clientId='judge-entry-'+side, symbol='BTC/USD', side=side,
                            type='market', notional='20', leverage='1', stopBps='50', targetBps='100', riskBudget='1')
                assert (await client.post('/api/paper/orders',json=body)).status_code == 202
                backend.state.db.execute('UPDATE paper_orders SET due=0')
                backend.state.paper.tick()
                snapshot = (await client.get('/api/paper/account')).json()
                position = snapshot['positions'][0]
                assert Decimal(position['riskBudget']) <= 1
                for index, fraction in enumerate(('.5','1')):
                    response = await client.post('/api/paper/positions/'+position['id']+'/close',
                        json=dict(clientId='judge-close-'+str(index), fraction=fraction))
                    assert response.status_code == 202
                    backend.state.db.execute('UPDATE paper_orders SET due=0')
                    backend.state.paper.tick()
                final = (await client.get('/api/paper/account')).json()
                assert not final['positions'] and not final['preferences']['autonomous']
                assert len(final['trades']) == 2
                assert Decimal(final['balance']) == Decimal(first.json()['balance'])+sum(Decimal(t['pnl']) for t in final['trades'])
    # Keep the sandbox selector awake during FileResponse's thread callbacks.
    async def heartbeat():
        while True:
            await asyncio.sleep(.01)
    loop = asyncio.new_event_loop()
    pulse = loop.create_task(heartbeat())
    try:
        loop.run_until_complete(run())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        pulse.cancel()
        loop.run_until_complete(asyncio.gather(pulse, return_exceptions=True))
        loop.close()
