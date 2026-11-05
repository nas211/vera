#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 or newer required"'
node -e 'const [a,b]=process.versions.node.split(".").map(Number);if(a<22||(a===22&&b<12))throw Error("Node 22.12 or newer required")'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix site/vela-dashboard ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund
npm --prefix site/vela-dashboard run build
npm --prefix site/vela-dashboard run build -- --mode testnet
printf '\nReady. Run: .venv/bin/python tools/run_judge_demo.py\nOpen: http://127.0.0.1:8802/\n'
