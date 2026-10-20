"""Explicit engineering smoke session on DogeOS; never pretends to be an AI decision."""
import json
import os
import time
from decimal import Decimal as D
from web3 import Web3
from .settings import Settings, ROOT
from .db import connect, enqueue, get
from .chain import Chain
from .paper import PaperAdapter
from .prices import PriceFeed, quote
from .agents import Agents
from . import fees
from .quotes import sign_quote


def main():
    settings = Settings.from_env()
    if not settings.chain_writes:
        raise RuntimeError("On-chain verification paused; configure an explicit full-fee budget before enabling writes")
    if settings.chain_id != 6281971:
        raise RuntimeError("This verification is for DogeOS testnet only")
    db = connect(settings.db_path)
    chain = Chain(db, settings, os.environ.get("VELA_OPERATOR_PRIVATE_KEY"))
    if not chain.account:
        raise RuntimeError("Operator key required")
    import fcntl
    lock = settings.db_path.with_suffix(".worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    feed, adapter = PriceFeed(db, settings), PaperAdapter(db, settings)
    wallet = chain.account.address
    session = chain.vault.functions.sessions(wallet).call()
    if session[0] != 0:
        raise RuntimeError("Verification wallet already has an active session; refusing duplicate")
    report = dict(chainId=6281971, wallet=wallet, vault=chain.vault.address,
        signal="Explicit engineering smoke signal, not AI or backtest evidence",
        mydogeApprovalTested=False, transactions={})
    version=settings.config["deployment"].get("version",1)

    def pump_until(predicate, timeout=150):
        deadline = time.time() + timeout
        last_feed = last_price = 0
        while time.time() < deadline:
            now = time.time()
            if now - last_feed > 5:
                feed.poll();last_feed = now
            caught_up = chain.sync()
            if version<2 and now - last_price > 30:
                if not db.execute("SELECT 1 FROM outbox WHERE kind='updatePrice' AND status IN ('queued','signed','sent')").fetchone():
                    enqueue(db, "smoke-price:" + str(time.time_ns()), "updatePrice", {})
                last_price = now
            if caught_up:
                chain.drain()
            if predicate():
                return
            time.sleep(1)
        raise RuntimeError("Timed out; durable DB/outbox retained for recovery")

    def direct(call, value=0):
        tx = call.build_transaction({"from":wallet, "chainId":6281971,
            "nonce":chain.w3.eth.get_transaction_count(wallet, "pending"),
            "gasPrice":chain.w3.eth.gas_price, "value":value})
        tx["gas"] = int(tx["gas"] * 1.2)
        signed = chain.account.sign_transaction(tx)
        fee_quote=fees.estimate(chain.w3,tx,signed.raw_transaction)
        fees.check(db,settings,fee_quote,chain.w3.eth.get_balance(wallet),value)
        fees.reserve(db,Web3.to_hex(signed.hash),fee_quote,"engineering_smoke")
        h = chain.w3.eth.send_raw_transaction(signed.raw_transaction)
        r = chain.w3.eth.wait_for_transaction_receipt(h, timeout=120)
        fees.record(db,r,6281971,"engineering_smoke")
        if r["status"] != 1:
            raise RuntimeError("Smoke transaction reverted")
        return Web3.to_hex(h)

    feed.poll()
    if version>=2:
        issued=min(int(time.time()),chain.w3.eth.get_block("latest")["timestamp"])
        q=sign_quote(chain.account,6281971,chain.vault.address,wallet,1,5*10**18,
            int(quote(db,"DOGE/USD")*10**8),issued,session[5]+1)
        call=chain.vault.functions.startTradingWithQuote(1,int(q["price"]),int(q["issuedAt"]),
                int(q["deadline"]),int(q["nonce"]),q["signature"])
    else:
        pump_until(lambda: chain.vault.functions.dogeUsd().call() > 0 and
                   not db.execute("SELECT 1 FROM outbox WHERE status IN ('signed','sent')").fetchone())
        call=chain.vault.functions.startTrading(1)
    report["transactions"]["start"] = direct(call, 5 * 10**18)
    pump_until(lambda: bool(db.execute("SELECT 1 FROM users WHERE address=? AND status='active'",
                                       (wallet.lower(),)).fetchone()))
    p = adapter.open(wallet, dict(symbol="DOGE/USD", side="long", stop_bps="100", target_bps="150",
                                 hold=60, signal_id="engineering-smoke:" + str(time.time_ns())))
    time.sleep(2)
    feed.poll()
    t = adapter.close(p["id"], "engineering_smoke")
    pump_until(lambda: db.execute("SELECT chain_status FROM trades WHERE id=?", (t["id"],)).fetchone()[0] == "confirmed")
    report["transactions"]["tradeLog"] = db.execute("SELECT tx_hash FROM trades WHERE id=?", (t["id"],)).fetchone()[0]
    # Finish outstanding price transactions before sharing the nonce with user STOP.
    pump_until(lambda: not db.execute("SELECT 1 FROM outbox WHERE status IN ('signed','sent')").fetchone())
    report["transactions"]["stop"] = direct(chain.vault.functions.stopTrading())
    pump_until(lambda: db.execute("SELECT status FROM users WHERE address=?", (wallet.lower(),)).fetchone()[0] == "stopping")
    adapter.tick(Agents(db))
    before = chain.w3.eth.get_balance(wallet)
    pump_until(lambda: db.execute("SELECT status FROM users WHERE address=?", (wallet.lower(),)).fetchone()[0] == "settled")
    user = dict(db.execute("SELECT * FROM users WHERE address=?", (wallet.lower(),)).fetchone())
    report["transactions"]["settlement"] = user["settlement_tx"]
    report.update(pnlUsdc=user["final_pnl"], dogeReturned=str(D(user["doge_out"])/10**18),
                  settlementConfirmed=chain.vault.functions.sessions(wallet).call()[0] == 0,
                  tradeLogged=chain.vault.functions.tradeLogged(bytes.fromhex(t["id"].zfill(64))).call(),
                  verifiedAt=time.time())
    if not report["settlementConfirmed"] or not report["tradeLogged"] or int(user["doge_out"]) <= 0:
        raise RuntimeError("On-chain verification failed")
    (ROOT / "snapshot/testnet-e2e.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    db.close()
    lock.close()


if __name__ == "__main__":
    main()
