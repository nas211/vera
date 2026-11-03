"""Bound actual ASGI request bytes, including chunked requests without a length."""
import json
import time
from collections import OrderedDict, deque


class RequestLimits:
    def __init__(self, app, maximum=16384, auth_limit=120, window=60, clock=time.monotonic):
        self.app, self.maximum = app, maximum
        self.auth_limit, self.window, self.clock = auth_limit, window, clock
        self.clients = OrderedDict()

    async def reject(self, send, status, message):
        headers = [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                   (b'x-content-type-options', b'nosniff')]
        if status == 429:
            headers.append((b'retry-after', str(self.window).encode()))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': json.dumps({'error': message}).encode()})

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        mutation = scope.get('method') not in ('GET', 'HEAD', 'OPTIONS')
        lengths = [value for name, value in scope.get('headers', []) if name.lower() == b'content-length']
        if len(lengths) > 1 or lengths and (len(lengths[0]) > 16 or not lengths[0].isdigit() or int(lengths[0]) > self.maximum):
            return await self.reject(send, 413, 'Request too large')
        # Do not trust client-supplied forwarding headers. At the gateway this
        # is the actual peer supplied by its configured trusted reverse proxy.
        if mutation and (scope.get('path', '').startswith('/api/auth/') or scope.get('path') == '/api/paper/session'):
            now = self.clock()
            peer = (scope.get('client') or ('unknown',))[0]
            while self.clients and now-next(iter(self.clients.values()))[-1] >= self.window:
                self.clients.popitem(last=False)
            if peer not in self.clients and len(self.clients) >= 4096:
                return await self.reject(send, 429, 'Authentication capacity reached; retry later')
            history = self.clients.setdefault(peer, deque())
            while history and now-history[0] >= self.window:
                history.popleft()
            if len(history) >= self.auth_limit:
                return await self.reject(send, 429, 'Too many authentication attempts; retry later')
            history.append(now)
            self.clients.move_to_end(peer)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            data = message.get('body', b'')
            size += len(data)
            if size > self.maximum:
                return await self.reject(send, 413, 'Request too large')
            chunks.append(data)
            if not message.get('more_body', False):
                break
        body, delivered = b''.join(chunks), False
        if mutation and scope.get('path', '').startswith('/api/') and body:
            try:
                value = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                return await self.reject(send, 400, 'A valid JSON object is required')

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
