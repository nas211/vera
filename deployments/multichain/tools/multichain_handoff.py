#!/usr/bin/env python3
"""Read audited configuration and prepare the preserved candidate; never restart services.

The ASGI factory is for a separately supervised candidate. --check does not open
a database, start background tasks, bind sockets, or make model/RPC requests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shlex
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from autodefi.config import Config
from tools.native_testnet_config import configured as native_testnet_config
from autodefi.trading import ProtocolModel
from vela.lighter.feed import LighterFeed, REST, WS

MODEL_LOADER = Path('/root/Vera/tools/run_lighter_paper.py')
MODEL_FILE = Path('/root/Vera/.runtime/lighter-showcase/model.env')
WALLET_FILE = Path('/root/VeraProtocol/.runtime/preview.env')
RELEASE = ROOT / '.runtime/releases/candidate-f9b20d84ab40/release.json'
ORIGIN = 'https://defivela.xyz'
DATABASE = ROOT / '.runtime/lighter-showcase/multichain-candidate.sqlite3'


def private_file(path):
    """Reject permissive or redirected secret inputs without changing permissions."""
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384
                or path.resolve() != path):
            raise ValueError()
    except (OSError, ValueError):
        raise ValueError('Configuration requires an owned, non-symlink mode-0600 file') from None


def load_existing_model():
    private_file(MODEL_FILE)
    try:
        # Reuse the audited loader itself. run_path does not write its bytecode
        # into the existing deployment and does not execute its CLI main().
        loader = runpy.run_path(str(MODEL_LOADER))['load_model_file']
        values = loader(MODEL_FILE)
        model = ProtocolModel(model_values=values)
        if not model.ready:
            raise ValueError()
        return model
    except Exception:
        # Parsing exceptions must not disclose credential-bearing input.
        raise ValueError('Existing secure model configuration could not be loaded') from None


def load_existing_wallet():
    private_file(WALLET_FILE)
    wanted = {'ORIGIN', 'RPC', 'MYDOGE_CLIENT_ID', 'WALLETCONNECT_PROJECT_ID'}
    values = {}
    try:
        for line in WALLET_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, raw = line.removeprefix('export ').partition('=')
            key = key.strip()
            if not sep:
                raise ValueError()
            name = next((key[len(p):] for p in ('VERA_PROTOCOL_', 'ADP_') if key.startswith(p)), '')
            if name not in wanted:
                continue  # Never import old DBs, risk overrides or signing keys.
            parts = shlex.split(raw, comments=True)
            if len(parts) != 1 or not parts[0] or any(c.isspace() for c in parts[0]):
                raise ValueError()
            if name in values and values[name] != parts[0]:
                raise ValueError()
            values[name] = parts[0]
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Existing wallet configuration could not be read safely') from None
    if values.get('ORIGIN') != ORIGIN:
        raise ValueError('Existing wallet origin does not match the candidate public origin')
    chain = json.loads((ROOT / 'config/dogeos-testnet.json').read_text())
    if (chain['chainId'] != 6281971 or chain.get('testnet') is not True
            or values.get('RPC') != chain['rpcUrls'][0]):
        raise ValueError('Existing wallet RPC does not match the preserved DogeOS testnet')
    if not values.get('MYDOGE_CLIENT_ID'):
        raise ValueError('Existing MyDoge client configuration is empty')
    return values


def verify_candidate():
    release = json.loads(RELEASE.read_text())
    directory = ROOT / 'site/candidate'
    manifest_file = directory / 'build-manifest.json'
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if digest(manifest_file) != release['candidateManifestSha256']:
        raise ValueError('Preserved candidate manifest changed')
    manifest = json.loads(manifest_file.read_text())
    if digest(directory / 'index.html') != manifest['htmlSha256']:
        raise ValueError('Preserved candidate HTML changed')
    if set(manifest['outputs']) != set(manifest['sha256']):
        raise ValueError('Candidate asset manifest is incomplete')
    for name, expected in manifest['sha256'].items():
        path = (ROOT / 'site' / name).resolve()
        if not path.is_relative_to(directory / 'assets') or digest(path) != expected:
            raise ValueError('Preserved candidate asset changed')
    return len(manifest['outputs'])


def prepare():
    assets = verify_candidate()
    wallet = load_existing_wallet()
    model = load_existing_model()
    config = Config(origin=ORIGIN, rpc=wallet['RPC'], client_id=wallet['MYDOGE_CLIENT_ID'],
                    walletconnect_project_id=wallet.get('WALLETCONNECT_PROJECT_ID', ''),
                    db_path=DATABASE, multichain=True, network_enabled=True,
                    demo_staking=os.getenv('VERA_PROTOCOL_DEMO_STAKING_ENABLED','1')=='1')
    if config.db_path in {
        ROOT / '.runtime/lighter-showcase/multichain.sqlite3',
        Path('/root/Vera/.runtime/lighter-showcase/competition/paper.sqlite3'),
        Path('/root/VeraProtocol/.runtime/lighter-showcase/protocol.sqlite3'),
    }:
        raise ValueError('Candidate must not open an existing service ledger')
    config = native_testnet_config(config)
    report = dict(status='PASS', scope='Configuration only; no live connectivity or deployment claim',
                  origin=config.origin, modelConfigured=model.ready, modelSource=str(MODEL_FILE),
                  walletConfigured=bool(config.client_id), walletSource=str(WALLET_FILE),
                  walletOriginMatches=True, walletRegistrationVerified=False,
                  lighterImplementation='Existing public REST/WebSocket feed', lighterREST=REST, lighterWS=WS,
                  networkReadsEnabled=config.network_enabled, candidateAssetsVerified=assets,
                  candidateDatabase=str(config.db_path), execution='PAPER', lending='DEMO',
                  liveEnabled=False, realFunding=False, factoryConfigured=bool(config.factory), attestorConfigured=bool(config.attestor_key),
                  nativeWorkflowConfigured=config.native_actions_enabled, nativeRuntimeVerified=False,
                  readyForControlledActivation=False, deployed=False, liveProtectionVerified=False,
                  blockers=['Genuine browser/wallet and model/feed connectivity acceptance required',
                            'Candidate host service, TLS routing and rollback acceptance required',
                            'Canonical DogeOS contract, pool and attestor verification required'])
    return config, model, report


def create_app():
    """Uvicorn factory; all external credentials stay in model memory."""
    config, model, _ = prepare()
    from autodefi.api import create_app as application
    return application(config=config, feed=LighterFeed(), model=model, frontend='candidate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', required=True)
    parser.parse_args()
    try:
        _, _, report = prepare()
    except Exception:
        print(json.dumps(dict(status='FAIL', error='Secure candidate configuration check failed; no values disclosed',
                              deployed=False, liveProtectionVerified=False)))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
