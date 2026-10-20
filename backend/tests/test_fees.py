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


def test_price_updates_only_active_legacy_and_enabled(setup_db):
    db, s, _ = setup_db
    assert queue_active_price(db, s, 100, 0) == 0
    s.chain_writes = True
    assert queue_active_price(db, s, 100, 0) == 100
    assert queue_active_price(db, s, 110, 100) == 100
    queue_active_price(db, s, 140, 100)
    assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    db.execute("DELETE FROM outbox")
    s.config = dict(s.config, deployment={"version":2})
    assert queue_active_price(db, s, 200, 0) == 0


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
