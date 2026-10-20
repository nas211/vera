"""Local EVM integration: contracts + signed outbox + replay + paper accounting."""
import json
import time
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


def test_full_local_chain_roundtrip_and_resume(setup_db):
    db, settings, user = setup_db
    db.execute("DELETE FROM users")
    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:18545"))
    assert w3.eth.chain_id == 31337
    owner = w3.eth.accounts[0]
    wallet = w3.eth.accounts[1]
    operator = Account.create()  # Generated only in memory for loopback tests.
    w3.eth.wait_for_transaction_receipt(w3.eth.send_transaction({"from": owner, "to": operator.address, "value": 10**18}))
    artifact = json.loads((ROOT / "contracts/out/VelaVault.sol/VelaVault.json").read_text())
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    r = w3.eth.wait_for_transaction_receipt(factory.constructor(owner, operator.address).transact({"from": owner}))
    v = w3.eth.contract(address=r.contractAddress, abi=artifact["abi"])
    config = dict(settings.config, deployment=dict(vault=v.address, blockNumber=r.blockNumber))
    s = Settings(db_path=settings.db_path, rpc="http://127.0.0.1:18545", chain_id=31337, confirmations=0, config=config)
    chain = Chain(db, s, operator.key)
    v.functions.fundPool().transact({"from": owner, "value": 100 * 10**18})
    from vela.db import enqueue
    enqueue(db, "initial-price", "updatePrice", {})
    chain.drain()  # persist signed transaction, before broadcasting
    row = db.execute("SELECT * FROM outbox WHERE id='initial-price'").fetchone()
    assert row["status"] == "signed"
    expected_hash = row["tx_hash"]
    restarted = Chain(db, s, operator.key)
    drain_until(restarted, lambda: db.execute(
        "SELECT status FROM outbox WHERE id='initial-price'").fetchone()[0] == "confirmed")
    assert db.execute("SELECT status,tx_hash FROM outbox WHERE id='initial-price'").fetchone()[:] == ("confirmed", expected_hash)
    start = v.functions.startTrading(1).transact({"from": wallet, "value": 10 * 10**18})
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
