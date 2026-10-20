"""Verify a dedicated Vela API key through hidden input; optionally restart Vela.

No secret is written to the checkout, shell arguments or shell history.
Runtime secrets must be supplied again after a host reboot.
"""
import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path
from eth_account import Account

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from vela.agents import VelaModel
from vela.reuse import model_module

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=("verify", "worker-service"), nargs="?", default="verify")
parser.add_argument("--model", default="gpt-5.6-luna")
args = parser.parse_args()
if not args.model or not all(c.isalnum() or c in "-._" for c in args.model):
    raise SystemExit("Invalid model identifier")
os.environ["VELA_MODEL"] = args.model
os.environ["VELA_MODEL_ENDPOINT"] = "https://api.openai.com/v1/responses"
os.environ["VELA_MODEL_KEY"] = getpass.getpass("Dedicated Vela model API key (hidden): ").strip()
model = VelaModel()
try:
    answer = model.ask("review", dict(markets={}, purpose="Connection test only; no market evidence. Delay or veto."),
                       model_module.ASSESSMENT)
    if answer.get("verdict") not in ("delay", "veto") or not isinstance(answer.get("reason"),str):
        raise ValueError("AI_VALIDATION_FAILED")
except Exception as exc:
    code = str(exc) if str(exc).startswith("AI_") else type(exc).__name__
    raise SystemExit("Model connection not verified: " + code) from None
print("Vela model response verified: " + args.model, flush=True)
if args.action == "worker-service":
    key = getpass.getpass("Dedicated Vela operator private key (hidden): ").strip()
    try:
        address = Account.from_key(key).address
    except Exception:
        raise SystemExit("Invalid operator key; value was not logged") from None
    expected = os.getenv("VELA_EXPECTED_OPERATOR", "0xF8b7120F466f39063398fdc62F5f8cBCe37Ad1dB")
    if address.lower() != expected.lower():
        raise SystemExit("Operator key does not match the dedicated Vela address")
    os.environ["VELA_OPERATOR_PRIVATE_KEY"] = key
    # Model configuration must never implicitly enable chain spending.
    os.environ["VELA_CHAIN_WRITES"] = "false"
    subprocess.run(["systemctl", "stop", "vela-testnet-worker.service"], check=True)
    subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "tools/with_operator.py"),
                    "worker-service"], check=True)
    print("Vela worker restarted with model credentials; chain spending remains disabled.", flush=True)
