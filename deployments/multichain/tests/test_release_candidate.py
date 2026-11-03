"""Exact production artifact through real ASGI routes; no listener or deployment."""
import asyncio
import hashlib
import json
import re
import httpx
from autodefi.api import create_app
from autodefi.config import ROOT
from autodefi.adapters import CHAINS
from test_api_ai import FixtureModel


def test_candidate_routes_assets_seven_networks_and_safety(env):
    env['config'].multichain=True
    candidate=ROOT/'site/candidate'
    manifest=json.loads((candidate/'build-manifest.json').read_text())
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel(),frontend='candidate')
    async def run():
        async def heartbeat():
            while True:await asyncio.sleep(.01)
        beat=asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=env['config'].origin) as client:
                for route in ('/','/vault','/vault?network=base'):
                    response=await client.get(route)
                    assert response.status_code==200
                    assert response.content==(candidate/'index.html').read_bytes()
                    assert hashlib.sha256(response.content).hexdigest()==manifest['htmlSha256']
                    assert '<title>VELA — Multichain AI-Powered DeFi Trading</title>' in response.text
                    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"',response.text):
                        assert (await client.get(asset)).status_code==200
                for filename in manifest['outputs']:
                    response=await client.get('/'+filename.removeprefix('candidate/'))
                    assert response.status_code==200
                    assert hashlib.sha256(response.content).hexdigest()==manifest['sha256'][filename]
                for network in CHAINS:
                    headers={'X-Vella-Network':network}
                    config=(await client.get('/api/config',headers=headers)).json()
                    assert config['brand']['name']=='VELA'
                    assert config['chain']['id']==network
                    assert {chain['id'] for chain in config['chains']}==set(CHAINS)
                    assert config['mode']=='PAPER' and config['lending']=='DEMO'
                    assert config['liveEnabled'] is False and config['realFunding'] is False
                    assert (await client.get('/api/account',headers=headers)).status_code==401
                assert (await client.get('/api/config',headers={'X-Vella-Network':'unknown'})).status_code==400
                assert (await client.get('/api/live/order')).status_code==404
        finally:
            await asyncio.get_running_loop().shutdown_default_executor()
            beat.cancel()
    try:asyncio.run(run())
    finally:app.state.db.close()
