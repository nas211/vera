"""Deterministic integration checks. These are not live DogeOS evidence."""
import time
from dataclasses import replace
from types import SimpleNamespace
import pytest
from autodefi.chain import DogeOS
from autodefi.config import Config
from vela.db import transaction
from conftest import funded, BOB


def test_confirmed_settlement_reconciles_after_browser_closed(env):
    funded(env)
    with transaction(env['db']): env['lending'].repay_available(env['owner'])
    assert env['lending'].loan(env['owner'])['state']=='AWAITING_CHAIN'
    observation={**env['v'],'loanState':3,'withdrawable':env['v']['collateral']}
    DogeOS.record(env['db'],env['owner'],observation)
    assert env['lending'].loan(env['owner'])['state']=='REPAID'
    assert env['lending'].health(env['owner'])['withdrawableDoge']=='10000'
    # State observation is not a transaction receipt; none is fabricated.
    assert env['db'].execute('SELECT COUNT(*) FROM chain_receipts').fetchone()[0]==0


def test_chain_observation_cannot_replace_another_owner_or_chain(env):
    original=env['db'].execute('SELECT data FROM vaults WHERE owner=?',(env['owner'],)).fetchone()[0]
    for patch in ({'owner':BOB.address.lower()},{'chainId':1}):
        with pytest.raises(ValueError,match='owner and Chikyū'):
            DogeOS.record(env['db'],env['owner'],{**env['v'],**patch})
    assert env['db'].execute('SELECT data FROM vaults WHERE owner=?',(env['owner'],)).fetchone()[0]==original


def test_pending_chain_observation_never_unlocks_repaid_demo_loan(env):
    funded(env)
    with transaction(env['db']): env['lending'].repay_available(env['owner'])
    DogeOS.record(env['db'],env['owner'],env['v'])
    assert env['lending'].loan(env['owner'])['state']=='AWAITING_CHAIN'
    assert env['lending'].health(env['owner'])['withdrawableDoge']=='0'


def test_slow_rpc_cannot_restore_permissions_after_newer_revocation(env):
    import json
    funded(env)
    revoked={**env['v'],'block':102,'trading':False,'agent':'0x'+'0'*40,'permissionRevision':2}
    DogeOS.record(env['db'],env['owner'],revoked)
    env['db'].execute('UPDATE vaults SET checked=0')
    with pytest.raises(ValueError,match='Older vault observation'):
        DogeOS.record(env['db'],env['owner'],env['v'])
    row=env['db'].execute('SELECT data,checked FROM vaults WHERE owner=?',(env['owner'],)).fetchone()
    assert json.loads(row['data'])==revoked and row['checked']==0


def test_receipt_resynchronization_never_duplicates_collateral_or_events(env):
    receipt=dict(hash='f'*64,block=100,blockHash='LOCAL_FIXTURE')
    for _ in range(3):DogeOS.record(env['db'],env['owner'],env['v'],receipt)
    assert env['lending'].health(env['owner'])['collateral']=='10000'
    assert env['db'].execute('SELECT COUNT(*) FROM chain_receipts').fetchone()[0]==1
    assert env['db'].execute("SELECT COUNT(*) FROM loan_events WHERE kind='CONFIRMED_TESTNET_TRANSACTION'").fetchone()[0]==1


def test_walletconnect_config_is_dedicated_and_optional(monkeypatch):
    monkeypatch.delenv('ADP_WALLETCONNECT_PROJECT_ID',raising=False)
    monkeypatch.setenv('VELA_WALLETCONNECT_PROJECT_ID','must-not-inherit')
    assert Config.from_env().walletconnect_project_id==''
    monkeypatch.setenv('ADP_WALLETCONNECT_PROJECT_ID','public-autodefi-test-id')
    assert Config.from_env().walletconnect_project_id=='public-autodefi-test-id'


