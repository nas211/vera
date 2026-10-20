"""Local EVM integration: contracts + signed outbox + replay + paper accounting."""
import json
import time
import pytest
from decimal import Decimal as D
from pathlib import Path
from web3 import Web3
from eth_account import Account
from vela.settings import ROOT, Settings
from vela.db import get
from vela.chain import Chain
from vela.paper import PaperAdapter
from vela.agents import Agents


def drain_until(chain, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chain.drain()
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError("Outbox did not confirm before deadline")


@pytest.mark.parametrize("version",[1,2])
def test_full_local_chain_roundtrip_and_resume(setup_db,version):
    db, settings, user = setup_db
    db.execute("DELETE FROM users")
    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:18545"))
    assert w3.eth.chain_id == 31337
    owner = w3.eth.accounts[0]
    wallet = w3.eth.accounts[1]
    operator = Account.create()  # Generated only in memory for loopback tests.
    w3.eth.wait_for_transaction_receipt(w3.eth.send_transaction({"from": owner, "to": operator.address, "value": 10**18}))
    name="VelaVaultV2" if version==2 else "VelaVault"
    artifact = json.loads((ROOT / f"contracts/out/{name}.sol/{name}.json").read_text())
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    r = w3.eth.wait_for_transaction_receipt(factory.constructor(owner, operator.address).transact({"from": owner}))
    v = w3.eth.contract(address=r.contractAddress, abi=artifact["abi"])
    config = dict(settings.config, deployment=dict(vault=v.address, blockNumber=r.blockNumber,version=version))
    s = Settings(db_path=settings.db_path, rpc="http://127.0.0.1:18545", chain_id=31337, confirmations=0, config=config,
                 chain_writes=True, gas_reserve=D("0"))
    chain = Chain(db, s, operator.key)
    v.functions.fundPool().transact({"from": owner, "value": 100 * 10**18})
    from vela.db import enqueue
    if version==1:
        from vela.quotes import process_requests
        from vela.worker import queue_active_price
        db.execute("INSERT INTO start_quotes VALUES ('cold-start',?,1,?,?,'pending',NULL,NULL)",
                   (wallet.lower(),str(10*10**18),time.time()))
        process_requests(chain,True)
        # No active user exists yet: the START request must prepare conversion.
        queue_active_price(db,s,time.time(),0)
        initial_price=db.execute("SELECT id FROM outbox WHERE kind='updatePrice'").fetchone()[0]
        chain.drain()  # persist signed transaction, before broadcasting
        row = db.execute("SELECT * FROM outbox WHERE id=?",(initial_price,)).fetchone()
        assert row["status"] == "signed"
        expected_hash = row["tx_hash"]
        restarted = Chain(db, s, operator.key)
        drain_until(restarted, lambda: db.execute(
            "SELECT status FROM outbox WHERE id=?",(initial_price,)).fetchone()[0] == "confirmed")
        assert db.execute("SELECT status,tx_hash FROM outbox WHERE id=?",(initial_price,)).fetchone()[:] == ("confirmed", expected_hash)
        process_requests(restarted,True)
        assert db.execute("SELECT status FROM start_quotes WHERE id='cold-start'").fetchone()[0]=="ready"
        assert db.execute("SELECT count(*) FROM outbox WHERE kind='updatePrice'").fetchone()[0]==1
        start = v.functions.startTrading(1).transact({"from": wallet, "value": 10 * 10**18})
    else:
        from vela.quotes import sign_quote
        q=sign_quote(operator,31337,v.address,wallet,1,10*10**18,20_000_000,
                     w3.eth.get_block("latest")["timestamp"],1)
        start=v.functions.startTradingWithQuote(1,int(q["price"]),int(q["issuedAt"]),
                  int(q["deadline"]),int(q["nonce"]),q["signature"]).transact({"from":wallet,"value":10*10**18})
        restarted=Chain(db,s,operator.key)
    w3.eth.wait_for_transaction_receipt(start)
    restarted.sync()
    before_count = db.execute("SELECT count(*) FROM events").fetchone()[0]
    restarted.sync()
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == before_count
    u = dict(db.execute("SELECT * FROM users WHERE address=?", (wallet.lower(),)).fetchone())
    assert u["status"] == "active" and D(u["initial"]) == 2
    adapter = PaperAdapter(db, s)
    p = adapter.open(wallet, dict(symbol="DOGE/USD", side="short", stop_bps="100",
        target_bps="150", hold=60, signal_id="explicit-local-fixture"))
    t = adapter.close(p["id"], "integration_fixture")
    drain_until(restarted, lambda: db.execute("SELECT chain_status FROM trades").fetchone()[0] == "confirmed")
    assert db.execute("SELECT chain_status FROM trades").fetchone()[0] == "confirmed"
    w3.eth.wait_for_transaction_receipt(v.functions.stopTrading().transact({"from": wallet}))
    restarted.sync()
    adapter.tick(Agents(db))
    before = w3.eth.get_balance(wallet)
    drain_until(restarted, lambda: db.execute(
        "SELECT status FROM outbox WHERE kind='settle'").fetchone()[0] == "confirmed")
    restarted.sync()
    u = dict(db.execute("SELECT * FROM users").fetchone())
    assert u["status"] == "settled"
    assert w3.eth.get_balance(wallet) > before
    assert int(u["doge_out"]) == int((D(2) + D(t["pnl"])) / D(".2") * 10**18)
    assert v.functions.activeUsers().call() == 0
    if version==2:
        assert v.functions.dogeUsd().call()==0
        assert db.execute("SELECT count(*) FROM outbox WHERE kind='updatePrice'").fetchone()[0]==0
        assert len(list(db.execute("SELECT * FROM network_fees")))==2 # log + settle only
    assert v.functions.tradeLogged(bytes.fromhex(t["id"].zfill(64))).call()
    # Replay a confirmed event: no duplicate session, balance, trade, or cursor reset.
    restarted.sync()
    assert db.execute("SELECT count(*) FROM trades").fetchone()[0] == 1


def test_durable_cursor_reorg_halts(setup_db):
    db, settings, _ = setup_db
    # Reuse a minimal mocked chain reader without network or deployment.
    from vela.db import put
    put(db, "chain_cursor", 5);put(db, "chain_cursor_hash", "0x" + "00" * 32)
    class Eth:
        def get_block(self, _): return {"hash": bytes.fromhex("11" * 32)}
    class W3:
        eth = Eth()
    c = Chain.__new__(Chain)
    c.db, c.settings, c.start_block, c.w3 = db, settings, 1, W3()
    import pytest
    with pytest.raises(RuntimeError, match="reorganization"):
        c.sync()
    assert get(db, "chain_halted")
