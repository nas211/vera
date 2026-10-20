"""Deploy only the tested DogeOS showcase. No production chain or account endpoints."""
import argparse
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from web3 import Web3
from .settings import ROOT, CHAIN_FILE


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pool-doge", type=Decimal, default=Decimal("10"))
    args = p.parse_args()
    if not args.pool_doge.is_finite() or not 0 <= args.pool_doge <= 20:
        raise SystemExit("Pool funding must be between 0 and 20 test DOGE")
    chain = json.loads(CHAIN_FILE.read_text())
    if chain.get("deployment"):
        raise SystemExit("Deployment already recorded; refusing accidental redeployment")
    rpc = chain["rpcUrls"][0]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != 6281971:
        raise SystemExit("Not DogeOS Chikyū Testnet")
    key = os.environ.get("VELA_OPERATOR_PRIVATE_KEY")
    if not key:
        raise SystemExit("Set VELA_OPERATOR_PRIVATE_KEY in the deployment process environment")
    account = w3.eth.account.from_key(key)
    # Tests are a mandatory deploy gate. Do not put the key in command arguments.
    forge = str(ROOT / ".tools/foundry/forge") if (ROOT / ".tools/foundry/forge").exists() else "forge"
    subprocess.run([forge, "test", "-q"], cwd=ROOT / "contracts", check=True)
    artifact = json.loads((ROOT / "contracts/out/VelaVault.sol/VelaVault.json").read_text())
    (ROOT / "config/VelaVault.abi.json").write_text(json.dumps(artifact["abi"], indent=2) + "\n")
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    def send(call, value=0):
        tx = call.build_transaction({"from": account.address, "chainId": 6281971,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "gasPrice": w3.eth.gas_price, "value": value})
        tx["gas"] = int(tx["gas"] * 1.2)
        if w3.eth.get_balance(account.address) < value + tx["gas"] * tx["gasPrice"]:
            raise RuntimeError("Insufficient test DOGE for transaction")
        signed = account.sign_transaction(tx)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        print("Submitted " + Web3.to_hex(h), flush=True)
        receipt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        if receipt["status"] != 1:
            raise RuntimeError("Testnet transaction reverted")
        return receipt
    receipt = send(factory.constructor(account.address, account.address))
    vault = w3.eth.contract(address=receipt["contractAddress"], abi=artifact["abi"])
    deployment = dict(vault=vault.address, wdoge=vault.functions.wdoge().call(),
        tusdc=vault.functions.tusdc().call(), operator=account.address, owner=account.address,
        blockNumber=receipt["blockNumber"], deploymentTx=Web3.to_hex(receipt["transactionHash"]))
    for name in ("vault", "wdoge", "tusdc"):
        if not w3.eth.get_code(deployment[name]):
            raise RuntimeError("Deployment has no bytecode: " + name)
    chain["deployment"] = deployment
    CHAIN_FILE.write_text(json.dumps(chain, indent=2) + "\n")
    # Preserve the successfully deployed addresses even if funding later fails.
    if args.pool_doge:
        pool = send(vault.functions.fundPool(), int(args.pool_doge * 10**18))
        deployment["poolFundingTx"] = Web3.to_hex(pool["transactionHash"])
        deployment["poolDoge"] = str(args.pool_doge)
        CHAIN_FILE.write_text(json.dumps(chain, indent=2) + "\n")
    print(json.dumps(deployment, indent=2))


if __name__ == "__main__":
    main()