def test_vera_environment_aliases_preserve_isolation_and_fail_on_conflicts(monkeypatch):
    from autodefi.trading import ProtocolModel
    monkeypatch.setenv('VERA_PROTOCOL_MYDOGE_CLIENT_ID','public-fixture-vera')
    monkeypatch.delenv('ADP_MYDOGE_CLIENT_ID',raising=False)
    assert Config.from_env().client_id=='public-fixture-vera'
    monkeypatch.setenv('ADP_MYDOGE_CLIENT_ID','different-fixture')
    with pytest.raises(ValueError,match='Conflicting'):Config.from_env()
    monkeypatch.delenv('ADP_MYDOGE_CLIENT_ID')
    monkeypatch.setenv('VERA_PROTOCOL_EXECUTION','LIVE')
    with pytest.raises(ValueError,match='disabled'):Config.from_env()
    monkeypatch.delenv('VERA_PROTOCOL_EXECUTION')
    monkeypatch.setenv('VERA_PROTOCOL_MODEL','dedicated-fixture-model')
    monkeypatch.setenv('VERA_PROTOCOL_MODEL_KEY','not-a-real-key')
    assert ProtocolModel().ready
    monkeypatch.setenv('ADP_MODEL_KEY','conflicting-fixture-key')
    with pytest.raises(ValueError,match='Conflicting'):ProtocolModel()


def test_old_explicit_database_path_tracks_repository_move(monkeypatch):
    from autodefi.config import ROOT
    monkeypatch.setenv('ADP_DB','/root/AutonomousDeFi/.runtime/lighter-showcase/protocol.sqlite3')
    assert Config.from_env().db_path==ROOT/'.runtime/lighter-showcase/protocol.sqlite3'
    monkeypatch.setenv('ADP_DB','/root/Vera/production.sqlite')
    with pytest.raises(ValueError,match='Independent'):Config.from_env()


@pytest.mark.parametrize('failure',[None,'wrong_chain','stale','reorg','unconfirmed_receipt','failed_receipt','foreign_sender'])
def test_read_validates_confirmed_rpc_state_and_receipts(env,failure):
    """Exercise the real reader with a deterministic RPC boundary, never a live RPC."""
    v=env['v'];owner=env['owner'];factory='0x'+'12'*20;block_hash=bytes.fromhex('aa'*32)
    class Call:
        def __init__(self,name):self.name=name
        def __call__(self,*args):return self
        def call(self,block_identifier):
            assert block_identifier==100
            if self.name=='vaultOf':return v['address']
            if self.name=='limits':return [int(x) if k!='autoReduce' else x for k,x in v['limits'].items()]
            if self.name=='loanId':return bytes.fromhex(v['loanId'][2:])
            return v[self.name]
    class Functions:
        def __getattr__(self,name):return Call(name)
    class Eth:
        chain_id=1 if failure=='wrong_chain' else 6281971
        reads=0
        def get_block(self,height):
            if height=='latest':return SimpleNamespace(number=102,timestamp=time.time()-(120 if failure=='stale' else 0))
            assert height==100;self.reads+=1
            return SimpleNamespace(hash=bytes.fromhex('bb'*32) if failure=='reorg' and self.reads>1 else block_hash)
        def contract(self,*args,**kwargs):return SimpleNamespace(functions=Functions())
        def get_transaction_receipt(self,tx):return SimpleNamespace(status=0 if failure=='failed_receipt' else 1,
            blockNumber=101 if failure=='unconfirmed_receipt' else 100,blockHash=block_hash,transactionHash=bytes.fromhex('cc'*32))
        def get_transaction(self,tx):return {'from':BOB.address if failure=='foreign_sender' else owner,'to':v['address']}
    chain=DogeOS(replace(env['config'],factory=factory,network_enabled=True));chain.w3=SimpleNamespace(eth=Eth())
    tx='0x'+'cc'*32
    if failure:
        with pytest.raises(ValueError):chain.read(owner,tx)
    else:
        observation,receipt=chain.read(owner,tx)
        assert observation['owner']==owner and observation['block']==100
        assert receipt['block']==100 and receipt['hash']=='cc'*32
