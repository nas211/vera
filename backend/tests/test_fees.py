import time
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from vela import fees
from vela.db import enqueue
from vela.worker import queue_active_price


def test_idle_worker_never_queues_or_keeps_price_writes(setup_db):
    db, s, _ = setup_db
    s.chain_writes = True
    db.execute("UPDATE users SET status='settled'")
    enqueue(db, "old-idle", "updatePrice", {})
    for now in range(0, 3600, 30):
        assert queue_active_price(db, s, now, 0) == 0
    assert db.execute("SELECT count(*) FROM outbox WHERE status='queued'").fetchone()[0] == 0
    assert db.execute("SELECT status FROM outbox WHERE id='old-idle'").fetchone()[0] == "cancelled"


def test_price_updates_only_conversion_legacy_and_enabled(setup_db):
    db, s, _ = setup_db
    assert queue_active_price(db, s, 100, 0) == 0
    s.chain_writes = True
    # Active books use live off-chain prices, without ongoing paid heartbeats.
    assert queue_active_price(db, s, 100, 0) == 0
    db.execute("UPDATE users SET status='stopping'")
    assert queue_active_price(db, s, 100, 0) == 100
    assert queue_active_price(db, s, 110, 100) == 100
    queue_active_price(db, s, 140, 100)
    assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    db.execute("DELETE FROM outbox")
    s.config = dict(s.config, deployment={"version":2})
    assert queue_active_price(db, s, 200, 0) == 0


def test_pending_start_keeps_its_single_conversion_price_write(setup_db):
    db,s,user=setup_db
    s.chain_writes=True
    db.execute("UPDATE users SET status='settled'")
    now=time.time()
    db.execute("INSERT INTO start_quotes VALUES ('request',?,1,'1000',?,'pending',NULL,NULL)",(user,now))
    enqueue(db,"cold-start","updatePrice",{})
    queue_active_price(db,s,now,0)
    assert db.execute("SELECT status FROM outbox").fetchone()[0]=="queued"
    queue_active_price(db,s,now+91,0)
    assert db.execute("SELECT status FROM outbox").fetchone()[0]=="cancelled"


def test_fee_quote_includes_data_fee_and_fails_closed():
    seen = []
    class Oracle:
        def getL1Fee(self, raw):
            seen.append(raw)
            return SimpleNamespace(call=lambda: 10**17)
    w3 = SimpleNamespace(eth=SimpleNamespace(contract=lambda **_:SimpleNamespace(functions=Oracle())))
    tx = dict(chainId=6281971, nonce=1, gas=21000, gasPrice=1000, to="0x"+"11"*20, value=1, data="0x")
    q = fees.estimate(w3, tx)
    assert q["total"] == 21000000 + 10**17
    assert q["allowance"] > q["total"] and seen[0] == fees.encoded_for_estimate(tx)
    w3.eth.contract = lambda **_:None
    with pytest.raises(fees.FeeBlocked, match="Full DogeOS fee unavailable"):
        fees.estimate(w3, tx)
    with pytest.raises(fees.FeeBlocked, match="omitted"):
        fees.actual_fee(dict(gasUsed=1, effectiveGasPrice=1), 6281971)


def test_fee_reservations_survive_restarts_and_do_not_expire_pending(setup_db):
    db, s, _ = setup_db
    s.chain_writes = True
    quote = dict(allowance=2*10**17)
    fees.check(db, s, quote, 5*10**18)
    fees.reserve(db, "0x"+"11"*32, quote, "fixture")
    assert fees.spent(db) == 2*10**17
    db.execute("UPDATE network_fees SET created=?", (time.time()-100000,))
    assert fees.spent(db) == 2*10**17
    fees.reserve(db, "0x"+"22"*32, quote, "fixture")
    with pytest.raises(fees.FeeBlocked, match="24-hour"):
        fees.check(db, s, quote, 5*10**18)
    # The same pending transaction is not charged twice during retry.
    fees.check(db, s, quote, 5*10**18, exclude="0x"+"11"*32)
    with pytest.raises(fees.FeeBlocked, match="reserve"):
        fees.check(db, s, dict(allowance=1), 10**18)
    with pytest.raises(fees.FeeBlocked, match="per-transaction"):
        fees.check(db, s, dict(allowance=3*10**17), 5*10**18)
    s.chain_writes = False
    with pytest.raises(fees.FeeBlocked, match="paused"):
        fees.check(db, s, quote, 5*10**18)


def test_actual_receipt_costs_include_l1_fee_even_when_reverted(setup_db):
    db, _, _ = setup_db
    tx = "0x"+"33"*32
    fees.reserve(db, tx, dict(allowance=10**17), "fixture")
    r = dict(transactionHash=tx, gasUsed="0x5208", effectiveGasPrice="0x1", l1Fee=hex(10**16), status=0)
    assert fees.record(db, r, 6281971) == 21000+10**16
    assert fees.spent(db) == 21000+10**16
    fees.record(db, r, 6281971)
    assert db.execute("SELECT count(*) FROM network_fees").fetchone()[0] == 1


def test_empty_outbox_cannot_advertise_ready_with_exhausted_budget(setup_db):
    from vela.chain import Chain
    db,s,user=setup_db
    s.chain_writes=True
    chain=Chain.__new__(Chain)
    chain.db,chain.settings=db,s
    chain.account=SimpleNamespace(address=user)
    chain.w3=SimpleNamespace(eth=SimpleNamespace(get_balance=lambda _:10*10**18))
    chain.drain()
    assert db.execute("SELECT ok FROM services WHERE name='operator'").fetchone()[0]==1
    fees.reserve(db,"0x"+"99"*32,dict(allowance=5*10**17),"fixture")
    chain.drain()
    row=db.execute("SELECT ok,detail FROM services WHERE name='operator'").fetchone()
    assert row["ok"]==0 and "budget" in row["detail"]


def test_unfunded_operator_pauses_entries_but_existing_positions_can_close(setup_db):
    from vela.paper import PaperAdapter
    db,s,user=setup_db
    adapter=PaperAdapter(db,s)
    signal=dict(symbol="DOGE/USD",side="long",stop_bps="100",target_bps="150",hold=60,signal_id="fixture")
    agent=SimpleNamespace(signal=lambda _:signal)
    adapter.tick(agent,allow_entries=False)
    assert not adapter.getPosition(user)
    position=adapter.open(user,signal)
    db.execute("UPDATE users SET status='stopping'")
    adapter.tick(agent,allow_entries=False)
    assert not adapter.getPosition(user)
    assert db.execute("SELECT count(*) FROM trades").fetchone()[0]==1
