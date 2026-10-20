"""Public Bybit GET-only data relay. No auth, order, account or arbitrary URL routes."""
import http.server
import json
import os
import re
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOCKET = '/run/why-github-market/market.sock'
PATHS = {'/v5/market/' + x for x in (
    'time', 'instruments-info', 'tickers', 'orderbook', 'kline', 'mark-price-kline',
    'index-price-kline', 'premium-index-price-kline', 'funding/history', 'risk-limit',
    'recent-trade', 'open-interest')}
PARAMS = {'category', 'symbol', 'limit', 'cursor', 'interval', 'start', 'end',
          'startTime', 'endTime', 'baseCoin', 'settleCoin', 'status', 'period'}

def valid_path(target):
    u = urllib.parse.urlsplit(target)
    if u.scheme or u.netloc or u.fragment or u.path not in PATHS or len(target) > 1500:
        return False
    for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True):
        pattern = r'[A-Za-z0-9_.,:=+/% &-]{0,250}' if k == 'cursor' else r'[A-Za-z0-9_.,:=+/% -]{0,250}'
        if k not in PARAMS or not re.fullmatch(pattern, v):
            return False
    return True

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect denied')

OPENER = urllib.request.build_opener(NoRedirect)
CACHE = {}
LOCK = threading.Lock()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not valid_path(self.path):
            self.send_error(403, 'public-market-path-only'); return
        ttl = (0 if self.path == '/v5/market/time' else
               300 if 'instruments-info' in self.path or 'risk-limit' in self.path else 2)
        with LOCK:
            cached = CACHE.get(self.path)
        if cached and time.monotonic() - cached[0] < ttl:
            code, body = cached[1:]
        else:
            try:
                request = urllib.request.Request('https://api.bybit.com' + self.path,
                                                 headers={'User-Agent': 'WHY-public-shadow-data/1'})
                with OPENER.open(request, timeout=25) as response:
                    code, body = response.status, response.read(16 * 1024 * 1024)
                json.loads(body)
                with LOCK:
                    if len(CACHE) > 4096: CACHE.clear()
                    CACHE[self.path] = (time.monotonic(), code, body)
            except urllib.error.HTTPError as error:
                code, body = error.code, json.dumps({'relay_error': 'upstream_http_error'}).encode()
            except Exception as error:
                code, body = 502, json.dumps({'relay_error': type(error).__name__}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and str(args[1]) != '200':
            print(fmt % args, flush=True)

class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 128

if __name__ == '__main__':
    if os.path.exists(SOCKET): os.unlink(SOCKET)
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o666)
        server.serve_forever()
