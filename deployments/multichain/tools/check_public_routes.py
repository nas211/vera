"""Read-only checks for the public Vela pages, aliases and API routing boundary."""
from concurrent.futures import ThreadPoolExecutor
import json
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'https://defivela.xyz'
PAGES = ('/', '/overview', '/vault', '/borrow', '/trading', '/repay', '/how-it-works')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def cases():
    result = [(p, 200, None, 'text/html') for p in PAGES]
    result += [(p+'/', 308, p, None) for p in PAGES if p != '/']
    result += [(p, 308, target, None) for p, target in (
        ('/trade', '/trading'), ('/trade/', '/trading'),
        ('/trade?source=route-health', '/trading?source=route-health'),
        ('/trade/?source=route-health', '/trading?source=route-health'))]
    result += [(p, 401, None, 'application/json') for p in (
        '/api/session', '/api/account', '/api/history/transactions')]
    result += [(p, 404, None, 'application/json') for p in (
        '/route-health-not-found', '/api/route-health-not-found')]
    return result


def probe(case):
    path, expected, target, media_type = case
    try:
        opener = urllib.request.build_opener(NoRedirect)
        try:
            response = opener.open(ORIGIN+path, timeout=6)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(262144)
            status = response.code
            location = response.headers.get('Location', '')
            valid = status == expected
            if target:
                valid = valid and urllib.parse.urljoin(ORIGIN+path, location) == ORIGIN+target
            if media_type:
                valid = valid and response.headers.get('Content-Type', '').startswith(media_type)
            if expected == 200:
                valid = valid and b'<script' in body and b'/ui-assets/' in body
            return dict(path=path, status=status, passed=bool(valid))
    except Exception as error:
        # No headers, response bodies, cookies, wallet information or secrets.
        return dict(path=path, passed=False, errorType=type(error).__name__)


def check():
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(probe, cases()))
    failed = [row for row in results if not row['passed']]
    return dict(healthy=not failed, checked=len(results), failures=failed, readOnly=True)


if __name__ == '__main__':
    result = check()
    print(json.dumps(result))
    raise SystemExit(0 if result['healthy'] else 1)
