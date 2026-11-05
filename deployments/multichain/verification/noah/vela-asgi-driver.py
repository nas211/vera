"""Socket-free fixture transport for the TypeScript dashboard integration tests.

Creates a new disposable database, known synthetic wallet and market feed. No
runtime lifespan, RPC, public market connection, user wallet or real signature.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'tests')]
import httpx
from eth_account.messages import encode_defunct
from autodefi.api import create_app
from autodefi.config import Config
from autodefi.store import open_db, account
from autodefi.lending import Lending
from autodefi.trading import GovernedPaper
from conftest import ALICE, fixture_feed, fixture_vault, save_vault, funded, fill
from test_api_ai import FixtureModel
from vela.db import transaction

class NoChain:
    def __getattr__(self, name):
        raise AssertionError('No chain calls allowed in local adapter tests: ' + name)

async def main():
    frozen = time.time()
    time.time = lambda: frozen  # Deterministic interest and session expiry, matching backend fixtures.
    # Heartbeat accommodates the managed executor's restricted selector wakeups.
    async def heartbeat():
        while True:
            await asyncio.sleep(.01)
    pulse = asyncio.create_task(heartbeat())
    parent = ROOT / '.runtime/lighter-showcase'
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vela-adapter-test-', dir=parent) as temporary:
        config = Config(db_path=Path(temporary) / 'isolated.sqlite3', network_enabled=False)
        db = open_db(config.db_path)
        feed = fixture_feed()
        paper = GovernedPaper(db, config.paper(), feed)
        lending = Lending(db)
        owner = ALICE.address.lower()
        vault = fixture_vault(owner)
        with transaction(db):
            account(db, owner)
            save_vault(db, vault)
            lending.quote(owner, '0')
        env = dict(db=db, feed=feed, paper=paper, lending=lending, owner=owner, v=vault, config=config)
        funded(env)
        fill(env, side=sys.argv[1] if len(sys.argv) > 1 else 'long')
        db.close()
        app = create_app(config, feed=feed, chain=NoChain(), model=FixtureModel())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=config.origin,
                                        headers={'Origin': config.origin, 'X-ADP-Client': 'web'}) as client:
                challenge = (await client.post('/api/auth/nonce', json={'address': owner, 'chainId': 6281971})).json()
                # Known test identity only. No user wallet or network signing.
                signature = ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
                auth = await client.post('/api/auth/verify', json=dict(address=owner, nonce=challenge['nonce'], signature=signature))
                assert auth.status_code == 200, auth.text
                print(json.dumps(dict(ready=True, address=owner)), flush=True)
                while True:
                    line = await asyncio.to_thread(sys.stdin.readline)
                    if not line:
                        break
                    message = json.loads(line)
                    if message.get('control') == 'tick':
                        app.state.db.execute('UPDATE paper_orders SET due=0')
                        app.state.paper.tick()
                        result = dict(status=200, body={'ticked': True})
                    else:
                        context = message['context']
                        headers = {'X-Wallet-Address': context['address'], 'X-Vella-Network': context['network']}
                        if message.get('key'):
                            headers['Idempotency-Key'] = message['key']
                        response = await client.request('POST' if 'body' in message else 'GET', message['path'],
                                                        json=message.get('body'), headers=headers)
                        result = dict(status=response.status_code, body=response.json())
                    print(json.dumps(result), flush=True)
        finally:
            app.state.db.close()
            await asyncio.get_running_loop().shutdown_default_executor()
            pulse.cancel()

asyncio.run(main())
