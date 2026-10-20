"""DogeOS execution + data/finality fees. Never substitute execution gas alone.

https://docs.dogeos.com/en/developers/transaction-fees-on-dogeos
Budgets are pre-broadcast estimate gates, not protocol-enforced L1 fee caps.
"""
import time
from decimal import Decimal as D
import rlp
from web3 import Web3

ORACLE = "0x5300000000000000000000000000000000000002"
ORACLE_ABI = [{"type":"function", "name":"getL1Fee", "stateMutability":"view",
               "inputs":[{"name":"data", "type":"bytes"}],
               "outputs":[{"name":"fee", "type":"uint256"}]}]


class FeeBlocked(ValueError):
    pass


def number(x):
    return int(x, 16) if isinstance(x, str) and x.startswith("0x") else int(x)


def encoded_for_estimate(tx):
    """Legacy RLP with nonzero signature placeholders for a conservative quote."""
    def integer(n):
        n = number(n)
        return n.to_bytes((n.bit_length() + 7) // 8, "big")
    def raw(x):
        return bytes.fromhex(x.removeprefix("0x")) if isinstance(x, str) else bytes(x)
    return rlp.encode([integer(tx.get("nonce", 0)), integer(tx["gasPrice"]),
        integer(tx["gas"]), raw(tx.get("to") or b""), integer(tx.get("value", 0)),
        raw(tx.get("data", b"")), integer(number(tx["chainId"]) * 2 + 36),
        b"\x7f" * 32, b"\x7f" * 32])


def estimate(w3, tx, signed_raw=None):
    chain_id = number(tx["chainId"])
    execution = number(tx["gas"]) * number(tx["gasPrice"])
    if chain_id == 31337:
        data_fee = 0  # Settings permits this chain only on loopback.
    elif chain_id == 6281971:
        oracle = w3.eth.contract(address=ORACLE, abi=ORACLE_ABI)
        try:
            data_fee = number(oracle.functions.getL1Fee(
                bytes(signed_raw) if signed_raw is not None else encoded_for_estimate(tx)).call())
        except Exception:
            raise FeeBlocked("Full DogeOS fee unavailable; transaction not submitted") from None
        if data_fee <= 0:
            raise FeeBlocked("Invalid DogeOS data/finality fee; transaction not submitted")
    else:
        raise FeeBlocked("Unsupported chain")
    total = execution + data_fee
    return dict(execution=execution, data=data_fee, total=total,
                allowance=(total * 150 + 99) // 100, at=time.time())


def actual_fee(receipt, chain_id):
    if chain_id == 6281971 and "l1Fee" not in receipt:
        raise FeeBlocked("Receipt omitted the data/finality fee; operator paused")
    return number(receipt["gasUsed"]) * number(receipt["effectiveGasPrice"]) + number(receipt.get("l1Fee", 0))


def spent(db, now=None, exclude=None):
    now = time.time() if now is None else now
    # Pending reservations do not expire. Actual costs use a rolling 24h window.
    return sum(number(r["actual_wei"] or r["reserved_wei"]) for r in db.execute(
        "SELECT * FROM network_fees WHERE actual_wei IS NULL OR confirmed>?", (now - 86400,))
        if r["tx_hash"] != exclude)


def check(db, settings, quote, balance, value=0, exclude=None, require_enabled=True):
    if require_enabled and not settings.chain_writes:
        raise FeeBlocked("On-chain transactions paused by Vela fee controls")
    amount = quote["allowance"]
    if amount > int(settings.max_tx_fee * 10**18):
        raise FeeBlocked("Estimated total network fee exceeds the per-transaction budget")
    if spent(db, exclude=exclude) + amount > int(settings.daily_fee_budget * 10**18):
        raise FeeBlocked("Rolling 24-hour network fee budget reached")
    if balance < value + amount + int(settings.gas_reserve * 10**18):
        raise FeeBlocked("Operator DOGE reserve would be breached")


def reserve(db, tx_hash, quote, kind):
    db.execute("INSERT INTO network_fees VALUES (?,?,NULL,?,NULL,?) "
               "ON CONFLICT(tx_hash) DO UPDATE SET reserved_wei=excluded.reserved_wei",
               (tx_hash, str(quote["allowance"]), time.time(), kind))


def record(db, receipt, chain_id, kind="reconciled"):
    tx_hash = Web3.to_hex(receipt["transactionHash"]) if not isinstance(receipt["transactionHash"], str) else receipt["transactionHash"]
    cost = actual_fee(receipt, chain_id)
    db.execute("INSERT INTO network_fees VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(tx_hash) DO UPDATE SET actual_wei=excluded.actual_wei, "
               "confirmed=coalesce(network_fees.confirmed,excluded.confirmed)",
               (tx_hash, str(cost), str(cost), time.time(), time.time(), kind))
    return cost
