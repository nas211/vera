#!/usr/bin/env python3
"""Run the public Vela source locally; no production configuration or keys needed."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from autodefi.api import create_app
from autodefi.config import Config
from autodefi.trading import ProtocolModel


def judge_app(port=8802, *, offline=False, with_ai=False, db_path=None, feed=None):
    """Use the actual API/runtime. Offline is for inspection, not fabricated fills."""
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a loopback port between 1024 and 65535')
    connected = ROOT / 'site/vela-dashboard/dist-testnet'
    preview = ROOT / 'site/vela-dashboard/dist'
    for path in (connected/'vela.html', preview/'preview.html', ROOT/'site/products-built/paper.html'):
        if not path.is_file():
            raise ValueError('Missing frontend build. Run: bash tools/setup_judge_demo.sh')
    config = Config(origin=f'http://127.0.0.1:{port}',
                    db_path=db_path or ROOT/'.runtime/lighter-showcase/judge.sqlite3',
                    network_enabled=not offline)
    # No environment file is sourced. Native writes, factory and attestor stay off.
    # Reading model credentials requires an explicit --with-ai opt-in.
    model = ProtocolModel() if with_ai else ProtocolModel(model_values={})
    backend = create_app(config=config, feed=feed, model=model)
    app = FastAPI(lifespan=backend.router.lifespan_context)
    app.state.backend = backend

    @app.get('/')
    def home():
        return HTMLResponse('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Vela local review</title>
<body style="background:#101211;color:#e9e6db;font:18px system-ui;max-width:760px;margin:8vh auto;padding:24px">
<h1>Vela · local review</h1><p>This checkout runs its own API, SQLite ledger and paper execution engine.</p>
<ul><li><a style="color:#dfc18b" href="/paper">Paper trading desk</a>: no wallet or API key needed for manual paper orders. Uses public market data; no exchange orders.</li>
<li><a style="color:#dfc18b" href="/app/">Connected dashboard</a>: connects to this local backend. Wallet sign-in only; native vault writes are disabled.</li>
<li><a style="color:#dfc18b" href="/preview/">Noah dashboard preview</a>: clearly labeled sample data, independent of the backend ledger.</li>
<li><a style="color:#dfc18b" href="/api/health">Backend health</a></li></ul>
<p>AI requires your own optional model configuration. Offline mode does not invent market data or AI decisions.</p>
<p>For the deployed DogeOS testnet integration, visit <a style="color:#dfc18b" href="https://defivela.xyz/app/">defivela.xyz/app</a>.</p></body></html>''')

    @app.get('/app')
    def app_redirect():
        return RedirectResponse('/app/')

    @app.get('/app/')
    def dashboard():
        return FileResponse(connected/'vela.html')

    @app.get('/preview')
    def preview_redirect():
        return RedirectResponse('/preview/')

    @app.get('/preview/')
    def preview_page():
        return FileResponse(preview/'preview.html')

    app.mount('/app', StaticFiles(directory=connected), name='dashboard')
    app.mount('/preview', StaticFiles(directory=preview), name='fixture-preview')
    app.mount('/', backend)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8802)
    parser.add_argument('--offline', action='store_true', help='Disable public market/network reads; preview and API inspection only')
    parser.add_argument('--with-ai', action='store_true', help='Use your own VELA_MODEL settings; model calls may incur provider charges')
    args = parser.parse_args()
    import uvicorn
    app = judge_app(args.port, offline=args.offline, with_ai=args.with_ai)
    print(f'Open http://127.0.0.1:{args.port}/ — PAPER only; native writes disabled', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1, proxy_headers=False)
