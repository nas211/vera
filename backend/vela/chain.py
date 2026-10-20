"""Confirmed event cursor and durable single-signer transaction outbox."""
import json
import time
from decimal import Decimal as D
from web3 import Web3
from web3.exceptions import TransactionNotFound, Web3RPCError
from .settings import ROOT
from .db import transaction, get, put, log, health
from .prices import quote, entries_allowed
from . import fees


def hexstr(value):
    return Web3.to_hex(value)


class Chain:
    def __init__(self, db, settings, private_key=None):
        self.db, self.settings = db, settings
        deployment = settings.config.get("deployment")
        if not deployment:
            raise ValueError("DogeOS contracts are not deployed/configured")
        self.w3 = Web3(Web3.HTTPProvider(settings.rpc, request_kwargs={"timeout": 12}))
        if self.w3.eth.chain_id != settings.chain_id:
            raise ValueError("RPC chain mismatch")
        address = Web3.to_checksum_address(deployment["vault"])
        if not self.w3.eth.get_code(address):
            raise ValueError("Vault has no bytecode")
        abi_name = "VelaVaultV2.abi.json" if deployment.get("version",1)>=2 else "VelaVault.abi.json"
        self.vault = self.w3.eth.contract(address=address, abi=json.loads((ROOT / "config" / abi_name).read_text()))
        self.start_block = int(deployment["blockNumber"])
        self.account = self.w3.eth.account.from_key(private_key) if private_key else None
        if self.account and self.vault.functions.operator().call().lower() != self.account.address.lower():
            raise ValueError("Signer is not this vault's operator")
        binding = f"{settings.chain_id}:{address.lower()}:{self.start_block}"
        if get(db, "chain_binding", binding) != binding:
            raise ValueError("Database belongs to a different deployment")
        put(db, "chain_binding", binding)
        self.events = {getattr(self.vault.events, name)().topic: getattr(self.vault.events, name)()
                       for name in ("TradingStarted", "StopRequested", "Settled", "TradeLogged")}

    def sync(self):
        if get(self.db, "chain_halted"):
            raise RuntimeError("Chain history changed; operator reconciliation required")
        cursor = get(self.db, "chain_cursor", self.start_block - 1)
        known_hash = get(self.db, "chain_cursor_hash")
        if known_hash and hexstr(self.w3.eth.get_block(cursor)["hash"]) != known_hash:
            put(self.db, "chain_halted", True)
            raise RuntimeError("Confirmed block reorganization detected; halted")
        head = self.w3.eth.block_number - self.settings.confirmations
        if head < cursor:
            raise RuntimeError("RPC head behind durable cursor")
        end = min(head, cursor + 500)
        if end > cursor:
            block_hash = hexstr(self.w3.eth.get_block(end)["hash"])
            logs = self.w3.eth.get_logs(dict(address=self.vault.address, fromBlock=cursor + 1, toBlock=end))
            with transaction(self.db):
                for raw in sorted(logs, key=lambda r: (r["blockNumber"], r["transactionIndex"], r["logIndex"])):
                    decoder = self.events.get(hexstr(raw["topics"][0]))
                    if not decoder:
                        continue
                    event = decoder.process_log(raw)
                    self.apply_event(event["event"], dict(event["args"]), raw)
                if hexstr(self.w3.eth.get_block(end)["hash"]) != block_hash:
                    raise RuntimeError("Block changed while indexing")
                put(self.db, "chain_cursor", end)
                put(self.db, "chain_cursor_hash", block_hash)
        health(self.db, "events", True, f"Confirmed cursor {end}; target {head}")
        return end == head

    def apply_event(self, kind, a, raw):
        tx, block_hash = hexstr(raw["transactionHash"]), hexstr(raw["blockHash"])
        payload = {k: hexstr(v) if isinstance(v, bytes) else v for k, v in a.items()}
        inserted = self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
            (self.settings.chain_id, tx, raw["logIndex"], raw["blockNumber"], block_hash, kind, json.dumps(payload))).rowcount
        if not inserted:
            return
        user = a["user"].lower()
        if kind == "TradingStarted":
            prior = self.db.execute("SELECT * FROM users WHERE address=?", (user,)).fetchone()
            if prior and (prior["status"] != "settled" or self.db.execute("SELECT 1 FROM positions WHERE user=?", (user,)).fetchone()):
                raise RuntimeError("Unexpected new session; reconciliation required")
            credit = str(D(a["usdcCredited"]) / 1_000_000)
            started = self.w3.eth.get_block(raw["blockNumber"])["timestamp"]
            self.db.execute("""INSERT OR REPLACE INTO users(address,session,strategy,status,initial,
                balance,doge_in,started,start_tx,nonce) VALUES (?,?,?,'active',?,?,?,?,?,?)""",
                (user, tx, a["strategyId"], credit, credit, str(a["dogeIn"]), started, tx, prior["nonce"] + 1 if prior else 1))
        elif kind == "StopRequested":
            self.db.execute("UPDATE users SET status='stopping',stop_tx=? WHERE address=? AND status='active'", (tx, user))
        elif kind == "Settled":
            self.db.execute("UPDATE users SET status='settled',settlement_tx=?,doge_out=?,final_pnl=? WHERE address=?",
                (tx, str(a["dogeOut"]), str(D(a["pnl"]) / 1_000_000), user))
        elif kind == "TradeLogged":
            # Our trade ids are the UUID padded to bytes32, globally unique.
            trade_id = hexstr(a["tradeId"])[2:].lstrip("0").zfill(32)
            self.db.execute("UPDATE trades SET tx_hash=?,chain_status='confirmed' WHERE id=?", (tx, trade_id))

    def make_call(self, row):
        p = json.loads(row["payload"])
        if row["kind"] == "updatePrice":
            if not entries_allowed(self.db, "DOGE/USD", self.settings.max_price_age):
                raise ValueError("DOGE price unsafe")
            return self.vault.functions.updatePrice(int(quote(self.db, "DOGE/USD") * 100_000_000))
        if row["kind"] == "logTrade":
            t = self.db.execute("SELECT * FROM trades WHERE id=?", (p["trade"],)).fetchone()
            if not t:
                raise ValueError("Trade missing")
            return self.vault.functions.logTrade(
                Web3.to_checksum_address(t["user"]), bytes.fromhex(t["id"].zfill(64)), t["symbol"],
                0 if t["side"] == "long" else 1, int(D(t["qty"]) * 10**18),
                int(D(t["entry"]) * 10**8), int(D(t["exit"]) * 10**8), t["pnl_micro"])
        if row["kind"] == "settle":
            if not entries_allowed(self.db, "DOGE/USD", self.settings.max_price_age):
                raise ValueError("DOGE price unsafe")
            user = self.db.execute("SELECT * FROM users WHERE address=?", (p["user"],)).fetchone()
            if not user or user["session"] != p["session"] or user["status"] != "stopping":
                raise ValueError("Settlement session changed")
            if self.db.execute("SELECT 1 FROM positions WHERE user=?", (p["user"],)).fetchone():
                raise ValueError("Positions still open")
            if self.db.execute("SELECT 1 FROM trades WHERE session=? AND chain_status!='confirmed'", (p["session"],)).fetchone():
                raise ValueError("Trades not yet confirmed")
            if self.settings.config["deployment"].get("version",1)>=2:
                price = int(quote(self.db, "DOGE/USD") * 100_000_000)
                observed = int(self.db.execute("SELECT ts FROM prices WHERE symbol='DOGE/USD'").fetchone()[0])
                observed = min(observed, self.w3.eth.get_block("latest")["timestamp"])
                return self.vault.functions.settleAtPrice(Web3.to_checksum_address(p["user"]),int(p["pnl_micro"]),price,observed)
            price = self.vault.functions.dogeUsd().call()
            updated = self.vault.functions.priceUpdatedAt().call()
            if time.time() - updated > 90:
                raise ValueError("Awaiting fresh on-chain price")
            return self.vault.functions.settle(Web3.to_checksum_address(p["user"]), int(p["pnl_micro"]), price)
        raise ValueError("Unknown outbox action")

    def drain(self):
        if not self.account:
            health(self.db, "operator", False, "VELA_OPERATOR_PRIVATE_KEY is not configured")
            return
        # A single operator worker owns the DB lock; one in-flight tx at a time.
        row = self.db.execute("SELECT * FROM outbox WHERE status IN ('signed','sent') ORDER BY created LIMIT 1").fetchone()
        if row:
            try:
                receipt = self.w3.eth.get_transaction_receipt(row["tx_hash"])
            except TransactionNotFound:
                if not self.settings.chain_writes:
                    health(self.db, "operator", False, "On-chain transactions paused; pending receipt monitored")
                    return
                try:
                    tx = json.loads(row["payload"]).get("transaction")
                    if tx is None:
                        raise fees.FeeBlocked("Legacy pending transaction requires fee reconciliation")
                    estimate = fees.estimate(self.w3, tx, bytes.fromhex(row["raw_tx"]))
                    fees.check(self.db, self.settings, estimate,
                               self.w3.eth.get_balance(self.account.address), tx.get("value", 0), row["tx_hash"])
                    fees.reserve(self.db, row["tx_hash"], estimate, row["kind"])
                    sent = self.w3.eth.send_raw_transaction(bytes.fromhex(row["raw_tx"]))
                    if hexstr(sent) != row["tx_hash"]:
                        raise RuntimeError("Broadcast hash mismatch")
                    self.db.execute("UPDATE outbox SET status='sent' WHERE id=?", (row["id"],))
                except fees.FeeBlocked as exc:
                    health(self.db, "operator", False, str(exc))
                except (ValueError, Web3RPCError):
                    # "already known" or "nonce too low" is not proof of success.
                    # Retain signed bytes and await the exact transaction receipt.
                    health(self.db, "operator", False, "Awaiting signed transaction receipt")
                return
            if self.w3.eth.block_number - receipt["blockNumber"] < self.settings.confirmations:
                return
            status = "confirmed" if receipt["status"] == 1 else "failed"
            fees.record(self.db, receipt, self.settings.chain_id, row["kind"])
            self.db.execute("UPDATE outbox SET status=?,error=? WHERE id=?",
                (status, None if status == "confirmed" else "Transaction reverted; inspect before retry", row["id"]))
            if status == "confirmed" and row["kind"] == "logTrade":
                self.db.execute("UPDATE trades SET tx_hash=?,chain_status='confirmed' WHERE id=?",
                    (row["tx_hash"], json.loads(row["payload"])["trade"]))
            health(self.db, "operator", status == "confirmed", f"{row['kind']}: {status}")
            return
        if not self.settings.chain_writes:
            health(self.db, "operator", False, "On-chain transactions paused by Vela fee controls")
            return
        row = self.db.execute("""SELECT * FROM outbox WHERE status='queued'
            ORDER BY CASE kind WHEN 'updatePrice' THEN 0 WHEN 'logTrade' THEN 1 ELSE 2 END,created LIMIT 1""").fetchone()
        if not row:
            health(self.db, "operator", True, "Outbox empty")
            return
        try:
            call = self.make_call(row)
            tx = call.build_transaction(dict(
                **{"from": self.account.address}, chainId=self.settings.chain_id,
                nonce=self.w3.eth.get_transaction_count(self.account.address, "pending"),
                gasPrice=self.w3.eth.gas_price))
            tx["gas"] = int(tx["gas"] * 1.2)
            signed = self.account.sign_transaction(tx)
            raw = bytes(signed.raw_transaction)
            tx_hash = hexstr(self.w3.keccak(raw))
            estimate = fees.estimate(self.w3, tx, raw)
            fees.check(self.db, self.settings, estimate, self.w3.eth.get_balance(self.account.address), tx.get("value", 0))
            payload = json.loads(row["payload"])
            payload["transaction"] = dict(tx, data=hexstr(tx["data"]) if isinstance(tx["data"], bytes) else tx["data"])
            # Commit before any broadcast, so restart cannot lose a nonce/hash.
            with transaction(self.db):
                fees.reserve(self.db, tx_hash, estimate, row["kind"])
                self.db.execute("UPDATE outbox SET status='signed',raw_tx=?,tx_hash=?,payload=? WHERE id=?",
                                (raw.hex(), tx_hash, json.dumps(payload), row["id"]))
            health(self.db, "operator", True, "Signed transaction queued for broadcast")
        except fees.FeeBlocked as exc:
            health(self.db, "operator", False, str(exc))
        except Exception as exc:
            # No secret, raw RPC error, or signed bytes in logs/API.
            health(self.db, "operator", False, f"Action waiting: {type(exc).__name__}")
            log(self.db, "operator", "action_waiting", "warning", action=row["kind"], error=type(exc).__name__)
