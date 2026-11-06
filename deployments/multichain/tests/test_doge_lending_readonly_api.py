"""Independent reader cannot submit transactions or read the private demo ledger."""
import json
import time
from fastapi.testclient import TestClient
from autodefi import doge_lending as module

OWNER='0x'+'11'*20

def client(tmp_path, monkeypatch, enabled=False, failure=False):
    d=dict(schemaVersion=1,chainId=6281971,enabled=enabled,market='0x'+'22'*20,
           manager=OWNER,runtimeCodeHash='0x'+'33'*32,deploymentBlock=1,confirmations=3,
           conversion={'enabled':False})
    p=tmp_path/'settings.json';p.write_text(json.dumps(d));calls=[]
    def snapshot(self,wallet,tx):
        calls.append((wallet,tx))
        if failure:raise RuntimeError('provider details must not leak')
        return {'owner':wallet,'transaction':tx,'source':'chain'}
    monkeypatch.setattr(module.DogeLendingReader,'snapshot',snapshot)
    return TestClient(module.create_readonly_app(p)),calls

def test_disabled_market_never_reads_chain(tmp_path,monkeypatch):
    c,calls=client(tmp_path,monkeypatch)
    with c:
        r=c.get('/api/doge-lending/config')
        assert r.json()['enabled'] is False and r.headers['cache-control']=='no-store'
        assert c.get('/api/doge-lending/account',headers={'x-wallet-address':OWNER}).status_code==400
        assert not calls

def test_public_wallet_reads_cached_but_transactions_distinct(tmp_path,monkeypatch):
    c,calls=client(tmp_path,monkeypatch,enabled=True)
    with c:
        for _ in range(3):
            assert c.get('/api/doge-lending/account',headers={'x-wallet-address':OWNER}).json()['owner']==OWNER
        assert len(calls)==1
        assert c.get('/api/doge-lending/account?tx=0x'+'44'*32,headers={'x-wallet-address':OWNER}).status_code==200
        assert len(calls)==2

def test_no_wallet_writes_or_arbitrary_rpc_and_no_error_leak(tmp_path,monkeypatch):
    c,calls=client(tmp_path,monkeypatch,enabled=True,failure=True)
    with c:
        for address in ['', '0x'+'00'*20,'https://attacker.invalid']:
            assert c.get('/api/doge-lending/account',headers={'x-wallet-address':address}).status_code==400
        assert c.get('/api/doge-lending/account?tx=bad',headers={'x-wallet-address':OWNER}).status_code==400
        assert not calls
        for path in ['/api/doge-lending/account','/api/doge-lending/config']:
            assert c.post(path,json={}).status_code==405
        assert c.get('/api/trading/status').status_code==404
        r=c.get('/api/doge-lending/account',headers={'x-wallet-address':OWNER})
        assert r.status_code==503 and 'provider details' not in r.text
