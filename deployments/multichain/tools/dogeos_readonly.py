"""Reversible read-only DogeOS gateway over the unchanged seven-network app.

No database, signer, model credential or execution worker is opened here.
The existing application owns authentication, demo accounting and trading.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
import sys
import time

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from autodefi.dogeos_readonly import DogeOSReadOnly
from autodefi.http_security import RequestLimits

ORIGIN = 'https://defivela.xyz'
UPSTREAM = 'http://127.0.0.1:8804'
BLOCKED = frozenset({'/api/vault/sync', '/api/borrow', '/api/borrow/cancel',
                     '/api/certificate', '/api/liquidate', '/api/settle'})
HOP_HEADERS = frozenset({'host', 'connection', 'upgrade', 'keep-alive',
                         'proxy-authenticate', 'proxy-authorization', 'te',
                         'trailer', 'transfer-encoding', 'content-length', 'content-encoding'})


class NoSharedCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


def create_app(reader=None, upstream=UPSTREAM, transport=None):
    reader = reader or DogeOSReadOnly()
    cache, lock = {}, asyncio.Lock()
    client = httpx.AsyncClient(base_url=upstream, timeout=90, transport=transport,
                               follow_redirects=False, headers={'accept-encoding': 'identity'},
                               cookies=CookieJar(policy=NoSharedCookies()))

    async def observation(wallet=None, verify=False):
        key = wallet or 'deployment'
        async with lock:
            cached = cache.get(key)
            if not verify and cached and time.time() - cached['observedAt'] < 15:
                return cached
            result = await asyncio.to_thread(reader.verify_deployment if verify else reader.snapshot,
                                             *(() if verify else (wallet,)))
            if len(cache) >= 64:
                cache.pop(next(iter(cache)))
            cache[key] = result
            return result

    @asynccontextmanager
    async def lifespan(app):
        await observation(verify=True)
        try:
            yield
        finally:
            await client.aclose()
            reader.client.close()

    app = FastAPI(title='VELA DogeOS read-only integration', lifespan=lifespan)
    app.add_middleware(RequestLimits)
    app.state.reader = reader

    @app.middleware('http')
    async def guard(request, call_next):
        selected = request.headers.get('x-vella-network', 'dogeos')
        path = request.url.path.rstrip('/') or '/'
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('origin') != ORIGIN or request.headers.get('x-adp-client') != 'web':
                return JSONResponse({'error': 'Same-origin request required'}, status_code=403)
            length = request.headers.get('content-length', '0')
            if len(length) > 16 or not length.isdigit() or int(length) > 16384:
                return JSONResponse({'error': 'Request too large'}, status_code=413)
            if selected == 'dogeos' and (path in BLOCKED or path.startswith('/api/simulated-vault/')):
                return JSONResponse({'error': 'DogeOS contracts are read-only. Use demo trading capital; customer contract transactions are disabled.'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store' if path.startswith('/api') else 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    async def contract_response(wallet=None):
        try:
            return JSONResponse(await observation(wallet))
        except Exception:
            return JSONResponse({'error': 'DogeOS RPC verification unavailable. Contract data is not current.',
                                 'status': 'UNAVAILABLE', 'readOnly': True,
                                 'customerTransactionsEnabled': False}, status_code=503)

    @app.get('/api/dogeos/status')
    async def status():
        return await contract_response()

    @app.get('/api/dogeos/demonstration')
    async def demonstration():
        return JSONResponse(json.loads((ROOT / 'config/dogeos-paper-demonstration.json').read_text()))

    @app.get('/api/dogeos/account')
    async def account(request: Request):
        if request.headers.get('x-vella-network', 'dogeos') != 'dogeos':
            return JSONResponse({'error': 'Select DogeOS to view its wallet state'}, status_code=403)
        session = await client.get('/api/session', headers={k:v for k,v in request.headers.items() if k not in HOP_HEADERS})
        if session.status_code != 200:
            return Response(session.content, status_code=session.status_code, media_type='application/json')
        data = session.json()
        if data.get('network') != 'dogeos' or data.get('authenticated') is not True:
            return JSONResponse({'error': 'DogeOS wallet session required'}, status_code=403)
        return await contract_response(data['address'])

    @app.get('/integrations/{filename}')
    async def integration_asset(filename: str):
        if filename not in ('dogeos-readonly.js', 'dogeos-readonly.css'):
            return Response(status_code=404)
        return FileResponse(ROOT / 'site/integrations' / filename)

    @app.get('/vault')
    async def vault_page():
        html = (ROOT / 'site/candidate/index.html').read_text()
        html = html.replace('</head>', '<link rel="stylesheet" href="/integrations/dogeos-readonly.css"></head>')
        html = html.replace('</body>', '<script type="module" src="/integrations/dogeos-readonly.js"></script></body>')
        return HTMLResponse(html)

    @app.websocket('/ws/markets')
    @app.websocket('/api/paper/stream')
    async def markets(ws: WebSocket):
        if ws.headers.get('origin') != ORIGIN:
            await ws.close(code=1008)
            return
        try:
            async with websockets.connect(upstream.replace('http://', 'ws://') + ws.url.path,
                                          origin=ORIGIN, open_timeout=10) as connection:
                await ws.accept()
                async for message in connection:
                    if isinstance(message, str):
                        await ws.send_text(message)
                    else:
                        await ws.send_bytes(message)
        except Exception:
            try:
                await ws.close(code=1013)
            except Exception:
                pass

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def proxy(path: str, request: Request):
        headers = {k:v for k,v in request.headers.items() if k not in HOP_HEADERS and k != 'accept-encoding'}
        body = await request.body()
        if len(body) > 16384:
            return JSONResponse({'error': 'Request too large'}, status_code=413)
        # Fixed authority, no redirects or user-controlled upstream URL.
        target = httpx.URL(upstream).copy_with(path='/' + path, query=request.url.query.encode())
        try:
            response = await client.request(request.method, target, headers=headers, content=body)
        except httpx.HTTPError:
            return JSONResponse({'error': 'Existing application unavailable'}, status_code=503)
        if path == 'api/config' and response.status_code == 200:
            data = response.json()
            data['dogeosReadOnly'] = dict(chainId=6281971, factory=reader.factory,
                referenceVault=reader.reference, customerTransactionsEnabled=False,
                readOnly=True, simulationFunding='existing demo trading ledger')
            for chain in data.get('chains', []) + [data.get('chain', {})]:
                if chain.get('id') == 'dogeos':
                    chain['status'] = 'Featured · DogeOS Testnet · Demo'
                    chain['capabilities'].update(testnetVault=False, tokenTransfers=False,
                        demoLending=False, contractReads=True, customerTransactions=False)
            # Keep the legacy writer disabled. The actual factory lives in the
            # explicitly read-only descriptor, never in the old transaction path.
            data['factory'] = None
            data['attestorConfigured'] = False
            return JSONResponse(data)
        if path == 'api/health' and response.status_code == 200:
            data = response.json()
            try:
                onchain = await observation()
                data['dogeos'] = dict(status=onchain['status'], chainId=onchain['chainId'],
                    block=onchain['block'], readOnly=True, customerTransactionsEnabled=False)
            except Exception:
                data['dogeos'] = dict(status='UNAVAILABLE', readOnly=True)
                return JSONResponse(data, status_code=503)
            return JSONResponse(data)
        result = Response(response.content, status_code=response.status_code)
        result.raw_headers = [(k,v) for k,v in response.headers.raw if k.decode().lower() not in HOP_HEADERS]
        return result

    return app
