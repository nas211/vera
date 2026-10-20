"""Deploy only the tested DogeOS showcase. No production chain or account endpoints."""
import argparse
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from web3 import Web3
from .settings import ROOT, CHAIN_FILE
from . import fees


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pool-doge", type=Decimal, default=Decimal("0"))
    p.add_argument("--execute", action="store_true", help="Broadcast only after reviewing the dry-run fee estimate")
    p.add_argument("--max-total-fee-doge", type=Decimal, default=Decimal("0.25"))
    p.add_argument("--from-address", default="0xF8b7120F466f39063398fdc62F5f8cBCe37Ad1dB")
    p.add_argument("--output", default=str(ROOT / ".runtime/vela-v2-deployment.json"))
    args = p.parse_args()
    if not args.pool_doge.is_finite() or not 0 <= args.pool_doge <= 20:
        raise SystemExit("Pool funding must be between 0 and 20 test DOGE")
    chain = json.loads(CHAIN_FILE.read_text())
    output=Path(args.output).resolve()
    if not output.is_relative_to(ROOT) or output==CHAIN_FILE or output.exists():
        raise SystemExit("Choose a new candidate config inside Vera; active config is never overwritten")
    if not args.max_total_fee_doge.is_finite() or args.max_total_fee_doge<=0:
        raise SystemExit("Set a positive total network fee budget")
    rpc = chain["rpcUrls"][0]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != 6281971:
        raise SystemExit("Not DogeOS Chikyū Testnet")
    key = os.environ.get("VELA_OPERATOR_PRIVATE_KEY")
    if args.execute and not key:
        raise SystemExit("Set VELA_OPERATOR_PRIVATE_KEY in the deployment process environment")
    account = w3.eth.account.from_key(key) if key else None
    sender=account.address if account else Web3.to_checksum_address(args.from_address)
    # Tests are a mandatory deploy gate. Do not put the key in command arguments.
    forge = str(ROOT / ".tools/foundry/forge") if (ROOT / ".tools/foundry/forge").exists() else "forge"
    subprocess.run([forge, "test", "-q"], cwd=ROOT / "contracts", check=True)
    artifact = json.loads((ROOT / "contracts/out/VelaVaultV2.sol/VelaVaultV2.json").read_text())
    (ROOT / "config/VelaVaultV2.abi.json").write_text(json.dumps(artifact["abi"], indent=2) + "\n")
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    used_fees=0
    def send(call, value=0):
        nonlocal used_fees
        tx = call.build_transaction({"from": sender, "chainId": 6281971,
            "nonce": w3.eth.get_transaction_count(sender, "pending"),
            "gasPrice": w3.eth.gas_price, "value": value})
        tx["gas"] = int(tx["gas"] * 1.2)
        quote=fees.estimate(w3,tx)
        balance=w3.eth.get_balance(sender)
        plan=dict(action="broadcast" if args.execute else "dry_run",sender=sender,
                  estimatedFullFeeDoge=str(Decimal(quote["total"])/10**18),
                  feeAllowanceDoge=str(Decimal(quote["allowance"])/10**18),
                  walletBalanceDoge=str(Decimal(balance)/10**18),poolFundingDoge=str(args.pool_doge),
                  feeBudgetDoge=str(args.max_total_fee_doge),activeConfigUnchanged=True)
        print(json.dumps(plan),flush=True)
        if not args.execute:
            return None
        if used_fees+quote["allowance"]>int(args.max_total_fee_doge*10**18):
            raise RuntimeError("Full network fee exceeds deployment budget; no transaction submitted")
        if balance < value + quote["allowance"] + 10**18:
            raise RuntimeError("Insufficient test DOGE including full fees and 1 DOGE reserve; no transaction submitted")
        signed = account.sign_transaction(tx)
        pending=output.with_suffix(".pending.json")
        if pending.exists():
            raise RuntimeError("Previous submission must be reconciled before deployment retry")
        pending.parent.mkdir(parents=True,exist_ok=True)
        pending.write_text(json.dumps(dict(tx=Web3.to_hex(signed.hash),nonce=tx["nonce"],sender=sender)))
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        print("Submitted " + Web3.to_hex(h), flush=True)
        receipt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        used_fees+=fees.actual_fee(receipt,6281971)
        if receipt["status"] != 1:
            raise RuntimeError("Testnet transaction reverted")
        pending.unlink()
        return receipt
    receipt = send(factory.constructor(sender, sender))
    if receipt is None:
        return
    vault = w3.eth.contract(address=receipt["contractAddress"], abi=artifact["abi"])
    deployment = dict(vault=vault.address, wdoge=vault.functions.wdoge().call(),
        tusdc=vault.functions.tusdc().call(), operator=sender, owner=sender,version=2,
        blockNumber=receipt["blockNumber"], deploymentTx=Web3.to_hex(receipt["transactionHash"]))
    for name in ("vault", "wdoge", "tusdc"):
        if not w3.eth.get_code(deployment[name]):
            raise RuntimeError("Deployment has no bytecode: " + name)
    chain["previousDeployment"] = chain.get("deployment")
    chain["deployment"] = deployment
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(chain, indent=2) + "\n")
    # Preserve the successfully deployed addresses even if funding later fails.
    if args.pool_doge:
        pool = send(vault.functions.fundPool(), int(args.pool_doge * 10**18))
        deployment["poolFundingTx"] = Web3.to_hex(pool["transactionHash"])
        deployment["poolDoge"] = str(args.pool_doge)
        output.write_text(json.dumps(chain, indent=2) + "\n")
    print(json.dumps(deployment, indent=2))


if __name__ == "__main__":
    main()
