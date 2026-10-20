"""Read the disposable testnet operator key without echo or disk persistence."""
import getpass
import os
import runpy
import subprocess
import sys
from pathlib import Path
from eth_account import Account

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
action = sys.argv[1] if len(sys.argv) > 1 else "verify"
if action not in ("verify", "deploy", "e2e", "worker", "worker-service"):
    raise SystemExit("Allowed actions: verify, deploy, e2e, worker, worker-service")
key = os.environ.get("VELA_OPERATOR_PRIVATE_KEY") or getpass.getpass("Vela testnet private key (hidden): ")
try:
    account = Account.from_key(key)
except Exception:
    raise SystemExit("Invalid key; value was not logged") from None
expected = os.getenv("VELA_EXPECTED_OPERATOR", "0xF8b7120F466f39063398fdc62F5f8cBCe37Ad1dB")
if account.address.lower() != expected.lower():
    raise SystemExit("Signer does not match the expected public operator address")
os.environ["VELA_OPERATOR_PRIVATE_KEY"] = key
print("Verified testnet signer: " + account.address, flush=True)
if action == "worker-service":
    # --setenv=NAME imports the inherited value without exposing it in argv.
    options = ["--setenv=" + name for name in (
        "VELA_MODEL", "VELA_MODEL_KEY", "VELA_MODEL_ENDPOINT", "VELA_PYTH_API_KEY",
        "VELA_DB_PATH", "VELA_RPC_URL", "VELA_CONFIRMATIONS", "VELA_MAX_LEVERAGE",
        "VELA_SIZE_FRACTION", "VELA_SLIPPAGE", "VELA_LOSS_BUDGET") if name in os.environ]
    subprocess.run(["systemd-run", "--unit=vela-testnet-worker", "--collect",
        "--property=WorkingDirectory=" + str(root), "--property=Restart=on-failure",
        "--property=RestartSec=5", "--property=NoNewPrivileges=yes",
        "--setenv=VELA_OPERATOR_PRIVATE_KEY", "--setenv=PYTHONPATH=" + str(root / "backend"), *options,
        str(root / ".venv/bin/python"), "-m", "vela.worker"], check=True)
elif action != "verify":
    sys.argv = ["vela." + action, *sys.argv[2:]]
    runpy.run_module("vela." + action, run_name="__main__")
