"""Gateway safety/identity tests with explicit HTTP fixtures, not wallet approvals."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from autodefi.dogeos_readonly import DogeOSReadOnly
from autodefi.adapters import CHAINS
from autodefi.config import Config
from tools.dogeos_readonly import create_app, ORIGIN, BLOCKED


class FixtureReader:
    factory = '0xD6644a0C4233257b091D669142A7A8db3F2B7C3a'
    reference = '0x533beF9B9453914b584241F53aBA40f106Ac3125'
    def __init__(self):
        self.wallets = []
    def snapshot(self, wallet=None):
        self.wallets.append(wallet)
        return dict(status='VERIFIED', chainId=6281971, block=123, wallet=wallet,
                    observedAt=0, readOnly=True, registered=False, vault=None)


def test_reader_rejects_all_broadcast_and_signing_methods():
    reader = DogeOSReadOnly(client=SimpleNamespace(post=lambda *a,**k:pytest.fail('Must reject before HTTP')))
    for method in ('eth_sendRawTransaction','eth_sendTransaction','eth_sign','personal_sign','wallet_sendCalls'):
        with pytest.raises(ValueError, match='read-only'):
            reader.rpc(method, [])
    with pytest.raises(ValueError, match='Read-only ABI'):
        reader.call(reader.factory, 'VaultFactory', 'createVault', 'latest')


def test_gateway_blocks_contract_actions_preserves_sessions_and_seven_networks():
    sent = []
    reader = FixtureReader()
    def handler(request):
        sent.append(request)
        network = request.headers.get('x-vella-network','dogeos')
        if request.url.path == '/api/session':
            if request.headers.get('cookie') != 'adp_session=fixture':
                return httpx.Response(401, json={'error':'Authentication required'})
            return httpx.Response(200, json={'network':'dogeos','address':'0x'+'12'*20,'authenticated':True})
        if request.url.path == '/api/config':
            return httpx.Response(200, json=dict(chains=[a.public(Config(multichain=True)) for a in CHAINS.values()],
                chain=CHAINS[network].public(Config(multichain=True)),factory=None,attestorConfigured=False,
                mode='PAPER',lending='DEMO',liveEnabled=False,realFunding=False))
        if request.url.path == '/api/auth/verify':
            return httpx.Response(200, json={'authenticated':True}, headers={'set-cookie':'adp_session=fixture; HttpOnly; Secure; SameSite=strict; Path=/api'})
        return httpx.Response(200, json={'upstream':True})
    app = create_app(reader, transport=httpx.MockTransport(handler))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            assert (await client.get('/api/dogeos/account')).status_code == 401
            assert not reader.wallets
            h={'Origin':ORIGIN,'X-ADP-Client':'web','X-Vella-Network':'dogeos'}
            for path in BLOCKED | {'/api/simulated-vault/create'}:
                n=len(sent)
                assert (await client.post(path,json={},headers=h)).status_code == 403
                assert (await client.post(path+'/',json={},headers=h)).status_code == 403
                assert len(sent)==n
            assert (await client.post('/api/trading/allocate',json={},headers={**h,'Origin':'https://attacker.invalid'})).status_code==403
            assert (await client.post('/api/trading/allocate',json={'amount':'500','confirm':True},headers={**h,'Idempotency-Key':'same-key'})).status_code==200
            assert sent[-1].headers['idempotency-key']=='same-key'
            assert sent[-1].url.host=='127.0.0.1'
            verified=await client.post('/api/auth/verify',json={},headers=h)
            assert 'HttpOnly' in verified.headers['set-cookie'] and 'Secure' in verified.headers['set-cookie']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=ORIGIN) as stranger:
                assert (await stranger.get('/api/dogeos/account')).status_code==401
                assert 'cookie' not in sent[-1].headers
            account=await client.get('/api/dogeos/account',headers={'X-Vella-Network':'dogeos'})
            assert account.status_code==200 and reader.wallets[-1]=='0x'+'12'*20
            assert account.json()['vault'] is None  # Never show reference as the user's vault.
            assert (await client.get('/api/dogeos/account',headers={'X-Vella-Network':'base'})).status_code==403
            for network in CHAINS:
                config=(await client.get('/api/config',headers={'X-Vella-Network':network})).json()
                assert len(config['chains'])==7 and config['chain']['id']==network
                assert config['mode']=='PAPER' and not config['liveEnabled'] and not config['realFunding']
                assert config['factory'] is None and config['attestorConfigured'] is False
                doge=next(c for c in config['chains'] if c['id']=='dogeos')
                assert doge['capabilities']['contractReads'] and not doge['capabilities']['tokenTransfers']
                if network!='dogeos': assert config['chain']['capabilities']['simulatedVault']
            home=await client.get('/')
            assert home.json()=={'upstream':True}
            vault=await client.get('/vault')
            assert vault.json()=={'upstream':True}  # Same release for every portal route.
            demo=(await client.get('/api/dogeos/demonstration')).json()
            assert 'not live AI trades' in demo['label']
            assert any(float(c['realizedPnl'])>0 for c in demo['cases'])
            assert any(float(c['realizedPnl'])<0 for c in demo['cases'])
    asyncio.run(run())


def test_rpc_failure_does_not_return_verified_or_stale_values():
    class Broken(FixtureReader):
        def snapshot(self,wallet=None): raise ValueError('wrong chain or RPC failure')
    app=create_app(Broken(),transport=httpx.MockTransport(lambda req:httpx.Response(200,json={})))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=ORIGIN) as client:
            r=await client.get('/api/dogeos/status')
            assert r.status_code==503 and r.json()['status']=='UNAVAILABLE'
            assert 'walletBalanceTestDOGE' not in r.json() and 'vault' not in r.json()
    asyncio.run(run())


def test_narrow_wallet_capability_never_enables_lending_or_backend_broadcasts():
    sent=[]
    def handler(req):
        sent.append(req)
        return httpx.Response(200,json=dict(chain=CHAINS['dogeos'].public(Config(multichain=True)),chains=[],factory='legacy',attestorConfigured=True))
    app=create_app(FixtureReader(),transport=httpx.MockTransport(handler),wallet_transactions=True)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=ORIGIN) as c:
            config=(await c.get('/api/config')).json()
            assert config['dogeosWallet']['enabled']
            assert config['dogeosWallet']['actions']==['createVault','deposit','withdraw']
            assert config['factory'] is None and not config['attestorConfigured']
            assert not config['chain']['capabilities']['demoLending']
            h={'Origin':ORIGIN,'X-ADP-Client':'web','X-Vella-Network':'dogeos'}
            for path in BLOCKED:
                n=len(sent);assert (await c.post(path,headers=h,json={})).status_code==403;assert len(sent)==n
    asyncio.run(run())


def test_gateway_can_start_during_rpc_outage():
    class Unavailable(FixtureReader):
        client=SimpleNamespace(close=lambda:None)
        def snapshot(self,*a,**k):raise ValueError('offline')
        def verify_deployment(self):raise AssertionError('Must not block gateway startup on RPC')
    app=create_app(Unavailable(),transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'application':'available'})))
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=ORIGIN) as c:
                assert (await c.get('/api/markets')).json()=={'application':'available'}
                assert (await c.get('/api/dogeos/status')).status_code==503
    asyncio.run(run())


def test_transaction_query_bypasses_cached_snapshot_and_rejects_bad_hash():
    class Reader(FixtureReader):
        def snapshot(self,wallet=None,transaction=None):
            self.wallets.append(transaction)
            return dict(status='VERIFIED',wallet=wallet,observedAt=__import__('time').time(),transaction=transaction)
    reader=Reader()
    app=create_app(reader,transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'network':'dogeos','address':'0x'+'12'*20,'authenticated':True})))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=ORIGIN) as c:
            assert (await c.get('/api/dogeos/account')).status_code==200
            for _ in range(2):assert (await c.get('/api/dogeos/account?tx=0x'+'ab'*32)).status_code==200
            assert len(reader.wallets)==3
            assert (await c.get('/api/dogeos/account?tx=garbage')).status_code==400
    asyncio.run(run())
